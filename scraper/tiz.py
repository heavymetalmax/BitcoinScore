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
_CALIBRATION     = 120   # days for full maturity (avg of 2018~150d, 2022~210d)
_SCORE_FRESH     = 38    # risk at day 0 in zone
_SCORE_MATURE    = 5     # risk at calibration_days


def compute_tiz(threshold=_THRESHOLD, window=_WINDOW_DAYS, calibration=_CALIBRATION):
    """Return (tiz_risk_score, days_in_zone) or (None, 0) when not in zone.

    Counts cumulative days with final_score <= threshold in the last `window` days.
    Uses rolling (not consecutive) count so a brief exit doesn't reset the clock.
    """
    if not os.path.exists(_SCORES_PATH):
        return None, 0
    try:
        with open(_SCORES_PATH, encoding='utf-8') as f:
            history = json.load(f)
    except Exception:
        return None, 0

    if not history:
        return None, 0

    # Most recent entry must be in zone, otherwise metric is inactive
    latest = sorted(history, key=lambda r: r.get('date', ''))[-1]
    if latest.get('final_score') is None or latest['final_score'] > threshold:
        return None, 0

    cutoff = (datetime.date.today() - datetime.timedelta(days=window)).isoformat()
    days_in_zone = sum(
        1 for r in history
        if r.get('date', '') >= cutoff
        and r.get('final_score') is not None
        and r['final_score'] <= threshold
    )

    if days_in_zone == 0:
        return None, 0

    progress = min(1.0, days_in_zone / calibration)
    risk = round(_SCORE_FRESH + (_SCORE_MATURE - _SCORE_FRESH) * progress)
    return risk, days_in_zone


__all__ = ['compute_tiz']
