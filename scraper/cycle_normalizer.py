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


_load()


def _causal_vals(metric: str, ex_type: str, target_date_str: str):
    """Raw metric values at confirmed extremes of ex_type before target_date."""
    result = []
    for e in _extremes:
        if e['type'] != ex_type:
            continue
        if e['date'] >= target_date_str:
            continue
        row = _scores_map.get(e['date'], {})
        v = row.get(metric)
        if v is not None:
            result.append(float(v))
    return result


def cycle_normalize(metric: str, value, target_date) -> int | None:
    """
    Normalize raw metric value to 0-100 using confirmed cycle extremes as anchors.

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
    b_pct = sum(1 for bv in bottom_vals if bv <= v) / len(bottom_vals)
    t_pct = sum(1 for tv in top_vals   if tv <= v) / len(top_vals)
    return round((0.5 * b_pct + 0.5 * t_pct) * 100)


def intra_cycle_percentile(metric: str, value, target_date) -> int | None:
    """Percentile rank of value within the current bull cycle.

    Window: from the last confirmed BOTTOM before target_date up to target_date.
    Uses daily raw metric values stored in scores.json (_scores_map).

    For metrics with a secular trend (e.g. rhodl_ratio), intra-cycle comparison
    is more meaningful than comparing absolute values across different eras.

    Returns None if fewer than 90 days of intra-cycle data are available.
    """
    if value is None or not _extremes or not _scores_map:
        return None

    if isinstance(target_date, datetime.date):
        target_str = target_date.isoformat()
    else:
        target_str = str(target_date)

    # Most recent confirmed BOTTOM strictly before target_date
    bottoms = sorted(
        [e for e in _extremes if e['type'] == 'BOTTOM' and e['date'] < target_str],
        key=lambda e: e['date'],
    )
    if not bottoms:
        return None

    cycle_start = bottoms[-1]['date']

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


def reload():
    """Re-load source files (call after scores.json or cycle_extremes.json update)."""
    _load()
