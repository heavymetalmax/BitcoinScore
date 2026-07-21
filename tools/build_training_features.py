#!/usr/bin/env python3
"""Build V5.12 feature matrix for mixing model training.

V5.7 DESIGN: Per-basket threshold counts replace flat averages
==============================================================
Lesson from V5.x: flat basket averages hurt the model (negative perm importance)
because they are lossy summaries of MICRO features that the model already sees.

Key insight: the model doesn't need to know the AVERAGE of QC metrics.
It needs to know HOW MANY QC metrics are currently screaming danger/safe.
This captures the STRUCTURE of the cluster: consensus vs isolated extreme.

Example:
  nupl=0.97, mvrv=0.85, rhodl=0.60, cvdd=0.50, puell=0.72
  avg  = 0.73  (hides that 2 metrics are in danger zone)
  hot  = 2/5 = 0.40  (reveals cluster is partially alarmed)
  std  = 0.18  (reveals internal disagreement)

These threshold counts answer: "which metrics in the basket are currently KEY?"
→ The ones exceeding the threshold are key. hot_frac encodes how many.

Risk-oriented percentiles (all high = high risk):
  m2_yoy, yield_curve, lth_supply_pct → INVERTED (1 - raw_pct)
  all others → raw_pct (no change)

Feature categories (28 total):
  MICRO        (13) — risk-oriented causal percentile per metric
  BASKET HOT    (3) — fraction of metrics > 0.75 per cluster (QC/DY/MC)
  BASKET COLD   (3) — fraction of metrics < 0.25 per cluster (safe zone)
  DIVERGENCE    (3) — signed basket diffs (from flat avg): dy_vs_qc, mc_vs_qc, all_spread
  COHERENCE     (2) — qc_std, dy_std (internal cluster spread)
  EXTREME       (3) — count_danger, count_safe, extreme_bias (all metrics, global)
  VELOCITY      (1) — delta_qc_30d (best contributor in V5.2, skip noisy dy/mc)

Metric taxonomy:
  QC (Quantitative/position): nupl, mvrv, rhodl_ratio, cvdd_ratio, puell
  DY (Dynamic/flow):          cipherb_daily, mayer_multiple, fear_greed, funding_rate
  MC (Macro/backdrop):        m2_yoy*, yield_curve*, dxy   (* inverted)
  Standalone:                 lth_supply_pct*              (* inverted)

Labels: V3 cycle position score (0-100, causal). 0=buy extreme, 100=sell extreme.
V5 learns to replicate and improve V3 using additional context: ATH, velocity, price level.
Output: data/training_features.json
"""
import bisect
import datetime
import json
import math
from pathlib import Path

QC_METRICS  = ['nupl', 'mvrv', 'rhodl_ratio', 'cvdd_ratio', 'puell']
DY_METRICS  = ['cipherb_daily', 'mayer_multiple', 'fear_greed', 'funding_rate']
MC_METRICS  = ['m2_yoy', 'yield_curve', 'dxy']
ALL_METRICS = QC_METRICS + DY_METRICS + MC_METRICS + ['lth_supply_pct']

RISK_INVERTED = {'m2_yoy', 'yield_curve', 'lth_supply_pct'}

VELOCITY_DAYS = 30
VELOCITY_TOL  = 7
FORWARD_DAYS  = 365   # "буду в прибутку через рік?" — цикловий ризик, не 90d волатильність
TANH_SCALE    = 2.0

# Multi-horizon lookback: days → tolerance
LOOKBACK_HORIZONS = {7: 3, 30: 7, 90: 14, 180: 21}


# ── Helpers ───────────────────────────────────────────────────────────────────

def causal_percentile_series(values_by_date, dates_sorted):
    result = {}
    buf = []
    for date in dates_sorted:
        v = values_by_date.get(date)
        if v is not None:
            try:
                v = float(v)
            except (TypeError, ValueError):
                result[date] = None
                continue
            if math.isnan(v):
                result[date] = None
                continue
            rank = bisect.bisect_right(buf, v) / len(buf) if buf else 0.5
            result[date] = rank
            bisect.insort(buf, v)
        else:
            result[date] = None
    return result


def risk_pct(metric, raw_p):
    if raw_p is None:
        return None
    return (1.0 - raw_p) if metric in RISK_INVERTED else raw_p


def _avg(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def _std(vals):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None
    mean = sum(v) / len(v)
    return math.sqrt(sum((x - mean) ** 2 for x in v) / len(v))


def flat_baskets(rp: dict):
    """Flat risk-oriented average per basket."""
    qc = _avg([rp.get(m) for m in QC_METRICS])
    dy = _avg([rp.get(m) for m in DY_METRICS])
    mc = _avg([rp.get(m) for m in MC_METRICS])
    return qc, dy, mc


def basket_threshold_counts(rp_vals, hot_thresh=0.75, cold_thresh=0.25):
    """Per-basket threshold counts: fraction of metrics in hot/cold zone.

    hot_frac: fraction > hot_thresh  (how many metrics are screaming danger)
    cold_frac: fraction < cold_thresh (how many metrics are in safe zone)

    Returns (hot_frac, cold_frac) or (None, None).
    """
    vals = [v for v in rp_vals if v is not None]
    if not vals:
        return None, None
    hot = sum(1 for v in vals if v > hot_thresh) / len(vals)
    cold = sum(1 for v in vals if v < cold_thresh) / len(vals)
    return hot, cold


def forward_price(date_str, prices, days=FORWARD_DAYS, tol=7):
    target = datetime.date.fromisoformat(date_str) + datetime.timedelta(days=days)
    for delta in range(0, tol + 1):
        for sign in (0, 1, -1):
            d = (target + datetime.timedelta(days=delta * sign)).isoformat()
            if d in prices:
                return prices[d]
    return None


def return_to_label(ret):
    return max(5.0, min(95.0, 50.0 - 50.0 * math.tanh(ret * TANH_SCALE)))


def find_lookback_date(date_str, sorted_dates, days=VELOCITY_DAYS, tol=VELOCITY_TOL):
    target = datetime.date.fromisoformat(date_str) - datetime.timedelta(days=days)
    best, best_delta = None, tol + 1
    for d in sorted_dates:
        d_date = datetime.date.fromisoformat(d)
        delta = abs((d_date - target).days)
        if delta < best_delta:
            best_delta = delta
            best = d
        if d_date > target + datetime.timedelta(days=tol):
            break
    return best if best_delta <= tol else None


def smooth_basket_at(ref_date_str, basket_by_date, window_days=3):
    """Average basket metrics over ±window_days around ref_date.

    Removes single-day price spikes from the lookback reference so that
    velocity deltas (delta_qc_90d etc.) don't jump when a lookback boundary
    crosses an isolated spike in the historical data.
    """
    if ref_date_str is None:
        return None
    center = datetime.date.fromisoformat(ref_date_str)
    agg = {'qc': [], 'dy': [], 'mc': []}
    for delta in range(-window_days, window_days + 1):
        d = (center + datetime.timedelta(days=delta)).isoformat()
        b = basket_by_date.get(d)
        if b:
            for k in agg:
                if b.get(k) is not None:
                    agg[k].append(b[k])
    return {k: sum(v) / len(v) if v else None for k, v in agg.items()}


def build_rolling_peak(values_by_date, sorted_dates, days=90):
    """For each date: max value in the prior N days (exclusive of current day)."""
    result = {}
    for i, date in enumerate(sorted_dates):
        t = datetime.date.fromisoformat(date)
        cutoff = t - datetime.timedelta(days=days)
        peak = None
        for j in range(i - 1, -1, -1):
            prev = sorted_dates[j]
            pt   = datetime.date.fromisoformat(prev)
            if pt < cutoff:
                break
            v = values_by_date.get(prev)
            if v is not None:
                peak = v if peak is None else max(peak, v)
        result[date] = peak
    return result


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    src = Path('data/history/scores.json')
    with open(src) as f:
        records = json.load(f)

    valid = [r for r in records if r.get('btc_price')]
    valid.sort(key=lambda r: r['date'])

    # Forward-fill BMP metrics that disappear when Playwright scraping fails.
    # These metrics (rhodl, cvdd, puell, mayer, lth, asopr) come from BMP and can be
    # None for multiple consecutive days. Using last known value keeps feature vectors stable.
    _BMP = ['rhodl_ratio', 'cvdd_ratio', 'puell', 'mayer_multiple', 'lth_supply_pct', 'asopr']
    _last = {m: None for m in _BMP}
    for r in valid:
        for m in _BMP:
            if r.get(m) is not None:
                _last[m] = r[m]
            elif _last[m] is not None:
                r[m] = _last[m]

    dates   = [r['date'] for r in valid]
    by_date = {r['date']: r for r in valid}
    prices  = {r['date']: r['btc_price'] for r in valid if r.get('btc_price')}

    # ── Pass 1: raw causal percentile per metric ──────────────────────────────
    raw_pct = {m: causal_percentile_series({r['date']: r.get(m) for r in valid}, dates)
               for m in ALL_METRICS}
    # BTC price causal percentile: ATH = 1.0 = maximum danger signal
    btc_price_pct_series = causal_percentile_series(prices, dates)

    # ── Pass 1b: flat basket positions for velocity lookup ────────────────────
    basket_by_date: dict[str, dict] = {}
    for date in dates:
        rp = {m: risk_pct(m, raw_pct[m].get(date)) for m in ALL_METRICS}
        qc, dy, mc = flat_baskets(rp)
        basket_by_date[date] = {'qc': qc, 'dy': dy, 'mc': mc}

    # ── Pass 1c: rolling 90d peak for V3 context (bearish divergence detection) ─
    w_top_by_date  = {r['date']: r.get('w_top')     for r in valid}
    v3_by_date     = {r['date']: r.get('final_score') for r in valid}
    w_top_peak_90d = build_rolling_peak(w_top_by_date,  dates, days=90)
    v3_peak_90d    = build_rolling_peak(v3_by_date,     dates, days=90)

    # ── Pass 2: build feature rows ────────────────────────────────────────────
    rows = []
    labeled = skipped = 0

    for date in dates:
        r = by_date[date]
        rp = {m: risk_pct(m, raw_pct[m].get(date)) for m in ALL_METRICS}

        # Flat baskets — only used for divergence/velocity computation, NOT as direct features
        qc, dy, mc = flat_baskets(rp)

        # Per-basket threshold counts: HOW MANY metrics are in danger/safe zone
        qc_hot, qc_cold = basket_threshold_counts([rp.get(m) for m in QC_METRICS])
        dy_hot, dy_cold = basket_threshold_counts([rp.get(m) for m in DY_METRICS])
        mc_hot, mc_cold = basket_threshold_counts([rp.get(m) for m in MC_METRICS])

        # Signed divergences (from flat risk-oriented averages — for RELATIONSHIP signal)
        dy_vs_qc  = (dy - qc) if dy is not None and qc is not None else None
        mc_vs_qc  = (mc - qc) if mc is not None and qc is not None else None
        all_vals  = [v for v in [qc, dy, mc] if v is not None]
        all_spread = (max(all_vals) - min(all_vals)) if len(all_vals) >= 2 else None

        # Coherence: internal spread within clusters
        qc_std = _std([rp.get(m) for m in QC_METRICS])
        dy_std = _std([rp.get(m) for m in DY_METRICS])

        # Extreme concentration: global across ALL metrics (risk-oriented)
        all_rp = [rp[m] for m in ALL_METRICS if rp.get(m) is not None]
        if all_rp:
            count_danger = sum(1 for x in all_rp if x > 0.80) / len(all_rp)
            count_safe   = sum(1 for x in all_rp if x < 0.20) / len(all_rp)
            extreme_bias = count_danger - count_safe
        else:
            count_danger = count_safe = extreme_bias = None

        # Multi-horizon velocity: basket deltas at 7d / 30d / 90d / 180d
        lb_dates = {n: find_lookback_date(date, dates, days=n, tol=t)
                    for n, t in LOOKBACK_HORIZONS.items()}
        lbs      = {n: smooth_basket_at(lb_dates[n], basket_by_date, window_days=3) if lb_dates[n] else None
                    for n in LOOKBACK_HORIZONS}
        r_lbs    = {n: by_date.get(lb_dates[n]) if lb_dates[n] else None
                    for n in LOOKBACK_HORIZONS}

        def _d(cur, lb_dict, key):
            return (cur - lb_dict[key]) if cur is not None and lb_dict and lb_dict.get(key) is not None else None

        # QC basket deltas
        delta_qc_7d   = _d(qc, lbs[7],   'qc')
        delta_qc_30d  = _d(qc, lbs[30],  'qc')
        delta_qc_90d  = _d(qc, lbs[90],  'qc')
        delta_qc_180d = _d(qc, lbs[180], 'qc')
        # DY basket deltas
        delta_dy_7d   = _d(dy, lbs[7],   'dy')
        delta_dy_30d  = _d(dy, lbs[30],  'dy')
        delta_dy_90d  = _d(dy, lbs[90],  'dy')
        delta_dy_180d = _d(dy, lbs[180], 'dy')
        # MC basket deltas
        delta_mc_7d   = _d(mc, lbs[7],   'mc')
        delta_mc_30d  = _d(mc, lbs[30],  'mc')
        delta_mc_90d  = _d(mc, lbs[90],  'mc')
        delta_mc_180d = _d(mc, lbs[180], 'mc')

        # Momentum acceleration: 30d slope vs 90d slope ("куди прискорюємося?")
        accel_qc = (delta_qc_30d - delta_qc_90d)   if delta_qc_30d  is not None and delta_qc_90d  is not None else None
        accel_dy = (delta_dy_30d - delta_dy_90d)   if delta_dy_30d  is not None and delta_dy_90d  is not None else None

        # V3 velocity: w_top / w_bot / v3_score over multiple horizons
        curr_w_top = r.get('w_top')
        curr_w_bot = r.get('w_bot')
        curr_v3    = r.get('final_score')

        def _vdiff(curr, r_lb, key):
            if curr is None or r_lb is None:
                return None
            prev = r_lb.get(key)
            return (curr - prev) if prev is not None else None

        delta_w_top_30d = _vdiff(curr_w_top, r_lbs[30],  'w_top')
        delta_v3_30d    = _vdiff(curr_v3,    r_lbs[30],  'final_score')
        delta_w_bot_30d = _vdiff(curr_w_bot, r_lbs[30],  'w_bot')
        delta_w_bot_90d = _vdiff(curr_w_bot, r_lbs[90],  'w_bot')

        # "vs local peak" — how much has TOP phase confidence declined from its 90d high?
        # At double top: w_top_peak=0.999 → w_top_now=0.467 → w_top_vs_peak=-0.532 (massive warning)
        peak_w_top = w_top_peak_90d.get(date)
        peak_v3    = v3_peak_90d.get(date)
        w_top_vs_peak = (curr_w_top - peak_w_top) if curr_w_top is not None and peak_w_top is not None else None
        v3_vs_peak    = (curr_v3    - peak_v3)    if curr_v3   is not None and peak_v3    is not None else None

        # BTC price percentile: fraction of all past prices BELOW current price
        # ATH = 1.0 (never seen higher) = maximum uncharted territory risk
        pct_btc_price = btc_price_pct_series.get(date)

        # Label: composite causal cycle position (V5.17)
        # Equal-weight mean of price position + 3 core on-chain cycle indicators (all causal)
        # Answers: "how close to sell/buy extremes?" (0=buy extreme, 100=sell extreme)
        # pct_btc_price = 0.002 at 2018 bottom, = 1.0 at any ATH — monotonic with price ATH
        # NUPL/MVRV/RHODL near 0 at bottoms, near 1 at tops — captures on-chain cycle position
        # On-chain divergence at double tops captured by FEATURES (price_vs_all, ath_divergence)
        _label_pcts = [
            pct_btc_price,
            rp.get('nupl'),
            rp.get('mvrv'),
            rp.get('rhodl_ratio'),
        ]
        _label_valid = [v for v in _label_pcts if v is not None]
        label = round(sum(_label_valid) / len(_label_valid) * 100, 2) if len(_label_valid) >= 2 else None
        if label is not None:
            labeled += 1
        else:
            skipped += 1

        # PRICE DIVERGENCE: price running ahead of on-chain metrics = bearish divergence
        # Classic trader signal: BTC at new ATH but indicators NOT confirming → cycle exhaustion
        # e.g. 2025 ATH: pct_btc_price=1.0, qc_avg≈0.60 → price_vs_qc=+0.40 (strong divergence)
        #      2021 ATH: pct_btc_price=0.98, qc_avg≈0.90 → price_vs_qc=+0.08 (metrics confirmed)
        all_rp_avg = _avg([rp[m] for m in ALL_METRICS if rp.get(m) is not None])
        price_vs_qc  = (pct_btc_price - qc)         if pct_btc_price is not None and qc         is not None else None
        price_vs_dy  = (pct_btc_price - dy)         if pct_btc_price is not None and dy         is not None else None
        price_vs_all = (pct_btc_price - all_rp_avg) if pct_btc_price is not None and all_rp_avg is not None else None

        # COMPOUND: ATH + declining phase = trader's "danger zone" signal
        # pct_btc_price × max(0, -w_top_vs_peak):
        #   2025 ATH $124k: 1.00 × 0.532 = 0.532  ← price at ATH, TOP confidence collapsed
        #   2021 ATH  $67k: 0.99 × 0.000 = 0.000  ← price at ATH, TOP confidence was at peak (confirmed)
        #   Mid-cycle $40k: 0.55 × 0.050 = 0.028  ← background noise
        _neg_w_peak = max(0.0, -w_top_vs_peak) if w_top_vs_peak is not None else None
        _neg_v3_peak = max(0.0, -v3_vs_peak / 30.0) if v3_vs_peak is not None else None
        ath_divergence     = (pct_btc_price * _neg_w_peak)  if pct_btc_price is not None and _neg_w_peak  is not None else None
        ath_v3_divergence  = (pct_btc_price * _neg_v3_peak) if pct_btc_price is not None and _neg_v3_peak is not None else None

        rows.append({
            'date':  date,
            'label': label,
            **{f'raw_{m}': r.get(m) for m in ALL_METRICS},
            'raw_btc_price': r.get('btc_price'),
            # PRICE PERCENTILE: ATH awareness — pct_btc_price must come first
            'pct_btc_price': pct_btc_price,
            # MICRO: risk-oriented causal percentile (inversions applied)
            **{f'pct_{m}': rp[m] for m in ALL_METRICS},
            # BASKET HOT: fraction of cluster metrics > 0.75 (how many are screaming danger)
            'qc_hot': qc_hot, 'dy_hot': dy_hot, 'mc_hot': mc_hot,
            # BASKET COLD: fraction of cluster metrics < 0.25 (how many are in safe zone)
            'qc_cold': qc_cold, 'dy_cold': dy_cold, 'mc_cold': mc_cold,
            # DIVERGENCE: cross-cluster RELATIONSHIP signals (not summaries)
            'dy_vs_qc': dy_vs_qc, 'mc_vs_qc': mc_vs_qc, 'all_spread': all_spread,
            # COHERENCE: internal cluster spread
            'qc_std': qc_std, 'dy_std': dy_std,
            # EXTREME: global consensus across all metrics
            'count_danger': count_danger, 'count_safe': count_safe, 'extreme_bias': extreme_bias,
            # VELOCITY MULTI-HORIZON: basket deltas (7d/30d/90d/180d) + acceleration
            'delta_qc_7d': delta_qc_7d,   'delta_dy_7d': delta_dy_7d,   'delta_mc_7d': delta_mc_7d,
            'delta_qc_30d': delta_qc_30d,  'delta_dy_30d': delta_dy_30d,  'delta_mc_30d': delta_mc_30d,
            'delta_qc_90d': delta_qc_90d,  'delta_dy_90d': delta_dy_90d,  'delta_mc_90d': delta_mc_90d,
            'delta_qc_180d': delta_qc_180d, 'delta_dy_180d': delta_dy_180d, 'delta_mc_180d': delta_mc_180d,
            'delta_w_bot_30d': delta_w_bot_30d, 'delta_w_bot_90d': delta_w_bot_90d,
            'accel_qc': accel_qc, 'accel_dy': accel_dy,
            # PRICE DIVERGENCE: price vs indicator consensus (bearish divergence at tops)
            'price_vs_qc': price_vs_qc, 'price_vs_dy': price_vs_dy, 'price_vs_all': price_vs_all,
            # V3 PHASE CONTEXT: V5 sees everything — stacked ensemble inputs
            'w_top':        r.get('w_top'),
            'w_bot':        r.get('w_bot'),
            'v3_score':     r.get('final_score'),
            'phase_is_top': 1.0 if r.get('phase') == 'TOP'    else 0.0,
            'phase_is_bot': 1.0 if r.get('phase') == 'BOTTOM' else 0.0,
            # V3 VELOCITY / DIVERGENCE: is V3 signal strengthening or fading?
            # Key: w_top_vs_peak = -0.532 at 2025 ATH (TOP fading while price makes new high)
            'delta_w_top_30d':  delta_w_top_30d,
            'delta_v3_30d':     delta_v3_30d,
            'w_top_vs_peak':    w_top_vs_peak,
            'v3_vs_peak':       v3_vs_peak,
            # COMPOUND: ATH price × phase-decline = explicit interaction term
            'ath_divergence':    ath_divergence,
            'ath_v3_divergence': ath_v3_divergence,
        })

    out = Path('data/training_features.json')
    with open(out, 'w') as f:
        json.dump(rows, f, separators=(',', ':'))

    print(f'build_training_features v5.13: {len(rows)} rows → {out}')
    print(f'  labeled: {labeled}   skipped: {skipped}')
    print(f'  QC coverage: {sum(1 for r in rows if r["pct_nupl"] is not None)}')
    print(f'  DY coverage: {sum(1 for r in rows if r["pct_cipherb_daily"] is not None)}')
    print(f'  vel 30d:  {sum(1 for r in rows if r["delta_qc_30d"] is not None)}')
    print(f'  vel 90d:  {sum(1 for r in rows if r["delta_qc_90d"] is not None)}')
    print(f'  vel 180d: {sum(1 for r in rows if r["delta_qc_180d"] is not None)}')
    print(f'Features: 1 PRICE_PCT + 13 MICRO + 6 HOT/COLD + 3 DIV + 2 COH + 3 EXT + 16 VEL_MULTI + 3 PRICE_DIV + 13 V3PHASE = 60')


if __name__ == '__main__':
    main()
