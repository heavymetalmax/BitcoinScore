"""Build ML training data: (metric_scores + phase_context, 90d_return_label) pairs.

Two types of features for each training date:
  1. Metric scores (what things look like right now) — 10 values
  2. Phase context (what KIND of moment this is) — 4 values:
       sp_v2       — distance-based cycle position vs confirmed tops/bottoms
       top_signal  — % confidence we're near historical top territory
       bot_signal  — % confidence we're near historical bottom territory
       tiz_days    — how many days we've been in the current risk zone

This separation allows ML to learn:
  "CB=0 + NUPL=47 in NEUTRAL phase" ≠ "CB=0 + NUPL=15 near BOTTOM territory"

Run: python3 tools/build_ml_training_data.py
Output: data/ml_training_data.json
"""
import sys, os, json, datetime, bisect
sys.path.insert(0, '.')

from tools.backtest import load_data, compute_at
from scraper.scoring_v2 import score_processor_v2, phase_signals, _METRIC_LOOKBACK

ROOT = os.path.join(os.path.dirname(__file__), '..')

METRIC_KEYS = [
    'nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'mayer_multiple',
    'asopr', 'cipherb_weekly', 'cipherb_daily', 'fear_greed', 'm2_yoy',
]
PHASE_KEYS  = ['sp_v2', 'top_signal', 'bot_signal', 'tiz_days', 'days_since_bottom']
FEATURE_KEYS = METRIC_KEYS + PHASE_KEYS

MIN_METRIC_FEATURES = 6
RETURN_HORIZON      = 90   # days
LOOKBACK_BUFFER     = 65   # pre-fetch extra days for prev_scores lookup


def _load_bottom_dates():
    path = os.path.join(ROOT, 'data', 'adaptive_weights.json')
    with open(path) as f:
        return sorted(json.load(f).get('bottom_dates', []))


def _days_since_bottom(date_str, bottom_dates):
    for bd in reversed(bottom_dates):
        if bd <= date_str:
            d0 = datetime.date.fromisoformat(bd)
            d1 = datetime.date.fromisoformat(date_str)
            return (d1 - d0).days
    return 0


def _load_prices():
    path = os.path.join(ROOT, 'data', 'history', 'unified_history.json')
    with open(path) as f:
        rows = json.load(f).get('series', [])
    return {r['date']: r['btc_price'] for r in rows if (r.get('btc_price') or 0) > 0}


def _load_tiz():
    """Load tiz_days from backfill_scores.json (pre-computed)."""
    path = os.path.join(ROOT, 'data', 'history', 'backfill_scores.json')
    with open(path) as f:
        rows = json.load(f).get('series', [])
    return {r['date']: r.get('tiz_days', 0) for r in rows}


def _nearest_price_on_or_after(prices, sorted_dates, target_str):
    idx = bisect.bisect_left(sorted_dates, target_str)
    return prices.get(sorted_dates[idx]) if idx < len(sorted_dates) else None


def _build_prev_scores(date_str, cache):
    """Build prev_scores dict from cache (no extra compute_at calls)."""
    td = datetime.date.fromisoformat(date_str)
    prev = {}
    for metric, lb in _METRIC_LOOKBACK.items():
        prev_str = (td - datetime.timedelta(days=lb)).isoformat()
        cached = cache.get(prev_str)
        prev[metric] = cached.get(metric) if cached else None
    return prev


def main():
    print("Loading series data...")
    series = load_data()
    prices = _load_prices()
    tiz_by_date = _load_tiz()
    bottom_dates = _load_bottom_dates()
    sorted_price_dates = sorted(prices)

    start = datetime.date(2018, 1, 1)
    end   = datetime.date.today() - datetime.timedelta(days=RETURN_HORIZON + 1)

    # All calendar dates we need (training range + lookback buffer)
    buffer_start = start - datetime.timedelta(days=LOOKBACK_BUFFER)
    all_dates = []
    d = buffer_start
    while d <= end:
        all_dates.append(d)
        d += datetime.timedelta(days=1)

    # ── First pass: compute & cache metric scores for every date ────────────────
    print(f"First pass: computing metric scores for {len(all_dates)} dates...")
    scores_cache = {}
    for i, td in enumerate(all_dates):
        result = compute_at(td, series)
        scores_cache[td.isoformat()] = result['scores']
        if (i + 1) % 500 == 0:
            print(f"  {td.isoformat()} — {i+1}/{len(all_dates)}")

    # ── Second pass: build training records ─────────────────────────────────────
    print("\nSecond pass: building training records...")
    records = []
    td = start
    while td <= end:
        date_str   = td.isoformat()
        future_str = (td + datetime.timedelta(days=RETURN_HORIZON)).isoformat()

        price_now    = prices.get(date_str)
        price_future = _nearest_price_on_or_after(prices, sorted_price_dates, future_str)

        if not price_now or not price_future:
            td += datetime.timedelta(days=1)
            continue

        curr_scores = scores_cache.get(date_str, {})
        non_null = sum(1 for k in METRIC_KEYS if curr_scores.get(k) is not None)
        if non_null < MIN_METRIC_FEATURES:
            td += datetime.timedelta(days=1)
            continue

        # Phase context: what kind of moment is this?
        prev_scores = _build_prev_scores(date_str, scores_cache)
        sp_v2  = score_processor_v2(curr_scores, prev_scores)
        phases = phase_signals(curr_scores, prev_scores)

        features = {k: curr_scores.get(k) for k in METRIC_KEYS}
        features['sp_v2']      = sp_v2
        features['top_signal'] = phases.get('top_signal')
        features['bot_signal'] = phases.get('bot_signal')
        features['tiz_days']   = tiz_by_date.get(date_str, 0)
        features['days_since_bottom'] = _days_since_bottom(date_str, bottom_dates)

        return_90d = round((price_future / price_now) - 1.0, 4)
        records.append({
            'date':       date_str,
            'features':   features,
            'return_90d': return_90d,
            'label':      1 if return_90d > 0 else 0,
        })

        if len(records) % 500 == 0:
            print(f"  {date_str} — {len(records)} records")

        td += datetime.timedelta(days=1)

    pos = sum(r['label'] for r in records)
    print(f"\nTotal: {len(records)} records — {pos} positive ({100*pos/len(records):.1f}%)")

    path = os.path.join(ROOT, 'data', 'ml_training_data.json')
    with open(path, 'w') as f:
        json.dump({'feature_keys': FEATURE_KEYS, 'records': records}, f)
    print(f"Saved → {path}")


if __name__ == '__main__':
    main()
