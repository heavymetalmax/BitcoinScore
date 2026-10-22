"""
Cycle-anchored normalization for on-chain metrics.

Instead of hardcoded ranges (e.g. map_cvdd clips to [1, 5]), we derive
normalization anchors from confirmed cycle extremes in cycle_extremes.json.

Algorithm:
    For a given metric and current value V, compute an equal-weight
    percentile across two distributions:
        b_pct = fraction of confirmed-bottom values <= V    (0..1)
        t_pct = fraction of confirmed-top values   <= V    (0..1)
        score = round((0.5*b_pct + 0.5*t_pct) * 100)

This gives:
    V at/below extreme bottom → score ≈ 0
    V at median of bottoms    → score ≈ 25
    V at median of tops       → score ≈ 75
    V at/above extreme top    → score ≈ 100

All anchors come from data — zero hardcoded domain numbers.
Causal: only extremes confirmed before target_date are used.
Fallback: if fewer than 2 bottoms or 2 tops are available, returns None
          (caller falls back to causal percentile or fixed map).

Metrics supported (all higher-value = more risk):
    nupl, mvrv, cvdd_ratio, rhodl_ratio, puell
"""

import json
import os
import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXTREMES_PATH = os.path.join(_ROOT, 'data', 'cycle_extremes.json')
_SCORES_PATH   = os.path.join(_ROOT, 'data', 'history', 'scores.json')

SUPPORTED_METRICS = {'nupl', 'mvrv', 'cvdd_ratio', 'puell'}
# rhodl_ratio is excluded from cycle_normalize: it has a secular downward trend
# across cycles (2017 "dips" had rhodl=36,000+, above 2025 ATH of 3507), which
# makes cross-cycle comparison meaningless. Uses intra_cycle_percentile instead.
INTRA_CYCLE_METRICS = {'rhodl_ratio'}

# Loaded once at module init
_extremes: list = []
_scores_map: dict = {}


def _load():
    global _extremes, _scores_map
    if not os.path.exists(_EXTREMES_PATH) or not os.path.exists(_SCORES_PATH):
        return
    with open(_EXTREMES_PATH) as f:
        _extremes = json.load(f).get('extremes', [])
    with open(_SCORES_PATH) as f:
        rows = json.load(f)
    _scores_map = {r['date']: r for r in rows if r.get('date')}


def invalidate_caches():
    """Reload extremes and scores.json from disk."""
    _load()


_load()


# Recency decay factor per older confirmed extreme (diminishing returns adjustment)
_RECENCY_GAMMA = 0.55


def _causal_vals(metric: str, ex_type: str, target_date_str: str):
    """Return [(value, recency_weight), ...] at confirmed extremes before target_date."""
    matched = []
    for e in _extremes:
        if e['type'] != ex_type:
            continue
        confirmed_at = e.get('confirmed_at')
        if not confirmed_at or confirmed_at > target_date_str:
            continue
        row = _scores_map.get(e['date'], {})
        v = row.get(metric)
        if v is not None:
            matched.append((e['date'], float(v)))
    matched.sort(key=lambda item: item[0])
    n = len(matched)
    # Newer extremes receive higher weight: w_i = gamma^(n - 1 - i)
    return [(val, _RECENCY_GAMMA ** (n - 1 - idx)) for idx, (_, val) in enumerate(matched)]


def _interp_fraction(weighted_vals, v) -> float:
    """Recency-weighted piecewise-linear empirical CDF in [0, 1].

    Accepts either [(val, weight), ...] or plain [val, ...].
    """
    if not weighted_vals:
        return 0.5
    if isinstance(weighted_vals[0], (tuple, list)):
        pairs = sorted(weighted_vals, key=lambda x: x[0])
    else:
        pairs = [(float(x), 1.0) for x in sorted(weighted_vals)]

    n = len(pairs)
    vals = [p[0] for p in pairs]
    weights = [p[1] for p in pairs]

    if v <= vals[0]:
        return 0.0
    if v >= vals[-1]:
        return 1.0

    # Cumulative weight quantiles anchored at 0.0 (min) and 1.0 (max)
    interval_weights = [0.5 * (weights[i - 1] + weights[i]) for i in range(1, n)]
    total_iw = sum(interval_weights)
    if total_iw <= 0:
        quantiles = [i / (n - 1) for i in range(n)]
    else:
        quantiles = [0.0]
        acc = 0.0
        for iw in interval_weights:
            acc += iw
            quantiles.append(acc / total_iw)
        quantiles[-1] = 1.0

    for i in range(1, n):
        if v <= vals[i]:
            lo, hi = vals[i - 1], vals[i]
            frac = (v - lo) / (hi - lo) if hi > lo else 0.0
            return quantiles[i - 1] + frac * (quantiles[i] - quantiles[i - 1])
    return 1.0


def cycle_normalize(metric: str, value, target_date) -> int | None:
    """
    Normalize raw metric value to 0-100 using confirmed cycle extremes as anchors,
    blended with intra-cycle percentile to account for secular diminishing returns.

    Returns None if insufficient history (fewer than 2 confirmed bottoms or tops).
    Caller should fall back to causal percentile in that case.
    """
    if value is None or metric not in SUPPORTED_METRICS:
        return None

    if isinstance(target_date, datetime.date):
        target_str = target_date.isoformat()
    else:
        target_str = str(target_date)

    bottom_vals = _causal_vals(metric, 'BOTTOM', target_str)
    top_vals    = _causal_vals(metric, 'TOP',    target_str)

    if len(bottom_vals) < 2 or len(top_vals) < 2:
        return None

    v = float(value)
    b_pct = _interp_fraction(bottom_vals, v)
    t_pct = _interp_fraction(top_vals, v)

    # Piecewise-continuous anchor mapping:
    #   [b_min .. b_max] → [0 .. 15]   (confirmed bottom zone)
    #   [b_max .. t_min] → [15 .. 75]  (neutral / bull transition)
    #   [t_min .. t_max] → [75 .. 100] (confirmed top zone)
    b_max = max(x[0] for x in bottom_vals)
    t_min = min(x[0] for x in top_vals)
    if v <= b_max:
        cross_score = 15.0 * b_pct
    elif v < t_min and t_min > b_max:
        mid_frac = (v - b_max) / (t_min - b_max)
        cross_score = 15.0 + 60.0 * mid_frac
    else:
        cross_score = 75.0 + 25.0 * t_pct

    intra_score = intra_cycle_percentile(metric, value, target_date)
    if intra_score is not None and cross_score > 40.0:
        # Secular diminishing returns compress cycle TOPS (MVRV 9.3 → 5.4 → 3.5 → 2.8),
        # whereas cycle BOTTOMS remain anchored (MVRV < 0, CVDD ~ 1.0, Puell < 0.5).
        # Ramp intra-cycle weight from 0% at cross_score=40 to 75% at cross_score>=55.
        w_intra = min(0.75, (cross_score - 40.0) / 15.0 * 0.75)
        return round((1.0 - w_intra) * cross_score + w_intra * float(intra_score))
    return round(cross_score)


def _last_major_bottom_date(target_str: str) -> str | None:
    """Return date of the most recent confirmed major cycle bottom before target_str."""
    major_bottoms = sorted(
        [e for e in _extremes
         if e['type'] == 'BOTTOM' and e.get('major_cycle', True)
         and e.get('confirmed_at') and e['confirmed_at'] <= target_str],
        key=lambda e: e['date'],
    )
    if major_bottoms:
        return major_bottoms[-1]['date']
    bottoms = sorted(
        [e for e in _extremes
         if e['type'] == 'BOTTOM' and e.get('confirmed_at')
         and e['confirmed_at'] <= target_str],
        key=lambda e: e['date'],
    )
    return bottoms[-1]['date'] if bottoms else None


def intra_cycle_percentile(metric: str, value, target_date) -> int | None:
    """Percentile rank of value within the current bull cycle.

    Window: from the last confirmed major BOTTOM before target_date up to target_date.
    Uses daily raw metric values stored in scores.json (_scores_map).

    For metrics with a secular trend (e.g. rhodl_ratio, mvrv, nupl, cvdd, puell),
    intra-cycle comparison captures current-cycle extremes despite diminishing returns.

    Returns None if fewer than 90 days of intra-cycle data are available.
    """
    if value is None or not _extremes or not _scores_map:
        return None

    if isinstance(target_date, datetime.date):
        target_str = target_date.isoformat()
    else:
        target_str = str(target_date)

    cycle_start = _last_major_bottom_date(target_str)
    if not cycle_start:
        return None

    # Collect all daily metric values in [cycle_start, target_date)
    cycle_vals = []
    for date, row in _scores_map.items():
        if cycle_start <= date < target_str:
            v = row.get(metric)
            if v is not None:
                cycle_vals.append(float(v))

    if len(cycle_vals) < 90:
        return None

    v = float(value)
    return round(sum(1 for cv in cycle_vals if cv <= v) / len(cycle_vals) * 100)


def price_cycle_percentile(target_date, current_price) -> int | None:
    """Percentile rank of current BTC price within the current bull cycle.

    When price is at new cycle ATH → 100. At cycle bottom → 0.
    Uses btc_price field from scores.json (_scores_map).

    This metric captures the direct price-based risk dimension: being at a
    higher price within the cycle is unambiguously riskier to buy regardless
    of what on-chain metrics show. High utility in TOP phase only.

    Returns None if fewer than 90 days of price history in current cycle.
    """
    if current_price is None or not _extremes or not _scores_map:
        return None

    if isinstance(target_date, datetime.date):
        target_str = target_date.isoformat()
    else:
        target_str = str(target_date)

    cycle_start = _last_major_bottom_date(target_str)
    if not cycle_start:
        return None

    prices = []
    for date, row in _scores_map.items():
        if cycle_start <= date < target_str:
            p = row.get('btc_price')
            if p is not None:
                prices.append(float(p))

    if len(prices) < 90:
        return None

    p = float(current_price)
    return round(sum(1 for pv in prices if pv <= p) / len(prices) * 100)


def reload():
    """Re-load source files (call after scores.json or cycle_extremes.json update)."""
    _load()


_load()
