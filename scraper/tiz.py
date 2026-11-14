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
_WINDOW_DAYS     = 365   # cumulative window for counting zone days
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
    target_str = datetime.date.today().isoformat()

    sub = [r for r in history if r.get('date') and cutoff <= r['date'] <= target_str]
    sub.sort(key=lambda r: r['date'])

    accumulated_days = 0.0
    min_score = 100

    for r in sub:
        s = r.get('final_score')
        if s is None:
            continue
        if s <= threshold:
            accumulated_days += 1.0
            if s < min_score:
                min_score = s
        elif s >= 55:
            accumulated_days = max(0.0, accumulated_days - 3.0)
            if accumulated_days == 0.0:
                min_score = 100
        else:
            accumulated_days = max(0.0, accumulated_days - 1.5)
            if accumulated_days == 0.0:
                min_score = 100

    days_in_zone = int(round(accumulated_days))
    if days_in_zone <= 0 or min_score > threshold:
        return None, 0, _CALIBRATION

    calibration = adaptive_calibration(min_score)
    progress = min(1.0, days_in_zone / calibration)
    risk = round(_SCORE_FRESH + (_SCORE_MATURE - _SCORE_FRESH) * progress)
    return risk, days_in_zone, calibration


def compute_tiz_causal(scores_history, target_date, threshold=_THRESHOLD, window=_WINDOW_DAYS):
    """Causal TiZ for live and backtest — no phase gate, score threshold only.

    'In zone' means final_score <= threshold (or phase == 'BOTTOM' when available).
    TiZ activates whenever the market is in the low-score zone regardless of phase
    label; the caller scales the contribution by w_bot to control impact.

    Parameters
    ----------
    scores_history : list of (date_str, score, phase?, w_bot?) tuples, or None.
    target_date    : datetime.date — causal cutoff.
    threshold      : int — zone boundary (default 40).
    window         : int — rolling day window (default 180).

    Returns (tiz_score, days_in_zone, calibration_days).
    Returns (None, 0, 200) when not in zone or insufficient history.
    """
    if not scores_history:
        return None, 0, _CALIBRATION

    target_str  = target_date.isoformat()
    cutoff_str  = (target_date - datetime.timedelta(days=window)).isoformat()

    # Only consider history up to target_date
    history = sorted(
        [h for h in scores_history if len(h) >= 2 and h[0] <= target_str],
        key=lambda x: x[0],
    )
    if not history:
        return None, 0, _CALIBRATION

    latest      = history[-1]
    latest_date = latest[0]
    latest_score = latest[1]
    latest_dt   = datetime.date.fromisoformat(latest_date)

    # Most recent record must be within 7 days and score in zone (pure score-based)
    if (target_date - latest_dt).days > 7:
        return None, 0, _CALIBRATION
    if latest_score is None or latest_score > threshold:
        return None, 0, _CALIBRATION

    # Chronological accumulation with regime decay for out-of-zone days.
    # Brief pullbacks or choppy action don't reset the bottom clock,
    # but sustained bull/neutral trends rapidly decay the old accumulation counter.
    sub = [r for r in history if cutoff_str <= r[0] <= target_str]
    sub.sort(key=lambda x: x[0])

    accumulated_days = 0.0
    min_score = 100

    for r in sub:
        s = r[1]
        if s is None:
            continue
        if s <= threshold:
            accumulated_days += 1.0
            if s < min_score:
                min_score = s
        elif s >= 55:
            accumulated_days = max(0.0, accumulated_days - 3.0)
            if accumulated_days == 0.0:
                min_score = 100
        else:
            accumulated_days = max(0.0, accumulated_days - 1.5)
            if accumulated_days == 0.0:
                min_score = 100

    # Include the gap between the last recorded in-zone date and target_date.
    last_in_zone = [r for r in sub if r[1] is not None and r[1] <= threshold]
    if last_in_zone:
        last_dt = datetime.date.fromisoformat(last_in_zone[-1][0])
        gap = (target_date - last_dt).days
        if gap > 0:
            accumulated_days += gap

    days_in_zone = int(round(accumulated_days))
    if days_in_zone <= 0 or min_score > threshold:
        return None, 0, _CALIBRATION

    calibration  = adaptive_calibration(min_score)
    progress     = min(1.0, days_in_zone / calibration)
    tiz_score    = round(_SCORE_FRESH + (_SCORE_MATURE - _SCORE_FRESH) * progress)
    return tiz_score, days_in_zone, calibration


__all__ = ['compute_tiz', 'compute_tiz_causal', 'adaptive_calibration']
