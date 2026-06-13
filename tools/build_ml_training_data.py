"""Build ML training data: (metric_scores_vector, 90d_return_label) pairs.

Reads from the same data sources as the live system (compute_at = point-in-time
consistent with adaptive calibration). Each sample: 12 metric scores [0-100]
+ binary label (1 if 90-day forward BTC return > 0, else 0).

Run: python3 tools/build_ml_training_data.py
Output: data/ml_training_data.json
"""
import sys, os, json, datetime
sys.path.insert(0, '.')

from tools.backtest import load_data, compute_at

ROOT = os.path.join(os.path.dirname(__file__), '..')

FEATURE_KEYS = [
    'nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'mayer_multiple',
    'asopr', 'cipherb_weekly', 'cipherb_daily', 'fear_greed', 'm2_yoy',
]

MIN_FEATURES  = 6     # require at least N non-null scores
STEP_DAYS     = 1     # sample every N days
RETURN_HORIZON = 90   # forward return window in days


def _load_prices():
    path = os.path.join(ROOT, 'data', 'history', 'unified_history.json')
    with open(path) as f:
        rows = json.load(f).get('series', [])
    prices = {}
    for r in rows:
        p = r.get('btc_price')
        if p and p > 0:
            prices[r['date']] = p
    return prices


def _nearest_price_on_or_after(prices, sorted_dates, target_str):
    """Return price for nearest date >= target_str, or None."""
    import bisect
    idx = bisect.bisect_left(sorted_dates, target_str)
    if idx >= len(sorted_dates):
        return None
    return prices.get(sorted_dates[idx])


def main():
    print("Loading series data...")
    series = load_data()
    prices = _load_prices()
    sorted_dates = sorted(prices)

    start = datetime.date(2018, 1, 1)
    end   = datetime.date.today() - datetime.timedelta(days=RETURN_HORIZON + 1)

    records = []
    cur = start
    while cur <= end:
        cur_str    = cur.isoformat()
        future_str = (cur + datetime.timedelta(days=RETURN_HORIZON)).isoformat()

        price_now    = prices.get(cur_str)
        price_future = _nearest_price_on_or_after(prices, sorted_dates, future_str)

        if not price_now or not price_future:
            cur += datetime.timedelta(days=STEP_DAYS)
            continue

        result = compute_at(cur, series)
        scores = result['scores']

        features = {k: scores.get(k) for k in FEATURE_KEYS}
        non_null = sum(1 for v in features.values() if v is not None)
        if non_null < MIN_FEATURES:
            cur += datetime.timedelta(days=STEP_DAYS)
            continue

        return_90d = round((price_future / price_now) - 1.0, 4)
        records.append({
            'date':       cur_str,
            'features':   features,
            'return_90d': return_90d,
            'label':      1 if return_90d > 0 else 0,
        })

        if len(records) % 100 == 0:
            print(f"  {cur_str} — {len(records)} records")

        cur += datetime.timedelta(days=STEP_DAYS)

    pos = sum(r['label'] for r in records)
    neg = len(records) - pos
    print(f"\nTotal: {len(records)} records — {pos} positive ({100*pos/len(records):.1f}%), {neg} negative")

    path = os.path.join(ROOT, 'data', 'ml_training_data.json')
    with open(path, 'w') as f:
        json.dump({'feature_keys': FEATURE_KEYS, 'records': records}, f)

    print(f"Saved → {path}")


if __name__ == '__main__':
    main()
