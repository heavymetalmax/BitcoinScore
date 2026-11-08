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

import bisect
import json
import os
import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXTREMES_PATH = os.path.join(_ROOT, 'data', 'cycle_extremes.json')
_SCORES_PATH   = os.path.join(_ROOT, 'data', 'history', 'scores.json')
_CVDD_RATIO_PATH = os.path.join(_ROOT, 'data', 'history', 'cvdd_ratio_history.json')

SUPPORTED_METRICS = {'nupl', 'mvrv', 'cvdd_ratio', 'puell'}
# rhodl_ratio is excluded from cycle_normalize: it has a secular downward trend
# across cycles (2017 "dips" had rhodl=36,000+, above 2025 ATH of 3507), which
# makes cross-cycle comparison meaningless. Uses intra_cycle_percentile instead.
INTRA_CYCLE_METRICS = {'rhodl_ratio'}

# Loaded once at module init
_extremes: list = []
_scores_map: dict = {}
_price_dates: list[str] = []
_price_vals: list[float] = []


def _load():
    global _extremes, _scores_map, _price_dates, _price_vals
    if not os.path.exists(_EXTREMES_PATH) or not os.path.exists(_SCORES_PATH):
        return
    with open(_EXTREMES_PATH) as f:
        _extremes = json.load(f).get('extremes', [])
    with open(_SCORES_PATH) as f:
        rows = json.load(f)
    _scores_map = {r['date']: r for r in rows if r.get('date')}

    price_by_date: dict[str, float] = {}
    if os.path.exists(_CVDD_RATIO_PATH):
        try:
            with open(_CVDD_RATIO_PATH, encoding='utf-8') as f:
                cv_raw = json.load(f)
            cv_rows = cv_raw.get('series', cv_raw) if isinstance(cv_raw, dict) else cv_raw
            for r in cv_rows:
                if isinstance(r, dict) and r.get('date') and r.get('btc_price'):
                    price_by_date[r['date'][:10]] = float(r['btc_price'])
        except Exception:
            pass
    for d, r in _scores_map.items():
        if r.get('btc_price') is not None:
            price_by_date[d[:10]] = float(r['btc_price'])
    _price_dates = sorted(price_by_date.keys())
    _price_vals = [price_by_date[d] for d in _price_dates]


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


def wma200_at(target_date) -> float | None:
    """Causal 200-week (1400-day) simple moving average of BTC price at target_date."""
    if not _price_dates:
        return None
    target_str = target_date.isoformat() if isinstance(target_date, datetime.date) else str(target_date)[:10]
    idx = bisect.bisect_right(_price_dates, target_str) - 1
    if idx < 199:
        return None
    win = _price_vals[max(0, idx - 1399) : idx + 1]
    return sum(win) / len(win)


def dma200_at(target_date) -> float | None:
    """Causal 200-day simple moving average of BTC price at target_date."""
    if not _price_dates:
        return None
    target_str = target_date.isoformat() if isinstance(target_date, datetime.date) else str(target_date)[:10]
    row = _scores_map.get(target_str)
    if row and row.get('btc_price') and row.get('mayer_multiple') and float(row['mayer_multiple']) > 0:
        return float(row['btc_price']) / float(row['mayer_multiple'])
    idx = bisect.bisect_right(_price_dates, target_str) - 1
    if idx < 199:
        return None
    win = _price_vals[idx - 199 : idx + 1]
    return sum(win) / len(win)


def compute_ma_context(target_date, current_price=None, mayer_raw=None) -> dict:
    """Compute causal 200D MA (with 30d slope & regime) and 200W MA context at target_date.

    Returns dict with:
      dma_200           : float | None  — 200-day SMA in USD
      dma_200_slope_30d : float | None  — 30-day % change of 200-day SMA
      above_dma_200     : bool  | None  — True when price >= 200D MA
      mayer_multiple    : float | None  — Price / 200D MA
      wma_200           : float | None  — 200-week (1400d) SMA in USD
      ma200w_ratio      : float | None  — Price / 200W MA
      ma_regime         : str           — 'BULL_EXPANSION' | 'BULL_PULLBACK' | 'RECOVERY' |
                                          'BEAR_CONTRACTION' | 'DEEP_ACCUMULATION' | 'NEUTRAL'
    """
    if isinstance(target_date, str):
        td = datetime.date.fromisoformat(target_date[:10])
    elif isinstance(target_date, datetime.date):
        td = target_date
    else:
        td = datetime.date.today()
    td_str = td.isoformat()

    p = float(current_price) if isinstance(current_price, (int, float)) else None
    if p is None and _scores_map.get(td_str, {}).get('btc_price') is not None:
        p = float(_scores_map[td_str]['btc_price'])

    mm_val = None
    dma_200 = None
    slope_30d = None
    if isinstance(mayer_raw, dict):
        inner = mayer_raw.get('value', mayer_raw)
        if isinstance(inner, dict):
            mm_val = inner.get('value')
            dma_200 = inner.get('dma_200')
            slope_30d = inner.get('dma_200_slope_30d')
        elif isinstance(inner, (int, float)):
            mm_val = float(inner)
    elif isinstance(mayer_raw, (int, float)):
        mm_val = float(mayer_raw)

    if dma_200 is None:
        if p is not None and mm_val is not None and mm_val > 0:
            dma_200 = p / mm_val
        else:
            dma_200 = dma200_at(td)

    if mm_val is None and p is not None and dma_200 and dma_200 > 0:
        mm_val = p / dma_200

    if slope_30d is None and dma_200 and dma_200 > 0:
        dma_prev30 = dma200_at(td - datetime.timedelta(days=30))
        if dma_prev30 and dma_prev30 > 0:
            slope_30d = round((dma_200 / dma_prev30 - 1.0) * 100.0, 2)

    wma_200 = wma200_at(td)
    ma200w_ratio = round(p / wma_200, 3) if (p is not None and wma_200 and wma_200 > 0) else None
    above_dma_200 = (mm_val >= 1.0) if mm_val is not None else None

    ma_regime = 'NEUTRAL'
    if above_dma_200 is not None:
        sl = slope_30d if slope_30d is not None else 0.0
        r200w = ma200w_ratio if ma200w_ratio is not None else 1.5
        if above_dma_200:
            ma_regime = 'BULL_EXPANSION' if sl >= 0.0 else 'RECOVERY'
        else:
            if sl > 0.5 and r200w >= 1.35:
                ma_regime = 'BULL_PULLBACK'
            elif sl <= 0.0 and r200w <= 1.15:
                ma_regime = 'DEEP_ACCUMULATION'
            else:
                ma_regime = 'BEAR_CONTRACTION'

    return {
        'dma_200':           round(dma_200, 1) if dma_200 is not None else None,
        'dma_200_slope_30d': round(slope_30d, 2) if slope_30d is not None else None,
        'above_dma_200':     above_dma_200,
        'mayer_multiple':    round(mm_val, 3) if mm_val is not None else None,
        'wma_200':           round(wma_200, 1) if wma_200 is not None else None,
        'ma200w_ratio':      ma200w_ratio,
        'ma_regime':         ma_regime,
    }


def price_cycle_percentile(target_date, current_price) -> int | None:
    """Percentile rank of current BTC price within the current bull cycle,
    anchored by the 200-week moving average (200W MA) at macro cycle floors.

    When price is at new cycle ATH → 100. At cycle bottom / below 200W MA → 0.
    Uses btc_price field from scores.json (_scores_map) plus causal 200W MA
    so bear-market bottoms prior to causal confirmation date still register
    as low cycle position rather than mid-range vs the previous cycle's bottom.

    Returns None if fewer than 90 days of price history in current cycle.
    """
    if current_price is None or not _extremes or not _scores_map:
        return None

    if isinstance(target_date, datetime.date):
        target_str = target_date.isoformat()
    else:
        target_str = str(target_date)[:10]

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
    raw_pct = sum(1 for pv in prices if pv <= p) / len(prices) * 100.0

    # Anchor cycle position to 200-week MA when price approaches or drops below
    # the macro 200W MA floor (eliminates pre-confirmation bottom lag in bear markets).
    wma = wma200_at(target_str)
    if wma and wma > 0:
        r200w = p / wma
        if r200w <= 1.35:
            floor_score = max(0.0, min(25.0, (r200w - 0.65) / 0.50 * 20.0))
            if r200w <= 1.05:
                return round(min(raw_pct, floor_score))
            blend = (r200w - 1.05) / 0.30
            return round(min(raw_pct, (1.0 - blend) * floor_score + blend * raw_pct))

    return round(raw_pct)


def reload():
    """Re-load source files (call after scores.json or cycle_extremes.json update)."""
    _load()


_load()
