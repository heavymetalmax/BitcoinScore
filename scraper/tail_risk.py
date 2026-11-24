"""Causal 13-week Tail Risk Classifier (replaces V5B).

Active model (B1_vol13):
  1-factor L2 logistic regression on causal 13-week realized weekly log-return
  volatility, trained strictly on matured 13-week drawdown labels
  (label_known_at < as_of_date) for P(MaxDD_13w >= 25%).

Shadow model (M2_vol_mvrv):
  2-factor L2 logistic regression (vol13 + log(MVRV)) logged to pit_ledger.jsonl
  for prospective PiT evaluation without affecting active gates.
"""
from __future__ import annotations

import datetime
import json
import math
import os
from typing import Any


def _load_daily_series(
    scores_path: str = 'data/history/scores.json',
    btc_price_today: float | None = None,
    mvrv_today: float | None = None,
    as_of_date: datetime.date | None = None,
) -> list[tuple[datetime.date, float, float | None]]:
    """Load sorted (date, btc_price, mvrv) daily rows up to as_of_date."""
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
                mv_f = float(mv) if (mv is not None and float(mv) > 0) else None
                by_date[d] = (float(p), mv_f)
        except Exception:
            pass

    if as_of_date is not None and btc_price_today is not None and float(btc_price_today) > 0:
        mv_t = float(mvrv_today) if (mvrv_today is not None and float(mvrv_today) > 0) else None
        prev = by_date.get(as_of_date)
        by_date[as_of_date] = (float(btc_price_today), mv_t if mv_t is not None else (prev[1] if prev else None))

    return [(d, v[0], v[1]) for d, v in sorted(by_date.items())]


def _to_monday_weekly(
    daily: list[tuple[datetime.date, float, float | None]],
) -> list[dict[str, Any]]:
    """Aggregate daily prices/MVRV into Monday-opening weekly bars."""
    buckets: dict[datetime.date, list[tuple[datetime.date, float, float | None]]] = {}
    for d, p, mv in daily:
        wk_open = d - datetime.timedelta(days=d.weekday())
        buckets.setdefault(wk_open, []).append((d, p, mv))

    weeks: list[dict[str, Any]] = []
    for wk_open in sorted(buckets):
        pts = buckets[wk_open]
        prices = [x[1] for x in pts]
        mvs = [x[2] for x in pts if x[2] is not None]
        weeks.append({
            'week_open': wk_open,
            'open': prices[0],
            'high': max(prices),
            'low': min(prices),
            'close': prices[-1],
            'mvrv': mvs[-1] if mvs else None,
        })
    return weeks


def predict_tail_risk_13w(
    btc_price_today: float | None = None,
    mvrv_today: float | None = None,
    target_date: datetime.date | str | None = None,
    scores_path: str = 'data/history/scores.json',
) -> dict[str, Any]:
    """Compute causal P(MaxDD_13w >= 25%) in [0, 100] from matured weekly history."""
    if target_date is None:
        as_of = datetime.date.today()
    elif isinstance(target_date, str):
        as_of = datetime.date.fromisoformat(target_date[:10])
    else:
        as_of = target_date

    daily = _load_daily_series(
        scores_path=scores_path,
        btc_price_today=btc_price_today,
        mvrv_today=mvrv_today,
        as_of_date=as_of,
    )
    weeks = _to_monday_weekly(daily)
    n = len(weeks)
    if n < 80:
        return {
            'score': None,
            'prob_dd25_13w_pct': None,
            'validated': False,
            'method': 'insufficient_history',
            'shadow_m2_pct': None,
            'train_n': 0,
            'vol13_ann_pct': None,
        }

    # Compute 13-week realized weekly log-return volatility
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

    # Build strictly matured training rows (label_known_at < as_of)
    X_tr_1: list[float] = []
    y_tr: list[int] = []
    X_tr_2: list[tuple[float, float]] = []
    y_tr_2: list[int] = []

    for i in range(13, n - 13):
        v = vol13[i]
        if v is None:
            continue
        label_end = weeks[i + 13]['week_open']
        label_known = label_end + datetime.timedelta(days=7)
        if label_known >= as_of:
            continue
        op_next = weeks[i + 1]['open']
        if op_next <= 0:
            continue
        min_low = min(weeks[k]['low'] for k in range(i + 1, i + 14))
        dd13 = max(0.0, 1.0 - min_low / op_next)
        lbl = 1 if dd13 >= 0.25 else 0
        X_tr_1.append(v)
        y_tr.append(lbl)

        mv = weeks[i]['mvrv']
        if mv is not None and mv > 0:
            X_tr_2.append((v, math.log(mv)))
            y_tr_2.append(lbl)

    cur_v = vol13[-1]
    if cur_v is None or len(X_tr_1) < 60 or len(set(y_tr)) < 2:
        return {
            'score': None,
            'prob_dd25_13w_pct': None,
            'validated': False,
            'method': 'insufficient_history',
            'shadow_m2_pct': None,
            'train_n': len(X_tr_1),
            'vol13_ann_pct': round(cur_v * math.sqrt(52) * 100.0, 1) if cur_v is not None else None,
        }

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        return {
            'score': None,
            'prob_dd25_13w_pct': None,
            'validated': False,
            'method': 'sklearn_unavailable',
            'shadow_m2_pct': None,
            'train_n': len(X_tr_1),
            'vol13_ann_pct': round(cur_v * math.sqrt(52) * 100.0, 1),
        }

    arr1 = np.array(X_tr_1, dtype=float)
    mu1 = float(np.mean(arr1))
    sd1 = max(float(np.std(arr1)), 1e-8)
    Z1_tr = np.clip((arr1 - mu1) / sd1, -4.0, 4.0).reshape(-1, 1)
    Z1_cur = np.clip((cur_v - mu1) / sd1, -4.0, 4.0)

    clf1 = LogisticRegression(C=0.25, solver='lbfgs', max_iter=300)
    clf1.fit(Z1_tr, np.array(y_tr, dtype=int))
    prob1 = float(clf1.predict_proba(np.array([[Z1_cur]], dtype=float))[0, 1]) * 100.0

    # Shadow M2 (vol13 + log_mvrv) logged for prospective PiT verification
    shadow_m2: float | None = None
    cur_mv = weeks[-1]['mvrv']
    if cur_mv is not None and cur_mv > 0 and len(X_tr_2) >= 60 and len(set(y_tr_2)) == 2:
        arr2 = np.array(X_tr_2, dtype=float)
        mu2 = arr2.mean(axis=0)
        sd2 = np.maximum(arr2.std(axis=0), 1e-8)
        Z2_tr = np.clip((arr2 - mu2) / sd2, -4.0, 4.0)
        Z2_cur = np.clip((np.array([cur_v, math.log(cur_mv)]) - mu2) / sd2, -4.0, 4.0).reshape(1, -1)
        clf2 = LogisticRegression(C=0.25, solver='lbfgs', max_iter=300)
        clf2.fit(Z2_tr, np.array(y_tr_2, dtype=int))
        shadow_m2 = round(float(clf2.predict_proba(Z2_cur)[0, 1]) * 100.0, 1)

    score_rounded = round(prob1, 1)
    return {
        'score': score_rounded,
        'prob_dd25_13w_pct': score_rounded,
        'validated': True,
        'method': 'causal_vol13_logistic',
        'shadow_m2_pct': shadow_m2,
        'train_n': len(X_tr_1),
        'vol13_ann_pct': round(cur_v * math.sqrt(52) * 100.0, 1),
    }
