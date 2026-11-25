"""Causal 13-week Tail Risk Classifier (replaces legacy V5B).

Active model (B1_vol13):
  1-factor L2 logistic regression on causal 13-week realized weekly log-return
  volatility over strictly CLOSED Monday-opening weekly OHLC bars
  (data/history/btc_weekly_ohlc.json, real H/L/O/C since 2011), trained strictly
  on matured 13-week drawdown labels (label_known_at < as_of).
  `validated=True` is set ONLY if expanding purged OOS walk-forward beats
  prevalence (BSS > 0) with acceptable calibration (ECE_5bin <= 0.08, n_oos >= 52).

Shadow model (M2_vol_mvrv):
  2-factor L2 logistic regression (vol13 + standardized MVRV Z-score without
  log-filtering, preserving all negative/zero bottom readings) logged to
  pit_ledger.jsonl for prospective PiT evaluation.
"""
from __future__ import annotations

import datetime
import json
import math
import os
from typing import Any

from .ohlc_store import DEFAULT_OHLC_PATH, load_closed_weekly_ohlc


def _load_daily_series(
    scores_path: str = 'data/history/scores.json',
    btc_price_today: float | None = None,
    mvrv_today: float | None = None,
    as_of_date: datetime.date | None = None,
) -> list[tuple[datetime.date, float, float | None]]:
    """Load sorted (date, btc_price, mvrv_z_score) daily rows up to as_of_date."""
    by_date: dict[datetime.date, tuple[float, float | None]] = {}
    if os.path.exists(scores_path):
        try:
            rows = json.load(open(scores_path, encoding='utf-8'))
            for r in rows:
                d_str = str(r.get('date', ''))[:10]
                p = r.get('btc_price')
                if not d_str or p is None or float(p) <= 0:
                    continue
                d = datetime.date.fromisoformat(d_str)
                if as_of_date is not None and d > as_of_date:
                    continue
                mv = r.get('mvrv')
                mv_f = float(mv) if mv is not None else None
                by_date[d] = (float(p), mv_f)
        except Exception:
            pass

    if as_of_date is not None and btc_price_today is not None and float(btc_price_today) > 0:
        mv_t = float(mvrv_today) if mvrv_today is not None else None
        prev = by_date.get(as_of_date)
        by_date[as_of_date] = (float(btc_price_today), mv_t if mv_t is not None else (prev[1] if prev else None))

    return [(d, v[0], v[1]) for d, v in sorted(by_date.items())]


def _load_mvrv_by_date(
    scores_path: str = 'data/history/scores.json',
    as_of_date: datetime.date | None = None,
) -> list[tuple[datetime.date, float]]:
    """Load sorted (date, mvrv_z_score) strictly <= as_of_date."""
    if not os.path.exists(scores_path):
        return []
    out: dict[datetime.date, float] = {}
    try:
        for r in json.load(open(scores_path, encoding='utf-8')):
            d_str = str(r.get('date', ''))[:10]
            mv = r.get('mvrv')
            if not d_str or mv is None:
                continue
            d = datetime.date.fromisoformat(d_str)
            if as_of_date is not None and d > as_of_date:
                continue
            out[d] = float(mv)
    except Exception:
        return []
    return sorted(out.items())


def _to_closed_monday_weekly(
    daily: list[tuple[datetime.date, float, float | None]],
    as_of_date: datetime.date,
) -> list[dict[str, Any]]:
    """Fallback: aggregate daily closes into CLOSED Monday-opening weekly bars."""
    buckets: dict[datetime.date, list[tuple[datetime.date, float, float | None]]] = {}
    for d, p, mv in daily:
        wk_open = d - datetime.timedelta(days=d.weekday())
        buckets.setdefault(wk_open, []).append((d, p, mv))

    weeks: list[dict[str, Any]] = []
    for wk_open in sorted(buckets):
        wk_close = wk_open + datetime.timedelta(days=6)
        if wk_close >= as_of_date:
            continue
        pts = buckets[wk_open]
        if len(pts) < 4:
            continue
        prices = [x[1] for x in pts]
        mvs = [x[2] for x in pts if x[2] is not None]
        weeks.append({
            'week_open': wk_open,
            'week_close': wk_close,
            'open': prices[0],
            'high': max(prices),
            'low': min(prices),
            'close': prices[-1],
            'mvrv_z': mvs[-1] if mvs else None,
        })
    return weeks


def _load_dxy_by_date(
    scores_path: str = 'data/history/scores.json',
    as_of_date: datetime.date | None = None,
) -> list[tuple[datetime.date, float]]:
    """Load sorted (date, dxy) strictly <= as_of_date."""
    if not os.path.exists(scores_path):
        return []
    out: dict[datetime.date, float] = {}
    try:
        for r in json.load(open(scores_path, encoding='utf-8')):
            d_str = str(r.get('date', ''))[:10]
            v = r.get('dxy')
            if not d_str or v is None or float(v) <= 0:
                continue
            d = datetime.date.fromisoformat(d_str)
            if as_of_date is not None and d > as_of_date:
                continue
            out[d] = float(v)
    except Exception:
        return []
    return sorted(out.items())


def _attach_causal_dxy(weeks: list[dict[str, Any]], dxy_daily: list[tuple[datetime.date, float]]) -> None:
    """Attach latest DXY observed strictly <= week_close to each weekly bar."""
    idx = 0
    last_v: float | None = None
    for w in weeks:
        wk_close = w['week_close']
        while idx < len(dxy_daily) and dxy_daily[idx][0] <= wk_close:
            last_v = dxy_daily[idx][1]
            idx += 1
        w['dxy'] = last_v


def _attach_causal_mvrv(weeks: list[dict[str, Any]], mvrv_daily: list[tuple[datetime.date, float]]) -> None:
    """Attach latest MVRV Z-score observed strictly <= week_close to each weekly bar."""
    idx = 0
    last_mv: float | None = None
    for w in weeks:
        wk_close = w['week_close']
        while idx < len(mvrv_daily) and mvrv_daily[idx][0] <= wk_close:
            last_mv = mvrv_daily[idx][1]
            idx += 1
        w['mvrv_z'] = last_mv


def _ece_5bin(y_true, y_prob) -> float:
    import numpy as np

    qs = np.quantile(y_prob, np.linspace(0.0, 1.0, 6))
    qs[0] -= 1e-9
    qs[-1] += 1e-9
    ece = 0.0
    n = len(y_true)
    for lo, hi in zip(qs[:-1], qs[1:]):
        m = (y_prob > lo) & (y_prob <= hi)
        if np.any(m):
            ece += (np.sum(m) / n) * abs(float(np.mean(y_prob[m])) - float(np.mean(y_true[m])))
    return float(ece)


def predict_tail_risk_13w(
    btc_price_today: float | None = None,
    mvrv_today: float | None = None,
    target_date: datetime.date | str | None = None,
    scores_path: str = 'data/history/scores.json',
    ohlc_path: str | None = DEFAULT_OHLC_PATH,
) -> dict[str, Any]:
    """Compute causal P(MaxDD_13w >= 25%) in [0, 100] with real OOS validation."""
    if target_date is None:
        as_of = datetime.date.today()
    elif isinstance(target_date, str):
        as_of = datetime.date.fromisoformat(target_date[:10])
    else:
        as_of = target_date

    weeks: list[dict[str, Any]] = []
    ohlc_source = 'daily_close_resampled'

    if ohlc_path and os.path.exists(ohlc_path):
        weeks = load_closed_weekly_ohlc(as_of_date=as_of, ohlc_path=ohlc_path)
        if weeks:
            ohlc_source = 'weekly_ohlc_archive'
            mvrv_daily = _load_mvrv_by_date(scores_path=scores_path, as_of_date=as_of)
            _attach_causal_mvrv(weeks, mvrv_daily)
            dxy_daily = _load_dxy_by_date(scores_path=scores_path, as_of_date=as_of)
            _attach_causal_dxy(weeks, dxy_daily)
    else:
        dxy_daily = _load_dxy_by_date(scores_path=scores_path, as_of_date=as_of)

    if not weeks:
        daily = _load_daily_series(
            scores_path=scores_path,
            btc_price_today=btc_price_today,
            mvrv_today=mvrv_today,
            as_of_date=as_of,
        )
        weeks = _to_closed_monday_weekly(daily, as_of_date=as_of)
        ohlc_source = 'daily_close_resampled'

    n = len(weeks)
    if n < 80:
        return {
            'score': None,
            'prob_dd25_13w_pct': None,
            'validated': False,
            'method': 'insufficient_history',
            'ohlc_source': ohlc_source,
            'shadow_m2_pct': None,
            'train_n': 0,
            'vol13_ann_pct': None,
            'last_closed_week': weeks[-1]['week_open'].isoformat() if weeks else None,
            'oos_validation': None,
        }

    ret1: list[float | None] = [None]
    for i in range(1, n):
        c0, c1 = weeks[i - 1]['close'], weeks[i]['close']
        ret1.append(math.log(c1 / c0) if (c0 > 0 and c1 > 0) else None)

    vol13: list[float | None] = [None] * n
    for i in range(13, n):
        win = [ret1[k] for k in range(i - 12, i + 1) if ret1[k] is not None]
        if len(win) == 13:
            mu_r = sum(win) / 13.0
            var_r = sum((x - mu_r) ** 2 for x in win) / 12.0
            vol13[i] = math.sqrt(max(0.0, var_r))

    matured_rows: list[dict[str, Any]] = []
    for i in range(13, n - 13):
        v = vol13[i]
        if v is None:
            continue
        label_end = weeks[i + 13]['week_open']
        label_known = label_end + datetime.timedelta(days=7)
        if label_known > as_of:
            continue
        op_next = weeks[i + 1]['open']
        if op_next <= 0:
            continue
        min_low = min(weeks[k]['low'] for k in range(i + 1, i + 14))
        dd13 = max(0.0, 1.0 - min_low / op_next)
        dxy_i = weeks[i].get('dxy')
        dxy_13 = weeks[i - 13].get('dxy') if i >= 13 else None
        dxy_roc_13 = math.log(dxy_i / dxy_13) if (dxy_i and dxy_13 and dxy_i > 0 and dxy_13 > 0) else None
        matured_rows.append({
            'week_open': weeks[i]['week_open'],
            'label_known': label_known,
            'vol13': v,
            'mvrv_z': weeks[i].get('mvrv_z'),
            'dxy_roc_13': dxy_roc_13,
            'y': 1 if dd13 >= 0.25 else 0,
        })

    cur_v = vol13[-1]
    last_wk_str = weeks[-1]['week_open'].isoformat()
    if cur_v is None or len(matured_rows) < 60 or len({r['y'] for r in matured_rows}) < 2:
        return {
            'score': None,
            'prob_dd25_13w_pct': None,
            'validated': False,
            'method': 'insufficient_history',
            'ohlc_source': ohlc_source,
            'shadow_m2_pct': None,
            'train_n': len(matured_rows),
            'vol13_ann_pct': round(cur_v * math.sqrt(52) * 100.0, 1) if cur_v is not None else None,
            'last_closed_week': last_wk_str,
            'oos_validation': None,
        }

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import average_precision_score
    except ImportError:
        return {
            'score': None,
            'prob_dd25_13w_pct': None,
            'validated': False,
            'method': 'sklearn_unavailable',
            'ohlc_source': ohlc_source,
            'shadow_m2_pct': None,
            'train_n': len(matured_rows),
            'vol13_ann_pct': round(cur_v * math.sqrt(52) * 100.0, 1),
            'last_closed_week': last_wk_str,
            'oos_validation': None,
        }

    # Expanding purged OOS validation (from 2017 when real OHLC archive starts in 2011, else +2y warmup)
    oos_y: list[int] = []
    oos_p: list[float] = []
    oos_b0: list[float] = []
    first_yr = matured_rows[0]['week_open'].year
    start_yr = max(first_yr + 2, 2017) if ohlc_source == 'weekly_ohlc_archive' else (first_yr + 2)
    end_yr = matured_rows[-1]['week_open'].year
    for yr in range(start_yr, end_yr + 1):
        cutoff = datetime.date(yr, 1, 1)
        nxt = datetime.date(yr + 1, 1, 1)
        tr = [r for r in matured_rows if r['label_known'] < cutoff]
        te = [r for r in matured_rows if cutoff <= r['week_open'] < nxt]
        if len(tr) < 52 or not te or len({r['y'] for r in tr}) < 2:
            continue
        x_tr = np.array([r['vol13'] for r in tr], dtype=float)
        y_tr_arr = np.array([r['y'] for r in tr], dtype=int)
        mu_f, sd_f = float(x_tr.mean()), max(float(x_tr.std()), 1e-8)
        Z_tr_f = np.clip((x_tr - mu_f) / sd_f, -4.0, 4.0).reshape(-1, 1)
        clf_f = LogisticRegression(C=0.25, solver='lbfgs', max_iter=300)
        clf_f.fit(Z_tr_f, y_tr_arr)

        x_te = np.array([r['vol13'] for r in te], dtype=float)
        Z_te_f = np.clip((x_te - mu_f) / sd_f, -4.0, 4.0).reshape(-1, 1)
        p_te = clf_f.predict_proba(Z_te_f)[:, 1]
        oos_y.extend(r['y'] for r in te)
        oos_p.extend(float(p) for p in p_te)
        oos_b0.extend([float(y_tr_arr.mean())] * len(te))

    oos_summary: dict[str, Any] | None = None
    validated = False
    if len(oos_y) >= 52 and len(set(oos_y)) == 2:
        ya = np.array(oos_y, dtype=float)
        pa = np.array(oos_p, dtype=float)
        b0 = np.array(oos_b0, dtype=float)
        br_mod = float(np.mean((pa - ya) ** 2))
        br_b0 = float(np.mean((b0 - ya) ** 2))
        bss = (1.0 - br_mod / br_b0) if br_b0 > 0 else 0.0
        ece = _ece_5bin(ya, pa)
        pr_auc = float(average_precision_score(ya, pa))
        validated = bool(bss > 0.0 and ece <= 0.08)
        oos_summary = {
            'n_oos': len(oos_y),
            'brier': round(br_mod, 5),
            'prevalence_brier': round(br_b0, 5),
            'bss_vs_prevalence': round(bss, 4),
            'pr_auc': round(pr_auc, 4),
            'ece_5bin': round(ece, 4),
            'passed': validated,
        }

    # Final fit on all matured rows
    arr1 = np.array([r['vol13'] for r in matured_rows], dtype=float)
    y_all = np.array([r['y'] for r in matured_rows], dtype=int)
    mu1 = float(np.mean(arr1))
    sd1 = max(float(np.std(arr1)), 1e-8)
    Z1_tr = np.clip((arr1 - mu1) / sd1, -4.0, 4.0).reshape(-1, 1)
    Z1_cur = np.clip((cur_v - mu1) / sd1, -4.0, 4.0)

    clf1 = LogisticRegression(C=0.25, solver='lbfgs', max_iter=300)
    clf1.fit(Z1_tr, y_all)
    prob1 = float(clf1.predict_proba(np.array([[Z1_cur]], dtype=float))[0, 1]) * 100.0

    # Shadow M2 (vol13 + standardized mvrv_z_score — keeps all negative/zero Z-scores)
    shadow_m2: float | None = None
    cur_mv = weeks[-1].get('mvrv_z')
    m2_rows = [r for r in matured_rows if r['mvrv_z'] is not None]
    if cur_mv is not None and len(m2_rows) >= 60 and len({r['y'] for r in m2_rows}) == 2:
        arr2 = np.array([[r['vol13'], r['mvrv_z']] for r in m2_rows], dtype=float)
        y2 = np.array([r['y'] for r in m2_rows], dtype=int)
        mu2 = arr2.mean(axis=0)
        sd2 = np.maximum(arr2.std(axis=0), 1e-8)
        Z2_tr = np.clip((arr2 - mu2) / sd2, -4.0, 4.0)
        Z2_cur = np.clip((np.array([cur_v, float(cur_mv)]) - mu2) / sd2, -4.0, 4.0).reshape(1, -1)
        clf2 = LogisticRegression(C=0.25, solver='lbfgs', max_iter=300)
        clf2.fit(Z2_tr, y2)
        shadow_m2 = round(float(clf2.predict_proba(Z2_cur)[0, 1]) * 100.0, 1)

    # Macro DXY 13w momentum & SMA20w pressure (causal; IC_13w = -0.385 vs BTC return)
    dxy_momentum: dict[str, Any] | None = None
    shadow_vol_dxy: float | None = None
    dxy_wk_vals = [w.get('dxy') for w in weeks]
    if dxy_daily and len(dxy_wk_vals) >= 20 and all(x is not None for x in dxy_wk_vals[-20:]):
        dxy_now = float(dxy_daily[-1][1])
        dxy_sma20 = sum(float(x) for x in dxy_wk_vals[-20:]) / 20.0
        dxy_13ago = float(dxy_wk_vals[-13])
        vs_sma_pct = (dxy_now / dxy_sma20 - 1.0) * 100.0 if dxy_sma20 > 0 else 0.0
        roc13_pct = (dxy_now / dxy_13ago - 1.0) * 100.0 if dxy_13ago > 0 else 0.0
        dxy_momentum = {
            'dxy_now': round(dxy_now, 2),
            'dxy_sma20w': round(dxy_sma20, 2),
            'dxy_vs_sma20w_pct': round(vs_sma_pct, 2),
            'dxy_roc_13w_pct': round(roc13_pct, 2),
            'pressure_active': bool(dxy_now > dxy_sma20 and roc13_pct > 0.0),
            'as_of': dxy_daily[-1][0].isoformat(),
        }
        cur_dxy_roc = math.log(dxy_now / dxy_13ago) if dxy_13ago > 0 else None
        md_rows = [r for r in matured_rows if r.get('dxy_roc_13') is not None]
        if cur_dxy_roc is not None and len(md_rows) >= 60 and len({r['y'] for r in md_rows}) == 2:
            arr_d = np.array([[r['vol13'], r['dxy_roc_13']] for r in md_rows], dtype=float)
            y_d = np.array([r['y'] for r in md_rows], dtype=int)
            mu_d = arr_d.mean(axis=0)
            sd_d = np.maximum(arr_d.std(axis=0), 1e-8)
            Zd_tr = np.clip((arr_d - mu_d) / sd_d, -4.0, 4.0)
            Zd_cur = np.clip((np.array([cur_v, cur_dxy_roc]) - mu_d) / sd_d, -4.0, 4.0).reshape(1, -1)
            clfd = LogisticRegression(C=0.25, solver='lbfgs', max_iter=300)
            clfd.fit(Zd_tr, y_d)
            shadow_vol_dxy = round(float(clfd.predict_proba(Zd_cur)[0, 1]) * 100.0, 1)

    score_rounded = round(prob1, 1)
    return {
        'score': score_rounded,
        'prob_dd25_13w_pct': score_rounded,
        'validated': validated,
        'method': 'causal_vol13_logistic',
        'ohlc_source': ohlc_source,
        'shadow_m2_pct': shadow_m2,
        'shadow_vol_dxy_pct': shadow_vol_dxy,
        'dxy_momentum': dxy_momentum,
        'train_n': len(matured_rows),
        'vol13_ann_pct': round(cur_v * math.sqrt(52) * 100.0, 1),
        'last_closed_week': last_wk_str,
        'oos_validation': oos_summary,
    }
