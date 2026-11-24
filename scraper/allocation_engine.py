"""Stateful zero-lookahead Dual-Sleeve Allocation & Regime Engine (BTCBRI V5).

Combines two orthogonal sleeves validated on 2017-2026 zero-lookahead backtests:
  1. Valuation Sleeve (50% weight): BTCBRI Trailing Peak + TiZ awareness
     - Enters when BRI <= 35 (full confirmation when TiZ(BRI <= 25) >= 21 days)
     - Arms exit only after BRI reaches >= 85; exits ONLY when BRI drops >= 15 pts below cycle peak
  2. Momentum Sleeve (50% weight): Weekly Cipher B Hysteresis (±2.0 deadband)
     - Enters when closed weekly (wt1 - wt2) >= +2.0
     - Exits when closed weekly (wt2 - wt1) >= +2.0
"""
from __future__ import annotations


def compute_allocation_signal(
    final_score: float | int | None,
    scores_history: list | None = None,
    cipherb_val: dict | None = None,
    data_quality_status: str = 'valid',
    confidence_score: float = 50.0,
    forward_risk: float | None = None,
    forward_risk_validated: bool = False,
    prev_cb_in_pos: bool | None = None,
) -> dict:
    """Compute causal V5 dual-sleeve allocation and backward-compatible market_regime."""
    if final_score is None:
        return {
            'market_regime': 'Unavailable',
            'action_label': 'UNAVAILABLE',
            'target_allocation_pct': 0,
            'bri_sleeve_pct': 0,
            'cipherb_sleeve_pct': 0,
            'bri_in_position': False,
            'trailing_armed': False,
            'trailing_peak_bri': 0.0,
            'trailing_exit_trigger': None,
            'tiz_le25_days': 0,
            'tiz_confirmed': False,
            'weekly_wt_spread': None,
            'weekly_score': None,
        }

    # 1. Replay causal BTCBRI Valuation Sleeve across historical scores + today's score
    bri_in_pos = False
    trailing_armed = False
    trailing_peak_bri = 0.0
    tiz_le25_days = 0

    hist_scores: list[float] = []
    if scores_history:
        for item in scores_history:
            sc_val = None
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                sc_val = item[1]
            elif isinstance(item, dict):
                sc_val = item.get('final_score')
            elif isinstance(item, (int, float)):
                sc_val = item
            if sc_val is not None:
                hist_scores.append(float(sc_val))

    hist_scores.append(float(final_score))

    for sc in hist_scores:
        tiz_le25_days = (tiz_le25_days + 1) if sc <= 25.0 else 0
        if not bri_in_pos and sc <= 35.0:
            bri_in_pos = True
            trailing_armed = False
            trailing_peak_bri = sc
        elif bri_in_pos:
            if sc >= 85.0:
                trailing_armed = True
            if sc > trailing_peak_bri:
                trailing_peak_bri = sc
            if trailing_armed and sc <= trailing_peak_bri - 15.0:
                bri_in_pos = False
                trailing_armed = False
                trailing_peak_bri = 0.0

    bri_sleeve_pct = 50 if bri_in_pos else 0
    tiz_confirmed = tiz_le25_days >= 21

    # 2. Evaluate Weekly Cipher B Momentum Sleeve (±2.0 hysteresis deadband)
    cb_dict = cipherb_val.get('value', cipherb_val) if isinstance(cipherb_val, dict) else {}
    if not isinstance(cb_dict, dict):
        cb_dict = {}
    wt1 = cb_dict.get('wt1')
    wt2 = cb_dict.get('wt2')
    weekly_score = cb_dict.get('weekly_score')

    wt_spread = round(float(wt1) - float(wt2), 2) if (wt1 is not None and wt2 is not None) else None
    cb_in_pos = False
    if wt_spread is not None:
        if wt_spread >= 2.0:
            cb_in_pos = True
        elif wt_spread <= -2.0:
            cb_in_pos = False
        elif prev_cb_in_pos is not None:
            cb_in_pos = bool(prev_cb_in_pos)
        else:
            cb_in_pos = wt_spread > 0.0
    elif weekly_score is not None:
        cb_in_pos = float(weekly_score) >= 55.0

    cipherb_sleeve_pct = 50 if cb_in_pos else 0
    target_allocation_pct = bri_sleeve_pct + cipherb_sleeve_pct

    # 3. Map to backward-compatible market_regime + rich action_label
    cur_bri = float(final_score)
    high_dd_guard = bool(forward_risk_validated and forward_risk is not None and forward_risk >= 60.0)

    if data_quality_status == 'invalid':
        market_regime = 'Unavailable'
        action_label = 'UNAVAILABLE'
    elif data_quality_status != 'valid':
        market_regime = 'Wait'
        action_label = 'DEGRADED_WAIT'
    elif cur_bri >= 65.0 and not bri_in_pos and not cb_in_pos:
        market_regime = 'Sell'
        action_label = 'EXIT_CASH'
    elif bri_in_pos and trailing_armed:
        market_regime = 'Hold'
        action_label = 'ARMED_TOP_HOLD' if cb_in_pos else 'TRIM_50'
    elif target_allocation_pct == 100 and cur_bri <= 40.0 and not high_dd_guard:
        market_regime = 'Buy'
        action_label = 'STRONG_BUY_100'
    elif cur_bri <= 35.0 and bri_in_pos and confidence_score >= 45 and not high_dd_guard:
        market_regime = 'Buy'
        action_label = 'STRONG_BUY_100' if (cb_in_pos or tiz_confirmed) else 'ACCUMULATE_50'
    elif target_allocation_pct >= 50 and cur_bri > 35.0:
        market_regime = 'Hold'
        action_label = 'HOLD_100' if target_allocation_pct == 100 else 'HOLD_50'
    else:
        market_regime = 'Wait'
        action_label = 'WAIT_CASH'

    return {
        'market_regime': market_regime,
        'action_label': action_label,
        'target_allocation_pct': target_allocation_pct,
        'bri_sleeve_pct': bri_sleeve_pct,
        'cipherb_sleeve_pct': cipherb_sleeve_pct,
        'bri_in_position': bri_in_pos,
        'trailing_armed': trailing_armed,
        'trailing_peak_bri': round(trailing_peak_bri, 1),
        'trailing_exit_trigger': round(trailing_peak_bri - 15.0, 1) if trailing_armed else None,
        'tiz_le25_days': tiz_le25_days,
        'tiz_confirmed': tiz_confirmed,
        'weekly_wt_spread': wt_spread,
        'weekly_score': round(float(weekly_score), 1) if weekly_score is not None else None,
    }
