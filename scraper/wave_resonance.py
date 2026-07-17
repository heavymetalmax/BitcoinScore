"""
Wave Resonance Score — rolling-percentile oscillator coherence formula.

Each on-chain metric is mapped to a [0,1] oscillator via its 3-year rolling
percentile rank. The score amplifies only when all waves are "in phase"
(low std = high coherence = resonance).

Formula:
  osc[m]    = percentile_rank(value, 3yr_window)          # [0,1]
  mean      = weighted_average(osc)                        # direction
  std       = weighted_std(osc, mean)                      # phase dispersion
  coherence = max(0, 1 - std / 0.289)                     # 0.289 = 1/(2√3)
  direction = 2 * mean - 1                                 # [-1, +1]
  score     = round(50 + 50 * direction * coherence)       # [0, 100]

High coherence at high mean → top signal (score ≥ 75).
High coherence at low mean → bottom signal (score ≤ 25).
Low coherence (mixed signals) → score stays near 50.

See tools/ml_experiment/reflections/wave_resonance_research.md for the full
research & performance stats (tops avg 81.4%, bottoms avg 13.3%, sep 68.1pp).
"""
import json
import math
import os
import datetime

WINDOW_DAYS = 1095           # 3-year rolling window
MIN_PTS     = 30             # minimum points in window before computing
NORM        = 0.289          # 1/(2√3) — theoretical max std for U[0,1]

# Metric config: weight, field name in scores.json (ONE database), inversion flag
# Inverted = True means high raw value = low risk (e.g. large pi gap = safe)
_METRICS = {
    'nupl':       {'w': 0.32, 'field': 'nupl',           'inv': False},
    'mvrv':       {'w': 0.24, 'field': 'mvrv',           'inv': False},
    'puell':      {'w': 0.14, 'field': 'puell',          'inv': False},
    'cvdd_ratio': {'w': 0.14, 'field': 'cvdd_ratio',     'inv': False},
    'mayer':      {'w': 0.10, 'field': 'mayer_multiple', 'inv': False},
    'pi_gap':     {'w': 0.06, 'field': 'pi_gap_pct',     'inv': True},
}

_UNIFIED_CACHE = None   # {date: row_dict}, lazy-loaded


def _load_unified():
    global _UNIFIED_CACHE
    if _UNIFIED_CACHE is not None:
        return _UNIFIED_CACHE
    path = 'data/history/scores.json'
    if not os.path.exists(path):
        _UNIFIED_CACHE = {}
        return _UNIFIED_CACHE
    _UNIFIED_CACHE = {r['date']: r for r in json.load(open(path, encoding='utf-8'))}
    return _UNIFIED_CACHE


def _oscillator(values, current_value, inverted=False):
    """Rolling percentile rank of current_value within values list → [0,1]."""
    if not values or len(values) < MIN_PTS or current_value is None:
        return None
    count = sum(1 for v in values if v <= current_value)
    osc = count / len(values)
    return 1.0 - osc if inverted else osc


def compute_wave_resonance(date=None, raw_overrides=None):
    """Compute the wave resonance score for a given date (ISO string).

    If date is None, uses the latest date in scores.json (the ONE database).

    raw_overrides — optional dict of fresh metric values that supplement or
    override what's in scores.json (e.g. today's live scraper data).
    Keys match _METRICS fields: 'nupl', 'mvrv', 'puell', 'cvdd_ratio',
    'mayer_multiple', 'pi_gap_pct'.

    Returns dict:
      score       — int [0, 100]  (None if insufficient data)
      coherence   — float [0, 1]
      mean_osc    — float [0, 1]  weighted average of oscillators
      oscillators — {metric: osc_value}  per-metric [0,1] oscillators
      date_used   — the actual date from scores.json that was used
      window_pts  — number of days in the rolling window
    """
    unified = _load_unified()
    if not unified:
        return _empty(date)

    sorted_dates = sorted(unified.keys())

    if date is None:
        date = datetime.date.today().isoformat()

    if date is None:
        return _empty(date)

    # find nearest available date (±7 days), then fall back to most recent past entry
    target = datetime.date.fromisoformat(date)
    actual_date = None
    for offset in range(8):
        for sign in (0, 1, -1):
            cand = (target + datetime.timedelta(days=offset * sign)).isoformat()
            if cand in unified:
                actual_date = cand
                break
        if actual_date:
            break
    if not actual_date:
        # scores.json may be stale — use most recent date with ≥3 core metrics present
        core_fields = [cfg['field'] for cfg in _METRICS.values()]
        for d in reversed(sorted_dates):
            if d > date:
                continue
            row_d = unified[d]
            if sum(1 for f in core_fields if row_d.get(f) is not None) >= 3:
                actual_date = d
                break
    if not actual_date:
        return _empty(date)

    row = unified.get(actual_date, {})
    cutoff = (datetime.date.fromisoformat(actual_date)
              - datetime.timedelta(days=WINDOW_DAYS)).isoformat()

    # merge: unified row ← overrides (live scraper values take precedence)
    overrides = raw_overrides or {}

    oscillators = {}
    for key, cfg in _METRICS.items():
        current = overrides.get(cfg['field'], row.get(cfg['field']))
        if current is None:
            continue
        # nupl in scores.json is in % (e.g. 70.4) — overrides use same units
        window = [
            unified[d][cfg['field']]
            for d in sorted_dates
            if cutoff <= d <= actual_date
            and unified[d].get(cfg['field']) is not None
        ]
        osc = _oscillator(window, current, inverted=cfg['inv'])
        if osc is not None:
            oscillators[key] = round(osc, 4)

    if len(oscillators) < 3:
        return _empty(actual_date)

    # weighted mean and std over available metrics only
    total_w = sum(_METRICS[k]['w'] for k in oscillators)
    mean_v  = sum(_METRICS[k]['w'] * v for k, v in oscillators.items()) / total_w
    var     = sum(_METRICS[k]['w'] * (v - mean_v) ** 2
                  for k, v in oscillators.items()) / total_w
    std_v   = math.sqrt(var)

    coherence = max(0.0, 1.0 - std_v / NORM)
    direction = 2.0 * mean_v - 1.0
    score     = round(50.0 + 50.0 * direction * coherence)
    score     = max(0, min(100, score))

    return {
        'score':       score,
        'coherence':   round(coherence, 3),
        'mean_osc':    round(mean_v, 3),
        'std_osc':     round(std_v, 3),
        'oscillators': oscillators,
        'date_used':   actual_date,
        'window_pts':  len([d for d in sorted_dates if cutoff <= d <= actual_date]),
    }


def _empty(date):
    return {
        'score': None, 'coherence': None, 'mean_osc': None,
        'std_osc': None, 'oscillators': {}, 'date_used': date, 'window_pts': 0,
    }


__all__ = ['compute_wave_resonance']
