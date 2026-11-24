"""Causal D1 Dual-Sleeve Allocation & Regime Engine (BTCBRI V5 / Causal v2).

Primary allocator (zero future leakage, unified with tools/run_causal_backtest.py):
  1. Sleeve 1 — Structural Trend (50% weight): Closed Weekly SMA40w
     - 50% BTC when closed weekly close > SMA40w, 0% when < SMA40w
  2. Sleeve 2 — Momentum + Value Sell-Veto (50% weight): Closed Weekly Cipher B 1W
     - Enters (50%) when closed weekly (wt1 - wt2) >= +2.0
     - Exits (0%) when closed weekly (wt1 - wt2) <= -2.0 UNLESS MVRV Value
       Sell-Veto is active (causal MVRV percentile <= 0.25), which blocks exit
       during deep value zones.
     - Deadband (-2.0, +2.0) preserves prior state (true hysteresis).

Legacy BTCBRI Trailing-Peak sleeve is retained strictly as an archive diagnostic
(`legacy_bri_sleeve_pct`, `legacy_trailing_*`) and does NOT control target allocation.
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
    value_sell_veto: bool = False,
    abstain: bool = False,
    sma40_in_pos: bool | None = None,
    sma40_value: float | None = None,
    mvrv_pct: float | None = None,
    d1_state: dict | None = None,
) -> dict:
    """Compute causal D1 dual-sleeve allocation and clean non-contradictory market_regime."""
    if final_score is None and d1_state is None and sma40_in_pos is None:
        return {
            'allocation_engine': 'causal_d1_sma40_cb_veto',
            'market_regime': 'Unavailable',
            'action_label': 'UNAVAILABLE',
            'target_allocation_pct': 0,
            'sma40_sleeve_pct': 0,
            'cipherb_sleeve_pct': 0,
            'bri_sleeve_pct': 0,
            'legacy_bri_sleeve_pct': 0,
            'sma40_in_position': False,
            'sma40_value': None,
            'cb_in_position': False,
            'veto_blocked_exit': False,
            'bri_in_position': False,
            'trailing_armed': False,
            'trailing_peak_bri': 0.0,
            'trailing_exit_trigger': None,
            'tiz_le25_days': 0,
            'tiz_confirmed': False,
            'weekly_wt_spread': None,
            'weekly_score': None,
            'value_sell_veto': False,
            'abstain': True,
        }

    # ── Archive diagnostic only: legacy BRI trailing-peak & TiZ ──────────────
    legacy_bri_in_pos = False
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
    if final_score is not None:
        hist_scores.append(float(final_score))

    for sc in hist_scores:
        tiz_le25_days = (tiz_le25_days + 1) if sc <= 25.0 else 0
        if not legacy_bri_in_pos and sc <= 35.0:
            legacy_bri_in_pos = True
            trailing_armed = False
            trailing_peak_bri = sc
        elif legacy_bri_in_pos:
            if sc >= 85.0:
                trailing_armed = True
            if sc > trailing_peak_bri:
                trailing_peak_bri = sc
            if trailing_armed and sc <= trailing_peak_bri - 15.0:
                legacy_bri_in_pos = False
                trailing_armed = False
                trailing_peak_bri = 0.0

    legacy_bri_sleeve_pct = 50 if legacy_bri_in_pos else 0
    tiz_confirmed = tiz_le25_days >= 21

    # ── 1. Primary Sleeve 1 (50%): Causal Weekly SMA40w Structural Trend ─────
    if sma40_in_pos is None and isinstance(d1_state, dict) and d1_state.get('available'):
        sma40_in_pos = bool(d1_state.get('sma40_in_pos'))
    if sma40_value is None and isinstance(d1_state, dict):
        sma40_value = d1_state.get('sma40_value')
    eff_sma40_in = bool(sma40_in_pos) if sma40_in_pos is not None else False
    sma40_sleeve_pct = 50 if eff_sma40_in else 0

    # ── 2. Primary Sleeve 2 (50%): Weekly Cipher B (±2.0) + Real MVRV Veto ──
    cb_dict = cipherb_val.get('value', cipherb_val) if isinstance(cipherb_val, dict) else {}
    if not isinstance(cb_dict, dict):
        cb_dict = {}
    wt1 = cb_dict.get('wt1')
    wt2 = cb_dict.get('wt2')
    weekly_score = cb_dict.get('weekly_score')
    bearish_div = bool(
        cb_dict.get('double_bearish_div')
        or cb_dict.get('fast_bearish_div')
        or cb_dict.get('bearish_divergence')
        or cb_dict.get('bearish_div')
    )

    if isinstance(d1_state, dict) and d1_state.get('available') and wt1 is None and wt2 is None:
        wt1 = d1_state.get('wt1')
        wt2 = d1_state.get('wt2')

    wt_spread = round(float(wt1) - float(wt2), 2) if (wt1 is not None and wt2 is not None) else None
    veto_blocked_exit = False

    if isinstance(d1_state, dict) and d1_state.get('available') and cipherb_val is None:
        cb_in_pos = bool(d1_state.get('cb_in_pos'))
        veto_blocked_exit = bool(d1_state.get('veto_blocked_exit'))
        wt_spread = d1_state.get('wt_spread')
    else:
        eff_prev_cb = prev_cb_in_pos
        if eff_prev_cb is None and isinstance(d1_state, dict) and d1_state.get('available'):
            eff_prev_cb = d1_state.get('prev_cb_in_pos')

        cb_in_pos = False
        if wt_spread is not None:
            if wt_spread >= 2.0:
                cb_in_pos = True
            elif wt_spread <= -2.0:
                if value_sell_veto and eff_prev_cb is True:
                    cb_in_pos = True
                    veto_blocked_exit = True
                else:
                    cb_in_pos = False
            elif eff_prev_cb is not None:
                cb_in_pos = bool(eff_prev_cb)
            else:
                cb_in_pos = wt_spread > 0.0
        elif weekly_score is not None:
            cb_in_pos = float(weekly_score) >= 55.0

    cipherb_sleeve_pct = 50 if cb_in_pos else 0
    target_allocation_pct = sma40_sleeve_pct + cipherb_sleeve_pct

    # ── 3. Clean, Non-Contradictory Regime & Action Label ────────────────────
    high_dd_guard = bool(forward_risk_validated and forward_risk is not None and forward_risk >= 60.0)
    is_cheap = bool(value_sell_veto or (mvrv_pct is not None and mvrv_pct <= 0.35))
    is_overheated = bool(
        (mvrv_pct is not None and mvrv_pct >= 0.70)
        or (mvrv_pct is None and final_score is not None and float(final_score) >= 65.0)
    )

    if data_quality_status == 'invalid':
        market_regime = 'Unavailable'
        action_label = 'UNAVAILABLE'
    elif data_quality_status != 'valid':
        market_regime = 'Wait'
        action_label = 'DEGRADED_WAIT'
    elif abstain:
        market_regime = 'Wait'
        action_label = 'ABSTAIN_UNCERTAIN'
    elif veto_blocked_exit:
        market_regime = 'Hold'
        action_label = 'VALUE_VETO_HOLD_100' if target_allocation_pct == 100 else 'VALUE_VETO_HOLD_50'
    elif target_allocation_pct == 100:
        if is_cheap and not high_dd_guard:
            market_regime = 'Buy'
            action_label = 'STRONG_BUY_100'
        elif bearish_div:
            market_regime = 'Hold'
            action_label = 'HOLD_100_BEAR_DIV_WATCH'
        else:
            market_regime = 'Hold'
            action_label = 'HOLD_100'
    elif target_allocation_pct == 50:
        if is_cheap and not high_dd_guard:
            market_regime = 'Buy'
            action_label = 'ACCUMULATE_50'
        else:
            market_regime = 'Hold'
            action_label = 'HOLD_50'
    else:
        if value_sell_veto:
            market_regime = 'Wait'
            action_label = 'ACCUMULATE_WATCH'
        elif is_overheated:
            market_regime = 'Sell'
            action_label = 'EXIT_CASH'
        else:
            market_regime = 'Wait'
            action_label = 'WAIT_CASH'

    return {
        'allocation_engine': 'causal_d1_sma40_cb_veto',
        'market_regime': market_regime,
        'action_label': action_label,
        'target_allocation_pct': target_allocation_pct,
        'sma40_sleeve_pct': sma40_sleeve_pct,
        'cipherb_sleeve_pct': cipherb_sleeve_pct,
        # Backward-compat alias for legacy readers; points to active Sleeve 1 (SMA40w)
        'bri_sleeve_pct': sma40_sleeve_pct,
        'legacy_bri_sleeve_pct': legacy_bri_sleeve_pct,
        'sma40_in_position': eff_sma40_in,
        'sma40_value': sma40_value,
        'cb_in_position': cb_in_pos,
        'veto_blocked_exit': veto_blocked_exit,
        'bri_in_position': legacy_bri_in_pos,
        'trailing_armed': trailing_armed,
        'trailing_peak_bri': round(trailing_peak_bri, 1),
        'trailing_exit_trigger': round(trailing_peak_bri - 15.0, 1) if trailing_armed else None,
        'tiz_le25_days': tiz_le25_days,
        'tiz_confirmed': tiz_confirmed,
        'weekly_wt_spread': wt_spread,
        'weekly_score': round(float(weekly_score), 1) if weekly_score is not None else None,
        'value_sell_veto': bool(value_sell_veto),
        'abstain': bool(abstain),
    }
