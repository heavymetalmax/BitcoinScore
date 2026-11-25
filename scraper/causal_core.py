"""Causal 4-Output Core, D1 Dual-Sleeve State Replay & Entry/Exit Checklist.

Authoritative leakage-free engine shared identically by production scoring
(scraper/score.py) and zero-lookahead backtests (tools/run_causal_backtest.py):
  1. Causal Valuation / Bottom Score (0-100): causal expanding percentile of
     cost-basis metrics (MVRV, Puell, CVDD) + Value Sell-Veto (mvrv_pct <= 0.25).
  2. Causal D1 Dual-Sleeve State on closed Monday weekly OHLC:
       - Sleeve 1 (50%): Structural Trend (close > SMA40w / < SMA40w)
       - Sleeve 2 (50%): Weekly Cipher B (n1=9, n2=12, SMA3, +/-2.0 deadband)
                         + real MVRV Value Sell-Veto (blocks exit if mvrv_pct <= 0.25)
  3. Tail Risk 13w: calibrated P(MaxDD_13w >= 25%) on real weekly OHLC.
  4. Entry/Exit Checklist: 12 metrics with causal expanding historical ranks and
     clean abstain semantics (abstain ONLY on missing/broken data or unvalidated
     tail-risk; cheap valuation + bearish momentum is surfaced as a normal regime
     note, NOT an error).
"""
from __future__ import annotations

import bisect
import datetime
import json
import math
import os
from typing import Any

from .mvrv_ratio_store import DEFAULT_MVRV_RATIO_PATH, load_daily_mvrv_and_cap
from .ohlc_store import DEFAULT_OHLC_PATH, load_closed_weekly_ohlc


def _ema_list(vals: list[float], length: int) -> list[float]:
    alpha = 2.0 / (length + 1.0)
    out: list[float] = []
    prev = vals[0]
    for v in vals:
        prev = alpha * v + (1.0 - alpha) * prev
        out.append(prev)
    return out


def compute_cipherb_wt_on_ohlc(
    weeks: list[dict[str, Any]],
    n1: int = 9,
    n2: int = 12,
    sma_len: int = 3,
) -> tuple[list[float | None], list[float | None]]:
    """Compute causal WaveTrend (wt1, wt2 = SMA(wt1, 3)) on closed weekly OHLC."""
    n = len(weeks)
    if n == 0:
        return [], []
    hlc3 = [(w['high'] + w['low'] + w['close']) / 3.0 for w in weeks]
    esa = _ema_list(hlc3, n1)
    d = _ema_list([abs(hlc3[i] - esa[i]) for i in range(n)], n1)
    ci = [(hlc3[i] - esa[i]) / (0.015 * d[i]) if d[i] > 1e-12 else 0.0 for i in range(n)]
    wt1_raw = _ema_list(ci, n2)
    wt1: list[float | None] = [wt1_raw[i] if i >= n2 else None for i in range(n)]
    wt2: list[float | None] = [None] * n
    for i in range(n2 + sma_len - 1, n):
        win = [wt1[k] for k in range(i - sma_len + 1, i + 1)]
        if all(x is not None for x in win):
            wt2[i] = sum(float(x) for x in win) / sma_len
    return wt1, wt2


def _load_daily_mvrv_ranks(
    scores_path: str = 'data/history/scores.json',
    as_of_date: datetime.date | str | None = None,
) -> list[tuple[datetime.date, float]]:
    """Return sorted (date, causal_mvrv_pct) strictly <= as_of_date."""
    if not os.path.exists(scores_path):
        return []
    as_of_dt: datetime.date | None = None
    if isinstance(as_of_date, str):
        as_of_dt = datetime.date.fromisoformat(as_of_date[:10])
    elif isinstance(as_of_date, datetime.date):
        as_of_dt = as_of_date
    raw_by_d: dict[datetime.date, float] = {}
    try:
        for row in json.load(open(scores_path, encoding='utf-8')):
            d_str = str(row.get('date', ''))[:10]
            mv = row.get('mvrv')
            if not d_str or mv is None:
                continue
            d = datetime.date.fromisoformat(d_str)
            if as_of_dt is not None and d > as_of_dt:
                continue
            raw_by_d[d] = float(mv)
    except Exception:
        return []

    buf: list[float] = []
    out: list[tuple[datetime.date, float]] = []
    for d in sorted(raw_by_d):
        v = raw_by_d[d]
        rank = bisect.bisect_right(buf, v) / len(buf) if buf else 0.5
        out.append((d, rank))
        bisect.insort(buf, v)
    return out


def _load_daily_cvdd_series(
    scores_path: str = 'data/history/scores.json',
    as_of_date: datetime.date | str | None = None,
) -> list[tuple[datetime.date, float]]:
    """Return sorted (date, cvdd_ratio) strictly <= as_of_date."""
    if not os.path.exists(scores_path):
        return []
    as_of_dt: datetime.date | None = None
    if isinstance(as_of_date, str):
        as_of_dt = datetime.date.fromisoformat(as_of_date[:10])
    elif isinstance(as_of_date, datetime.date):
        as_of_dt = as_of_date
    raw_by_d: dict[datetime.date, float] = {}
    try:
        for row in json.load(open(scores_path, encoding='utf-8')):
            d_str = str(row.get('date', ''))[:10]
            cv = row.get('cvdd_ratio')
            if not d_str or cv is None:
                continue
            d = datetime.date.fromisoformat(d_str)
            if as_of_dt is not None and d > as_of_dt:
                continue
            raw_by_d[d] = float(cv)
    except Exception:
        return []
    return [(d, raw_by_d[d]) for d in sorted(raw_by_d)]


def compute_causal_d1_state(
    as_of_date: datetime.date | str | None = None,
    ohlc_path: str = DEFAULT_OHLC_PATH,
    scores_path: str = 'data/history/scores.json',
    mvrv_ratio_path: str = DEFAULT_MVRV_RATIO_PATH,
) -> dict[str, Any]:
    """Replay exact causal D1 Dual-Sleeve state over strictly closed weekly OHLC bars.

    Sleeve 1 (50%): close > SMA40w / < SMA40w
    Sleeve 2 (50%): weekly Cipher B (wt1 - wt2 >= +2.0 / <= -2.0) with MVRV <= 25% Value-Veto
    """
    if as_of_date is None:
        as_of = datetime.date.today()
    elif isinstance(as_of_date, str):
        as_of = datetime.date.fromisoformat(as_of_date[:10])
    else:
        as_of = as_of_date

    weeks = load_closed_weekly_ohlc(as_of_date=as_of, ohlc_path=ohlc_path)
    if len(weeks) < 40:
        return {
            'available': False,
            'sma40_in_pos': None,
            'sma40_value': None,
            'last_closed_week': None,
            'last_closed_close': None,
            'cb_in_pos': None,
            'prev_cb_in_pos': None,
            'wt1': None,
            'wt2': None,
            'wt_spread': None,
            'veto_blocked_exit': False,
            'mvrv_pct_week': None,
        }

    wt1_list, wt2_list = compute_cipherb_wt_on_ohlc(weeks)
    mvrv_ranks = _load_daily_mvrv_ranks(scores_path=scores_path, as_of_date=as_of)
    mvrv_caps = load_daily_mvrv_and_cap(as_of_date=as_of, path=mvrv_ratio_path)
    cvdd_series = _load_daily_cvdd_series(scores_path=scores_path, as_of_date=as_of)

    closes = [w['close'] for w in weeks]
    highs = [w['high'] for w in weeks]
    lows = [w['low'] for w in weeks]
    ret1: list[float | None] = [None]
    for i in range(1, len(weeks)):
        c0, c1 = closes[i - 1], closes[i]
        ret1.append(math.log(c1 / c0) if (c0 > 0 and c1 > 0) else None)

    sma40_in = False
    cb_in = False
    prev_cb_in = False
    veto_blocked = False
    mv_idx = 0
    mv_cap_idx = 0
    cv_idx = 0
    last_mv_pct: float | None = None
    last_mvrv_ratio: float | None = None
    last_rcap: float | None = None
    last_cvdd: float | None = None
    last_sma40: float | None = None
    wt1_buf: list[float] = []
    wt1_rank_last: float | None = None

    running_ath = highs[0]
    log_mvrv_hist: list[float | None] = []
    rcap_hist: list[float | None] = []
    cvdd_hist: list[float | None] = []
    amp_last: dict[str, Any] | None = None

    for i, w in enumerate(weeks):
        wk_close = w['week_close']
        while mv_idx < len(mvrv_ranks) and mvrv_ranks[mv_idx][0] <= wk_close:
            last_mv_pct = mvrv_ranks[mv_idx][1]
            mv_idx += 1
        while mv_cap_idx < len(mvrv_caps) and mvrv_caps[mv_cap_idx][0] <= wk_close:
            last_mvrv_ratio = mvrv_caps[mv_cap_idx][1]
            last_rcap = mvrv_caps[mv_cap_idx][2]
            mv_cap_idx += 1
        while cv_idx < len(cvdd_series) and cvdd_series[cv_idx][0] <= wk_close:
            last_cvdd = cvdd_series[cv_idx][1]
            cv_idx += 1

        if i >= 39:
            sma40 = sum(closes[i - 39:i + 1]) / 40.0
            last_sma40 = sma40
            if not sma40_in and w['close'] > sma40:
                sma40_in = True
            elif sma40_in and w['close'] < sma40:
                sma40_in = False

        prev_cb_in = cb_in
        veto_blocked = False
        w1, w2 = wt1_list[i], wt2_list[i]
        if w1 is not None:
            wt1_rank_last = (bisect.bisect_right(wt1_buf, float(w1)) / len(wt1_buf) * 100.0) if wt1_buf else 50.0
            bisect.insort(wt1_buf, float(w1))
        if w1 is not None and w2 is not None:
            spread = w1 - w2
            if not cb_in and spread >= 2.0:
                cb_in = True
            elif cb_in and spread <= -2.0:
                if last_mv_pct is not None and last_mv_pct <= 0.25:
                    veto_blocked = True
                else:
                    cb_in = False

        # Strictly causal M3 Structural Core:
        #   1) CostBasis4Y: rolling 208w corridor of ln(Price / Realized Price)
        #   2) CVDD_4Y: rolling 208w causal rank of CVDD ratio
        #   3) MVRV_MCapAdj: causal OLS of |ln(MVRV)| on log10(Realized Cap)
        running_ath = max(running_ath, highs[i])
        low_4y = min(lows[max(0, i - 207):i + 1])
        dd_ath_pct = (closes[i] / running_ath - 1.0) * 100.0 if running_ath > 0 else 0.0
        log_dd = math.log(closes[i] / running_ath) if (running_ath > 0 and closes[i] > 0) else 0.0

        r_win = [r for r in ret1[max(1, i - 51):i + 1] if r is not None]
        vol52 = None
        if len(r_win) >= 26:
            mu_r = sum(r_win) / len(r_win)
            var_r = sum((x - mu_r) ** 2 for x in r_win) / (len(r_win) - 1)
            vol52 = math.sqrt(max(0.0, var_r)) * math.sqrt(52.0)

        vdd = (log_dd / vol52) if (vol52 is not None and vol52 > 0.05) else None

        cur_log_mv = math.log(last_mvrv_ratio) if (last_mvrv_ratio is not None and last_mvrv_ratio > 0) else None
        log_mvrv_hist.append(cur_log_mv)
        rcap_hist.append(last_rcap)
        cvdd_hist.append(last_cvdd)

        amp_sc = 50.0
        lo_4y_ratio: float | None = None
        hi_4y_ratio: float | None = None
        realized_price: float | None = None
        if cur_log_mv is not None and last_mvrv_ratio is not None:
            win_4y = [x for x in log_mvrv_hist[max(0, i - 207):i + 1] if x is not None]
            if len(win_4y) >= 26:
                lo_4y = min(win_4y)
                hi_4y = max(win_4y)
                span_4y = hi_4y - lo_4y
                if span_4y > 1e-9:
                    amp_sc = min(100.0, max(0.0, (cur_log_mv - lo_4y) / span_4y * 100.0))
                lo_4y_ratio = math.exp(lo_4y)
                hi_4y_ratio = math.exp(hi_4y)
            realized_price = closes[i] / last_mvrv_ratio

        cvdd_4y_sc: float | None = None
        if last_cvdd is not None:
            c_win = [x for x in cvdd_hist[max(0, i - 207):i + 1] if x is not None]
            if len(c_win) >= 26:
                c_win.sort()
                cvdd_4y_sc = bisect.bisect_right(c_win, last_cvdd) / len(c_win) * 100.0

        mcap_adj_sc: float | None = None
        exp_amp: float | None = None
        if cur_log_mv is not None and last_rcap is not None and last_rcap > 1e6 and i >= 104:
            xs: list[float] = []
            ys: list[float] = []
            for k in range(max(0, i - 520), i):
                lm_k = log_mvrv_hist[k]
                rc_k = rcap_hist[k]
                if lm_k is not None and rc_k is not None and rc_k > 1e6:
                    xs.append(math.log10(rc_k))
                    ys.append(abs(lm_k))
            if len(xs) >= 52:
                mx = sum(xs) / len(xs)
                my = sum(ys) / len(ys)
                vx = sum((x - mx) ** 2 for x in xs)
                slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx if vx > 1e-9 else 0.0
                exp_amp = max(0.15, (my - slope * mx) + slope * math.log10(last_rcap))
                mcap_adj_sc = 100.0 / (1.0 + math.exp(-1.5 * (cur_log_mv / exp_amp - 0.35)))

        amp_last = {
            'method': 'm3_cost_basis_cvdd_mcap',
            'mvrv_ratio': round(last_mvrv_ratio, 3) if last_mvrv_ratio is not None else None,
            'realized_price': round(realized_price, 2) if realized_price is not None else None,
            'realized_cap_b_usd': round(last_rcap / 1e9, 1) if last_rcap is not None else None,
            'mvrv_ratio_4y_min': round(lo_4y_ratio, 3) if lo_4y_ratio is not None else None,
            'mvrv_ratio_4y_max': round(hi_4y_ratio, 3) if hi_4y_ratio is not None else None,
            'running_ath': round(running_ath, 2),
            'low_4y': round(low_4y, 2),
            'dd_from_ath_pct': round(dd_ath_pct, 1),
            'vol52_ann_pct': round(vol52 * 100.0, 1) if vol52 is not None else None,
            'vol_scaled_dd': round(vdd, 2) if vdd is not None else None,
            'amp_score': round(amp_sc, 1),
            'cvdd_4y_score': round(cvdd_4y_sc, 1) if cvdd_4y_sc is not None else None,
            'mcap_adj_score': round(mcap_adj_sc, 1) if mcap_adj_sc is not None else None,
            'expected_mvrv_amp': round(exp_amp, 3) if exp_amp is not None else None,
            'risk_adjustment_pts': round((amp_sc - 50.0) * 0.35, 1),
        }

    last_w = weeks[-1]
    w1_last, w2_last = wt1_list[-1], wt2_list[-1]
    sp_last = round(float(w1_last) - float(w2_last), 2) if (w1_last is not None and w2_last is not None) else None
    return {
        'available': True,
        'sma40_in_pos': sma40_in,
        'sma40_value': round(last_sma40, 2) if last_sma40 is not None else None,
        'last_closed_week': last_w['date'],
        'last_closed_close': round(last_w['close'], 2),
        'cb_in_pos': cb_in,
        'prev_cb_in_pos': prev_cb_in,
        'wt1': round(float(w1_last), 2) if w1_last is not None else None,
        'wt2': round(float(w2_last), 2) if w2_last is not None else None,
        'wt_spread': sp_last,
        'wt1_causal_rank': round(wt1_rank_last, 1) if wt1_rank_last is not None else None,
        'veto_blocked_exit': veto_blocked,
        'mvrv_pct_week': round(last_mv_pct, 4) if last_mv_pct is not None else None,
        'cycle_amplitude': amp_last,
    }


def soft_risk_compress(raw_score: float) -> float:
    """Smooth C1 tail compression keeping risk strictly in (1.0, 99.0).

    Leaves [15, 85] untouched and asymptotically compresses extreme tails
    instead of hard-clipping at 0 or 100, preserving monotonic ordering and
    reflecting that true cycle extremes are never known with 100% certainty.
    """
    if raw_score < 15.0:
        return 15.0 - 14.0 * math.tanh((15.0 - raw_score) / 20.0)
    if raw_score > 85.0:
        return 85.0 + 14.0 * math.tanh((raw_score - 85.0) / 20.0)
    return raw_score


def compute_causal_btcbri(
    mvrv_info: dict[str, Any] | None,
    d1_state: dict[str, Any] | None,
) -> float | None:
    """Compute unified M3 Causal BTCBRI v5.2 Index in (1.0, 99.0) with 0% lookahead.

    Pure structural 4Y + Market-Cap-Adjusted core (WT1 removed as 52w drag):
      - 40% CostBasis4Y (rolling 208w corridor of ln(Price / Realized Price))
      - 40% CVDD_4Y (rolling 208w causal rank of CVDD ratio)
      - 20% MVRV_MCapAdj (causal OLS of |ln(MVRV)| on log10(Realized Cap))
      - Smooth asymptotic tail compression in (1.0, 99.0)
    """
    amp = (d1_state or {}).get('cycle_amplitude')
    if isinstance(amp, dict) and amp.get('amp_score') is not None:
        cb4y = float(amp['amp_score'])
        cvdd4y = amp.get('cvdd_4y_score')
        mcap_adj = amp.get('mcap_adj_score')
        eff_mcap = float(mcap_adj) if mcap_adj is not None else cb4y
        if cvdd4y is not None:
            raw = 0.40 * cb4y + 0.40 * float(cvdd4y) + 0.20 * eff_mcap
        else:
            raw = 0.67 * cb4y + 0.33 * eff_mcap
        return round(soft_risk_compress(raw), 1)

    val = (mvrv_info or {}).get('valuation_score')
    if val is None:
        return None
    return round(soft_risk_compress(float(val)), 1)


def compute_causal_uncertainty(
    raw_metrics: dict[str, Any],
    mvrv_info: dict[str, Any] | None,
    d1_state: dict[str, Any] | None,
    allocation_signal: dict[str, Any] | None,
    tail_risk: dict[str, Any] | None,
    data_quality: dict[str, Any] | None,
    as_of_date: datetime.date | str | None = None,
    scores_path: str = 'data/history/scores.json',
) -> tuple[int, int, dict[str, Any]]:
    """Compute Causal v5.2 Core Uncertainty (conflict_score) & Confidence in [0, 100].

    Measures real disagreement inside the v5.2 Causal Core (without macro-cycle
    anti-phase noise from DXY/Yield Curve):
      1. Core dispersion (0..55): std across causal ranks of (MVRV, Puell, CVDD, WT1).
      2. Regime conflict (0..25): cheap valuation while D1 still 0% (+15),
         split D1 sleeves at 50% (+10), or weekly bearish divergence when MVRV >= 50% (+15).
      3. Data/validation penalty (0..40): missing/stale core metrics or unvalidated tail risk.
    """
    mvrv_val, mvrv_meta = _extract_val(raw_metrics, 'mvrv')
    puell_val, puell_meta = _extract_val(raw_metrics, 'puell_multiple')
    cvdd_val, cvdd_meta = _extract_val(raw_metrics, 'cvdd_ratio')
    ranks = compute_causal_ranks(
        {'mvrv': mvrv_val, 'puell_multiple': puell_val, 'cvdd_ratio': cvdd_val},
        as_of_date=as_of_date,
        scores_path=scores_path,
    )

    core_vals: list[float] = []
    for k in ('mvrv', 'puell_multiple', 'cvdd_ratio'):
        if ranks.get(k) is not None:
            core_vals.append(float(ranks[k]))
    wt1_rank = (d1_state or {}).get('wt1_causal_rank')
    if wt1_rank is not None:
        core_vals.append(float(wt1_rank))

    if len(core_vals) >= 2:
        mu = sum(core_vals) / len(core_vals)
        core_std = (sum((x - mu) ** 2 for x in core_vals) / len(core_vals)) ** 0.5
    else:
        core_std = 30.0
    disp_term = min(55.0, (core_std / 30.0) * 55.0)

    alloc_pct = (allocation_signal or {}).get('target_allocation_pct')
    mv_pct = (mvrv_info or {}).get('mvrv_pct')
    cb_entry = raw_metrics.get('cipherb') if isinstance(raw_metrics.get('cipherb'), dict) else {}
    cb_inner = cb_entry.get('value', cb_entry) if isinstance(cb_entry, dict) else {}
    cb_bear_div = isinstance(cb_inner, dict) and bool(
        cb_inner.get('double_bearish_div')
        or cb_inner.get('fast_bearish_div')
        or cb_inner.get('bearish_divergence')
        or cb_inner.get('bearish_div')
    )

    regime_term = 0.0
    if mv_pct is not None and float(mv_pct) <= 0.25 and alloc_pct == 0:
        regime_term += 15.0
    elif mv_pct is not None and float(mv_pct) >= 0.75 and alloc_pct == 0:
        regime_term += 10.0
    elif alloc_pct == 50:
        regime_term += 10.0
    if cb_bear_div and mv_pct is not None and float(mv_pct) >= 0.50:
        regime_term += 15.0
    regime_term = min(25.0, regime_term)

    missing_core = max(0, 4 - len(core_vals))
    stale_core = sum(
        1 for meta in (mvrv_meta, puell_meta, cvdd_meta)
        if isinstance(meta, dict) and meta.get('is_fallback')
    )
    data_term = missing_core * 10.0 + stale_core * 8.0
    if (data_quality or {}).get('status') != 'valid':
        data_term += 20.0
    if tail_risk is not None and not tail_risk.get('validated'):
        data_term += 15.0
    data_term = min(40.0, data_term)

    conflict = int(min(100, max(0, round(disp_term + regime_term + data_term))))
    confidence = int(min(100, max(0, 100 - conflict)))
    breakdown = {
        'core_std': round(core_std, 1),
        'dispersion_pts': round(disp_term, 1),
        'regime_pts': round(regime_term, 1),
        'data_penalty_pts': round(data_term, 1),
        'core_components_n': len(core_vals),
    }
    return conflict, confidence, breakdown


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
        dt = datetime.date.fromisoformat(d)
        if dt.weekday() != 3 and not row.get('is_closed_bar'):
            continue
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


def compute_causal_ranks(
    raw_values: dict[str, float | None],
    as_of_date: datetime.date | str | None = None,
    scores_path: str = 'data/history/scores.json',
) -> dict[str, float | None]:
    """Compute strictly causal expanding percentile ranks (0..100) for all history-backed metrics."""
    if as_of_date is None:
        cutoff = datetime.date.today().isoformat()
    elif isinstance(as_of_date, str):
        cutoff = as_of_date[:10]
    else:
        cutoff = as_of_date.isoformat()

    field_map = {
        'mvrv': 'mvrv',
        'puell_multiple': 'puell',
        'cvdd_ratio': 'cvdd_ratio',
        'asopr': 'asopr',
        'fear_greed': 'fear_greed',
        'funding_rate': 'funding_rate',
        'dxy': 'dxy',
        'm2_mom': 'm2_yoy',
        'mayer_multiple': 'mayer_multiple',
    }
    hist: dict[str, list[float]] = {k: [] for k in field_map}
    if os.path.exists(scores_path):
        try:
            for row in json.load(open(scores_path, encoding='utf-8')):
                d = str(row.get('date', ''))[:10]
                if not d or d >= cutoff:
                    continue
                for k, col in field_map.items():
                    v = row.get(col)
                    if v is not None:
                        hist[k].append(float(v))
        except Exception:
            pass

    out: dict[str, float | None] = {}
    for k in field_map:
        v_now = raw_values.get(k)
        buf = hist[k]
        if v_now is None or len(buf) < 60:
            out[k] = None
            continue
        buf.sort()
        rank = bisect.bisect_right(buf, float(v_now)) / len(buf)
        out[k] = round(rank * 100.0, 1)
    return out


def compute_causal_mvrv_rank(
    mvrv_today: float | None,
    as_of_date: datetime.date | str | None = None,
    scores_path: str = 'data/history/scores.json',
    puell_today: float | None = None,
    cvdd_today: float | None = None,
) -> dict[str, Any]:
    """Compute strictly causal expanding percentile rank of MVRV + Cost-Basis Bottom Score (< as_of_date)."""
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
            'mvrv_rank_pct': None,
            'mvrv_pct': None,
            'value_sell_veto': False,
            'history_n': len(hist_vals),
        }

    hist_vals.sort()
    rank = bisect.bisect_right(hist_vals, float(mvrv_today)) / len(hist_vals)
    mvrv_score = round(rank * 100.0, 1)

    # Physical cost-basis composite (MVRV + Puell + CVDD) when optional values supplied
    cb_ranks = [mvrv_score]
    if puell_today is not None or cvdd_today is not None:
        extra = compute_causal_ranks(
            {'puell_multiple': puell_today, 'cvdd_ratio': cvdd_today},
            as_of_date=cutoff,
            scores_path=scores_path,
        )
        for ek in ('puell_multiple', 'cvdd_ratio'):
            if extra.get(ek) is not None:
                cb_ranks.append(float(extra[ek]))

    val_score = round(sum(cb_ranks) / len(cb_ranks), 1)
    return {
        'valuation_score': val_score,
        'mvrv_rank_pct': mvrv_score,
        'mvrv_pct': round(rank, 4),
        'value_sell_veto': rank <= 0.25,
        'cost_basis_components': len(cb_ranks),
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


def _rank_status(
    pct: float | None,
    low_lbl: str,
    high_lbl: str,
    fallback_low: bool = False,
    fallback_high: bool = False,
) -> str:
    if pct is not None:
        if pct <= 25.0:
            return low_lbl
        if pct >= 75.0:
            return high_lbl
        return 'neutral'
    if fallback_low:
        return low_lbl
    if fallback_high:
        return high_lbl
    return 'neutral'


def build_entry_exit_checklist(
    raw_metrics: dict[str, Any],
    mvrv_info: dict[str, Any],
    tail_risk: dict[str, Any] | None,
    data_quality: dict[str, Any],
    as_of_date: datetime.date | str | None = None,
    scores_path: str = 'data/history/scores.json',
    d1_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build transparent confirmation checklist with causal ranks and clean abstain state."""
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
    if d1_state and d1_state.get('available') and d1_state.get('wt_spread') is not None:
        wt_spread = float(d1_state['wt_spread'])
    elif wt1 is not None and wt2 is not None:
        wt_spread = round(float(wt1) - float(wt2), 2)
    else:
        wt_spread = None

    ranks = compute_causal_ranks(
        {
            'mvrv': mvrv_val,
            'puell_multiple': puell_val,
            'cvdd_ratio': cvdd_val,
            'asopr': asopr_val,
            'fear_greed': fg_val,
            'funding_rate': fr_val,
            'dxy': dxy_val,
            'm2_mom': m2_val,
            'mayer_multiple': mayer_val,
        },
        as_of_date=as_of_date,
        scores_path=scores_path,
    )

    m2_obs_date = m2_meta.get('observation_date') or (
        raw_metrics.get('m2', {}).get('observation_date')
        if isinstance(raw_metrics.get('m2'), dict) else None
    )
    cb_bear_div = bool(
        cb_val.get('double_bearish_div')
        or cb_val.get('fast_bearish_div')
        or cb_val.get('bearish_divergence')
        or cb_val.get('bearish_div')
    )
    cb_bull_div = bool(
        cb_val.get('double_bullish_div')
        or cb_val.get('fast_bullish_div')
        or cb_val.get('bullish_divergence')
        or cb_val.get('bullish_div')
    )
    cb_div_status = 'bearish_caution' if cb_bear_div else ('bullish_confirm' if cb_bull_div else 'neutral')

    mvrv_rank = mvrv_info.get('mvrv_rank_pct')
    if mvrv_rank is None:
        mvrv_rank = ranks.get('mvrv')
    sma40_in = d1_state.get('sma40_in_pos') if (d1_state and d1_state.get('available')) else None

    items = [
        {
            'key': 'mvrv',
            'label': 'Собівартість (MVRV Z)',
            'role': 'core_valuation',
            'value': round(mvrv_val, 3) if mvrv_val is not None else None,
            'causal_pct': mvrv_rank,
            'status': _rank_status(mvrv_rank, 'value_zone', 'overheated'),
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
            'label': 'Тренд SMA40w / 200DMA (Mayer)',
            'role': 'core_trend',
            'value': round(mayer_val, 3) if mayer_val is not None else None,
            'causal_pct': ranks.get('mayer_multiple'),
            'status': (
                'bull_trend' if sma40_in is True
                else ('value_zone' if (ranks.get('mayer_multiple') is not None and ranks['mayer_multiple'] <= 25.0)
                      else ('bear_trend' if sma40_in is False else _rank_status(ranks.get('mayer_multiple'), 'value_zone', 'overheated')))
            ),
            'stale': False,
        },
        {
            'key': 'puell_multiple',
            'label': 'Майнери (Puell)',
            'role': 'confirmation',
            'value': round(puell_val, 3) if puell_val is not None else None,
            'causal_pct': ranks.get('puell_multiple'),
            'status': _rank_status(ranks.get('puell_multiple'), 'value_zone', 'overheated'),
            'stale': bool(puell_meta.get('is_fallback', False)),
        },
        {
            'key': 'cvdd_ratio',
            'label': 'Підлога циклу (CVDD)',
            'role': 'confirmation',
            'value': round(cvdd_val, 3) if cvdd_val is not None else None,
            'causal_pct': ranks.get('cvdd_ratio'),
            'status': _rank_status(ranks.get('cvdd_ratio'), 'value_zone', 'overheated'),
            'stale': bool(cvdd_meta.get('is_fallback', False)),
        },
        {
            'key': 'asopr',
            'label': 'Реаліз. прибуток (aSOPR)',
            'role': 'confirmation',
            'value': round(asopr_val, 4) if asopr_val is not None else None,
            'causal_pct': ranks.get('asopr'),
            'status': 'capitulation' if (asopr_val is not None and asopr_val < 1.0) else ('profit_recovery' if (asopr_val is not None and asopr_val >= 1.0) else 'neutral'),
            'stale': bool(asopr_meta.get('is_fallback', False)),
        },
        {
            'key': 'fear_greed',
            'label': 'Fear & Greed (7d)',
            'role': 'sentiment',
            'value': round(fg_val, 1) if fg_val is not None else None,
            'causal_pct': ranks.get('fear_greed'),
            'status': _rank_status(ranks.get('fear_greed'), 'extreme_fear', 'extreme_greed', fallback_low=(fg_val is not None and fg_val <= 25), fallback_high=(fg_val is not None and fg_val >= 75)),
            'stale': bool(fg_meta.get('is_fallback', False)),
        },
        {
            'key': 'funding_rate',
            'label': 'Фандінг 7d (%)',
            'role': 'leverage',
            'value': round(fr_val, 5) if fr_val is not None else None,
            'causal_pct': ranks.get('funding_rate'),
            'status': _rank_status(ranks.get('funding_rate'), 'short_fear', 'overheated_longs', fallback_low=(fr_val is not None and fr_val <= 0.0), fallback_high=(fr_val is not None and fr_val >= 0.02)),
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
            'causal_pct': ranks.get('dxy'),
            'observation_date': dxy_meta.get('observation_date'),
            'status': _rank_status(ranks.get('dxy'), 'macro_tailwind', 'macro_pressure'),
            'stale': bool(dxy_meta.get('is_fallback', False)),
        },
        {
            'key': 'm2_mom',
            'label': 'Глобальна M2 YoY (%)',
            'role': 'macro',
            'value': round(m2_val, 2) if m2_val is not None else None,
            'causal_pct': ranks.get('m2_mom'),
            'observation_date': m2_obs_date,
            'status': _rank_status(ranks.get('m2_mom'), 'tightening', 'liquidity_expansion'),
            'stale': bool(m2_meta.get('is_fallback', False)),
        },
        {
            'key': 'cipherb_1w_div',
            'label': 'Cipher B (1W Дивергенції)',
            'role': 'core_timing',
            'value': 'BEAR_DIV' if cb_bear_div else ('BULL_DIV' if cb_bull_div else ('NONE' if cb_val else None)),
            'status': cb_div_status,
            'stale': False,
        },
    ]

    # Clean abstain semantics: ONLY broken/missing data or unvalidated risk model
    reasons: list[str] = []
    available_count = sum(1 for it in items if it.get('value') is not None)
    core_missing = any(
        it.get('value') is None
        for it in items
        if it.get('key') in ('mvrv', 'cipherb_1w', 'mayer_multiple')
    )
    if available_count < 6 or core_missing:
        reasons.append('insufficient_metrics')
    if data_quality.get('status') != 'valid':
        reasons.append(f"data_quality_{data_quality.get('status')}")
    if not (tail_risk and tail_risk.get('validated')):
        reasons.append('tail_risk_unvalidated')

    # Non-error regime notes (normal cyclical market states)
    regime_notes: list[str] = []
    val_pct = mvrv_info.get('mvrv_pct')
    if val_pct is not None and val_pct <= 0.25 and wt_spread is not None and wt_spread <= -2.0:
        regime_notes.append('cheap_falling_momentum')
    if cb_bear_div:
        regime_notes.append('weekly_bearish_divergence')

    return {
        'items': items,
        'abstain': len(reasons) > 0,
        'abstain_reasons': reasons,
        'regime_notes': regime_notes,
        'status_label': 'UNCERTAIN' if reasons else 'CLEAR',
    }
