"""Causal 4-Output Core & Entry/Exit Confirmation Checklist.

Provides leakage-free, non-overlapping outputs alongside legacy BRI compatibility:
  1. valuation_score (0-100): causal expanding percentile of MVRV (cost basis),
     plus Value Sell-Veto flag (mvrv_pct <= 0.25).
  2. trend_regime: closed weekly Cipher B 1w (with +/-2.0 hysteresis) + 200DMA.
  3. tail_risk_13w: calibrated P(MaxDD_13w >= 25%) from scraper/tail_risk.py.
  4. entry_exit_checklist + abstain_state: transparent confirmation across
     Puell, CVDD, aSOPR, Fear & Greed, Funding, ETF flows, DXY, and M2 YoY.
"""
from __future__ import annotations

import bisect
import datetime
import json
import os
from typing import Any


def infer_prev_cipherb_state(
    current_bar_date: str | None = None,
    path: str = 'data/history/cipherb_btcusdt_1w.json',
) -> bool | None:
    """Replay +/-2.0 deadband hysteresis over closed weekly bars strictly before current_bar_date."""
    if not os.path.exists(path):
        return None
    try:
        raw = json.load(open(path, encoding='utf-8'))
        series = raw.get('series', raw) if isinstance(raw, dict) else raw
    except Exception:
        return None

    in_pos: bool | None = None
    for row in sorted(
        (r for r in series if isinstance(r, dict) and r.get('date')),
        key=lambda r: str(r['date'])[:10],
    ):
        d = str(row['date'])[:10]
        if current_bar_date and d >= current_bar_date[:10]:
            break
        wt1, wt2 = row.get('wt1'), row.get('wt2')
        if wt1 is None or wt2 is None:
            continue
        spread = float(wt1) - float(wt2)
        if spread >= 2.0:
            in_pos = True
        elif spread <= -2.0:
            in_pos = False
        elif in_pos is None:
            in_pos = spread > 0.0
    return in_pos


def compute_causal_mvrv_rank(
    mvrv_today: float | None,
    as_of_date: datetime.date | str | None = None,
    scores_path: str = 'data/history/scores.json',
) -> dict[str, Any]:
    """Compute strictly causal expanding percentile rank of MVRV (< as_of_date)."""
    if as_of_date is None:
        cutoff = datetime.date.today().isoformat()
    elif isinstance(as_of_date, str):
        cutoff = as_of_date[:10]
    else:
        cutoff = as_of_date.isoformat()

    hist_vals: list[float] = []
    if os.path.exists(scores_path):
        try:
            for row in json.load(open(scores_path, encoding='utf-8')):
                d = str(row.get('date', ''))[:10]
                v = row.get('mvrv')
                if d and d < cutoff and v is not None:
                    hist_vals.append(float(v))
        except Exception:
            pass

    if mvrv_today is None or not hist_vals:
        return {
            'valuation_score': None,
            'mvrv_pct': None,
            'value_sell_veto': False,
            'history_n': len(hist_vals),
        }

    hist_vals.sort()
    rank = bisect.bisect_right(hist_vals, float(mvrv_today)) / len(hist_vals)
    return {
        'valuation_score': round(rank * 100.0, 1),
        'mvrv_pct': round(rank, 4),
        'value_sell_veto': rank <= 0.25,
        'history_n': len(hist_vals),
    }


def _extract_val(metrics: dict[str, Any], key: str, subkey: str | None = None) -> tuple[float | None, dict[str, Any]]:
    entry = metrics.get(key)
    if not isinstance(entry, dict):
        return (float(entry) if isinstance(entry, (int, float)) else None), {}
    raw = entry.get('value')
    if isinstance(raw, dict) and subkey is not None:
        raw = raw.get(subkey)
    try:
        val = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        val = None
    return val, entry


def build_entry_exit_checklist(
    raw_metrics: dict[str, Any],
    mvrv_info: dict[str, Any],
    tail_risk: dict[str, Any] | None,
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    """Build transparent confirmation checklist and explicit abstain/confidence state."""
    mvrv_val, mvrv_meta = _extract_val(raw_metrics, 'mvrv')
    puell_val, puell_meta = _extract_val(raw_metrics, 'puell_multiple')
    cvdd_val, cvdd_meta = _extract_val(raw_metrics, 'cvdd_ratio')
    asopr_val, asopr_meta = _extract_val(raw_metrics, 'asopr')
    fg_val, fg_meta = _extract_val(raw_metrics, 'fear_greed')
    fr_val, fr_meta = _extract_val(raw_metrics, 'funding_rate', 'avg_7d')
    etf_val, etf_meta = _extract_val(raw_metrics, 'etf_flows', 'value')
    dxy_val, dxy_meta = _extract_val(raw_metrics, 'dxy')
    m2_val, m2_meta = _extract_val(raw_metrics, 'm2_mom')
    mayer_val, mayer_meta = _extract_val(raw_metrics, 'mayer_multiple', 'value')

    cb_entry = raw_metrics.get('cipherb') if isinstance(raw_metrics.get('cipherb'), dict) else {}
    cb_val = cb_entry.get('value') if isinstance(cb_entry, dict) else {}
    cb_val = cb_val if isinstance(cb_val, dict) else {}
    wt1, wt2 = cb_val.get('wt1'), cb_val.get('wt2')
    wt_spread = round(float(wt1) - float(wt2), 2) if (wt1 is not None and wt2 is not None) else None

    items = [
        {
            'key': 'mvrv',
            'label': 'Собівартість (MVRV Z)',
            'role': 'core_valuation',
            'value': round(mvrv_val, 3) if mvrv_val is not None else None,
            'causal_pct': mvrv_info.get('valuation_score'),
            'status': 'value_zone' if (mvrv_info.get('mvrv_pct') is not None and mvrv_info['mvrv_pct'] <= 0.25) else ('overheated' if (mvrv_info.get('mvrv_pct') is not None and mvrv_info['mvrv_pct'] >= 0.75) else 'neutral'),
            'stale': bool(mvrv_meta.get('is_fallback', False)),
        },
        {
            'key': 'cipherb_1w',
            'label': 'Cipher B (1W wt1−wt2)',
            'role': 'core_timing',
            'value': wt_spread,
            'status': 'bullish_confirm' if (wt_spread is not None and wt_spread >= 2.0) else ('bearish_caution' if (wt_spread is not None and wt_spread <= -2.0) else 'neutral'),
            'stale': False,
        },
        {
            'key': 'mayer_multiple',
            'label': 'Тренд 200DMA (Mayer)',
            'role': 'core_trend',
            'value': round(mayer_val, 3) if mayer_val is not None else None,
            'status': 'value_zone' if (mayer_val is not None and mayer_val < 0.85) else ('bull_trend' if (mayer_val is not None and mayer_val >= 1.0) else 'neutral'),
            'stale': False,
        },
        {
            'key': 'puell_multiple',
            'label': 'Майнери (Puell)',
            'role': 'confirmation',
            'value': round(puell_val, 3) if puell_val is not None else None,
            'status': 'value_zone' if (puell_val is not None and puell_val <= 0.65) else ('overheated' if (puell_val is not None and puell_val >= 2.0) else 'neutral'),
            'stale': bool(puell_meta.get('is_fallback', False)),
        },
        {
            'key': 'cvdd_ratio',
            'label': 'Підлога циклу (CVDD)',
            'role': 'confirmation',
            'value': round(cvdd_val, 3) if cvdd_val is not None else None,
            'status': 'value_zone' if (cvdd_val is not None and cvdd_val <= 1.25) else ('overheated' if (cvdd_val is not None and cvdd_val >= 3.0) else 'neutral'),
            'stale': bool(cvdd_meta.get('is_fallback', False)),
        },
        {
            'key': 'asopr',
            'label': 'Реаліз. прибуток (aSOPR)',
            'role': 'confirmation',
            'value': round(asopr_val, 4) if asopr_val is not None else None,
            'status': 'capitulation' if (asopr_val is not None and asopr_val < 0.99) else ('profit_recovery' if (asopr_val is not None and asopr_val >= 1.0) else 'neutral'),
            'stale': bool(asopr_meta.get('is_fallback', False)),
        },
        {
            'key': 'fear_greed',
            'label': 'Fear & Greed (7d)',
            'role': 'sentiment',
            'value': round(fg_val, 1) if fg_val is not None else None,
            'status': 'extreme_fear' if (fg_val is not None and fg_val <= 25) else ('extreme_greed' if (fg_val is not None and fg_val >= 75) else 'neutral'),
            'stale': bool(fg_meta.get('is_fallback', False)),
        },
        {
            'key': 'funding_rate',
            'label': 'Фандінг 7d (%)',
            'role': 'leverage',
            'value': round(fr_val, 5) if fr_val is not None else None,
            'status': 'short_fear' if (fr_val is not None and fr_val <= 0.0) else ('overheated_longs' if (fr_val is not None and fr_val >= 0.02) else 'neutral'),
            'stale': bool(fr_meta.get('is_fallback', False)),
        },
        {
            'key': 'etf_flows',
            'label': 'Спот ETF 7d ($M)',
            'role': 'institutional_flow',
            'value': round(etf_val, 1) if etf_val is not None else None,
            'status': 'inflow' if (etf_val is not None and etf_val > 0) else ('outflow' if (etf_val is not None and etf_val < 0) else 'neutral'),
            'stale': bool(etf_meta.get('is_fallback', False)),
        },
        {
            'key': 'dxy',
            'label': 'Індекс долара (DXY)',
            'role': 'macro',
            'value': round(dxy_val, 2) if dxy_val is not None else None,
            'observation_date': dxy_meta.get('observation_date'),
            'status': 'macro_pressure' if (dxy_val is not None and dxy_val >= 120.0) else 'neutral',
            'stale': bool(dxy_meta.get('is_fallback', False)),
        },
        {
            'key': 'm2_mom',
            'label': 'Глобальна M2 YoY (%)',
            'role': 'macro',
            'value': round(m2_val, 2) if m2_val is not None else None,
            'observation_date': m2_meta.get('observation_date'),
            'status': 'liquidity_expansion' if (m2_val is not None and m2_val >= 5.0) else ('tightening' if (m2_val is not None and m2_val < 0) else 'neutral'),
            'stale': bool(m2_meta.get('is_fallback', False)),
        },
    ]

    reasons: list[str] = []
    if data_quality.get('status') != 'valid':
        reasons.append(f"data_quality_{data_quality.get('status')}")
    if not (tail_risk and tail_risk.get('validated')):
        reasons.append('tail_risk_unvalidated')
    val_pct = mvrv_info.get('mvrv_pct')
    if val_pct is not None and val_pct <= 0.25 and wt_spread is not None and wt_spread <= -2.0:
        reasons.append('value_vs_momentum_conflict')

    return {
        'items': items,
        'abstain': len(reasons) > 0,
        'abstain_reasons': reasons,
        'status_label': 'UNCERTAIN' if reasons else 'CLEAR',
    }
