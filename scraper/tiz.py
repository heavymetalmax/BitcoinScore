"""Time-in-Zone (TiZ) — dynamic risk metric based on days spent in low-score zone.

Rationale: entering the low-risk zone does not confirm a bottom. Historical cycles
(2018: ~150 days, 2022: ~210 days) show that the bottom forms 1-7 months AFTER
first zone entry. The longer the market stays in the zone, the more the bottom
matures — so risk decreases as time accumulates.

Returns a risk score 0-100 where:
  38 = fresh zone entry (high uncertainty)
  5  = mature bottom (calibration_days reached, historical reversal window)
  None = not in zone (metric excluded from composite)
"""
import json
import os
import datetime

_SCORES_PATH     = 'data/history/scores.json'
_THRESHOLD       = 40    # zone boundary (composite score ≤ this)
_WINDOW_DAYS     = 180   # cumulative window for counting zone days
_CALIBRATION     = 200   # kept for backward-compat imports; prefer adaptive_calibration()
_SCORE_FRESH     = 38    # risk at day 0 in zone
_SCORE_MATURE    = 5     # risk at calibration_days


def adaptive_calibration(min_zone_score):
    """Return calibration days based on depth of bottom.

    Anchored to historical data: 2018 (min~25, 231d), 2022 (min~20, 280d).
    Shallower zone entries (min_score closer to threshold) resolve faster.
    """
    if min_zone_score <= 20:
        return 280
    elif min_zone_score <= 25:
        # linear 280→230 as min_score goes 20→25
        return int(280 - (min_zone_score - 20) * 10)
    elif min_zone_score <= 35:
        # linear 230→150 as min_score goes 25→35
        return int(230 - (min_zone_score - 25) * 8)
    return 120


def compute_tiz(threshold=_THRESHOLD, window=_WINDOW_DAYS):
    """Return (tiz_risk_score, days_in_zone, calibration) or (None, 0, 200) when not in zone.

    Calibration is adaptive: deeper bottoms (lower min score) get longer calibration.
    Counts cumulative days with final_score <= threshold in the last `window` days.
    Uses rolling (not consecutive) count so a brief exit doesn't reset the clock.
    """
    if not os.path.exists(_SCORES_PATH):
        return None, 0, _CALIBRATION
    try:
        with open(_SCORES_PATH, encoding='utf-8') as f:
            history = json.load(f)
    except Exception:
        return None, 0, _CALIBRATION

    if not history:
        return None, 0, _CALIBRATION

    # Most recent entry must be in zone; use phase signal if available, else score threshold
    latest = sorted(history, key=lambda r: r.get('date', ''))[-1]
    latest_phase = latest.get('phase')
    if latest_phase is not None:
        if latest_phase != 'BOTTOM':
            return None, 0, _CALIBRATION
    elif latest.get('final_score') is None or latest['final_score'] > threshold:
        return None, 0, _CALIBRATION

    cutoff = (datetime.date.today() - datetime.timedelta(days=window)).isoformat()

    def _in_zone(r):
        if r.get('date', '') < cutoff:
            return False
        ph = r.get('phase')
        if ph is not None:
            return ph == 'BOTTOM'
        s = r.get('final_score')
        return s is not None and s <= threshold

    in_zone = [r for r in history if _in_zone(r)]

    if not in_zone:
        return None, 0, _CALIBRATION

    min_score = min(r['final_score'] for r in in_zone)
    calibration = adaptive_calibration(min_score)
    days_in_zone = len(in_zone)

    progress = min(1.0, days_in_zone / calibration)
    risk = round(_SCORE_FRESH + (_SCORE_MATURE - _SCORE_FRESH) * progress)
    return risk, days_in_zone, calibration


__all__ = ['compute_tiz', 'adaptive_calibration']
