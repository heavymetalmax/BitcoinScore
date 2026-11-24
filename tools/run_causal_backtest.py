#!/usr/bin/env python3
"""Zero-Lookahead Causal Strategy & Tail-Risk Effectiveness Backtest.

Evaluates strictly causal rules on repository weekly data (scores.json +
cipherb_btcusdt_1w.json closed bars) with zero future leakage:
  - BH: Buy & Hold reference
  - S1_SMA40w: Weekly close > SMA40w / < SMA40w
  - S2_CipherB_1W: Closed weekly Cipher B (wt1 - wt2 >= +2.0 / <= -2.0)
  - S3_CipherB_ValueVeto: S2 + causal expanding MVRV rank <= 0.25 sell-veto
  - S4_CipherB_Veto_TailThrottle: S3 + 50% position sizing when causal
    13w tail-risk P(MaxDD_13w >= 25%) >= 45%
"""
from __future__ import annotations

import bisect
import datetime
import json
import math
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parent.parent


def load_weekly_dataset() -> list[dict]:
    scores = json.loads((ROOT / 'data/history/scores.json').read_text(encoding='utf-8'))
    cb_raw = json.loads((ROOT / 'data/history/cipherb_btcusdt_1w.json').read_text(encoding='utf-8'))
    cb_series = cb_raw.get('series', cb_raw) if isinstance(cb_raw, dict) else cb_raw

    # Daily price & mvrv map
    daily = {}
    for r in scores:
        d = str(r.get('date', ''))[:10]
        p = r.get('btc_price')
        if d and p is not None and float(p) > 0:
            mv = r.get('mvrv')
            daily[d] = (float(p), float(mv) if mv is not None else None)

    # Closed weekly Cipher B bars (filter out any non-Thursday / unclosed daily noise)
    cb_by_week = {}
    for r in cb_series:
        d = str(r.get('date', ''))[:10]
        if not d:
            continue
        dt = datetime.date.fromisoformat(d)
        # Standard Kraken weekly bars open on Thursday (weekday == 3)
        if dt.weekday() != 3 and not r.get('is_closed_bar'):
            continue
        cb_by_week[d] = r

    # Group daily prices into Thursday-opening weeks matching Kraken 1W bars
    weeks = []
    sorted_days = sorted(daily.keys())
    by_wk: dict[str, list[tuple[str, float, float | None]]] = {}
    for d in sorted_days:
        dt = datetime.date.fromisoformat(d)
        days_since_thu = (dt.weekday() - 3) % 7
        wk_open = (dt - datetime.timedelta(days=days_since_thu)).isoformat()
        by_wk.setdefault(wk_open, []).append((d, daily[d][0], daily[d][1]))

    # Causal expanding MVRV rank
    mvrv_buf: list[float] = []
    mvrv_pct_by_day: dict[str, float] = {}
    for d in sorted_days:
        mv = daily[d][1]
        if mv is not None:
            rank = bisect.bisect_right(mvrv_buf, mv) / len(mvrv_buf) if mvrv_buf else 0.5
            mvrv_pct_by_day[d] = rank
            bisect.insort(mvrv_buf, mv)

    for wk_open in sorted(by_wk):
        pts = by_wk[wk_open]
        if len(pts) < 4:
            continue
        prices = [x[1] for x in pts]
        cb = cb_by_week.get(wk_open, {})
        last_d = pts[-1][0]
        weeks.append({
            'week_open': wk_open,
            'last_day': last_d,
            'open': prices[0],
            'high': max(prices),
            'low': min(prices),
            'close': prices[-1],
            'mvrv_pct': mvrv_pct_by_day.get(last_d),
            'wt1': cb.get('wt1'),
            'wt2': cb.get('wt2'),
            'weekly_score': cb.get('weekly_score'),
        })

    # Add causal SMA40w, wt2 (3-week SMA of wt1 matching cipherb.py wtSmaLength=3), vol13
    closes = [w['close'] for w in weeks]
    n = len(weeks)
    ret1 = [None] + [math.log(closes[i] / closes[i - 1]) for i in range(1, n)]
    for i, w in enumerate(weeks):
        if w.get('wt2') is None and i >= 2:
            w1_win = [weeks[k].get('wt1') for k in range(i - 2, i + 1)]
            if all(x is not None for x in w1_win):
                w['wt2'] = sum(float(x) for x in w1_win) / 3.0
        w['sma40'] = sum(closes[i - 39:i + 1]) / 40.0 if i >= 39 else None
        if i >= 13:
            win = [ret1[k] for k in range(i - 12, i + 1) if ret1[k] is not None]
            if len(win) == 13:
                mu = sum(win) / 13.0
                w['vol13'] = math.sqrt(sum((x - mu) ** 2 for x in win) / 12.0)
            else:
                w['vol13'] = None
        else:
            w['vol13'] = None

    # Compute causal 13w tail-risk prediction at each week i (strictly matured labels)
    for i in range(n):
        cur_v = weeks[i]['vol13']
        if cur_v is None or i < 80:
            weeks[i]['tail_risk_pct'] = None
            continue
        as_of = datetime.date.fromisoformat(weeks[i]['week_open']) + datetime.timedelta(days=7)
        X_tr, y_tr = [], []
        for j in range(13, i - 13):
            vj = weeks[j]['vol13']
            if vj is None:
                continue
            lbl_known = datetime.date.fromisoformat(weeks[j + 13]['week_open']) + datetime.timedelta(days=7)
            if lbl_known >= as_of:
                continue
            op_n = weeks[j + 1]['open']
            min_l = min(weeks[k]['low'] for k in range(j + 1, j + 14))
            dd13 = max(0.0, 1.0 - min_l / op_n)
            X_tr.append(vj)
            y_tr.append(1 if dd13 >= 0.25 else 0)
        if len(X_tr) >= 60 and len(set(y_tr)) == 2:
            arr = np.array(X_tr, dtype=float)
            mu, sd = float(arr.mean()), max(float(arr.std()), 1e-8)
            Z_tr = np.clip((arr - mu) / sd, -4.0, 4.0).reshape(-1, 1)
            Z_cur = np.clip((cur_v - mu) / sd, -4.0, 4.0)
            clf = LogisticRegression(C=0.25, solver='lbfgs', max_iter=300)
            clf.fit(Z_tr, np.array(y_tr, dtype=int))
            weeks[i]['tail_risk_pct'] = round(float(clf.predict_proba([[Z_cur]])[0, 1]) * 100.0, 1)
        else:
            weeks[i]['tail_risk_pct'] = None

    return weeks


def run_sim(weeks: list[dict], start_date: str, mode: str, fee: float = 0.005) -> dict:
    sub = [w for w in weeks if w['week_open'] >= start_date]
    n = len(sub)
    cash = 10000.0
    btc = 0.0
    pending = None  # ('BUY', target_frac) | ('SELL', 0.0)
    eq = []
    held_w = 0.0
    closed = []
    open_entry = None
    post_exit_26 = []

    for i, w in enumerate(sub):
        if pending is not None:
            act, frac = pending
            pending = None
            op = w['open']
            if act == 'BUY' and btc == 0.0:
                invest = cash * frac
                btc = invest * (1.0 - fee) / op
                cash -= invest
                fut_lows = [sub[k]['low'] for k in range(i, min(n, i + 26))]
                dd26 = max(0.0, 1.0 - min(fut_lows) / op) * 100.0
                open_entry = {'i': i, 'date': w['week_open'], 'price': op, 'frac': frac, 'dd26': dd26}
            elif act == 'SELL' and btc > 0.0:
                cash += btc * op * (1.0 - fee)
                btc = 0.0
                ret_pct = (op / open_entry['price'] * ((1.0 - fee) ** 2) - 1.0) * 100.0
                closed.append({**open_entry, 'exit_date': w['week_open'], 'exit_price': op, 'ret_pct': ret_pct})
                if i + 26 < n:
                    post_exit_26.append((sub[i + 26]['close'] / op - 1.0) * 100.0)
                open_entry = None

        val_now = cash + btc * w['close']
        if btc > 0.0:
            held_w += (btc * w['close']) / max(val_now, 1e-8)
        eq.append(val_now)

        if mode == 'BH':
            continue

        wt1, wt2 = w.get('wt1'), w.get('wt2')
        spread = (float(wt1) - float(wt2)) if (wt1 is not None and wt2 is not None) else None
        mv_p = w.get('mvrv_pct')
        sma40 = w.get('sma40')
        tr_pct = w.get('tail_risk_pct')

        if mode == 'S1_SMA40w' and sma40 is not None:
            if btc == 0.0 and w['close'] > sma40:
                pending = ('BUY', 1.0)
            elif btc > 0.0 and w['close'] < sma40:
                pending = ('SELL', 0.0)
        elif mode in ('S2_CipherB_1W', 'S3_CipherB_ValueVeto', 'S4_CipherB_Veto_TailThrottle') and spread is not None:
            if btc == 0.0 and spread >= 2.0:
                frac = 0.5 if (mode == 'S4_CipherB_Veto_TailThrottle' and tr_pct is not None and tr_pct >= 45.0) else 1.0
                pending = ('BUY', frac)
            elif btc > 0.0 and spread <= -2.0:
                if mode == 'S2_CipherB_1W' or mv_p is None or mv_p > 0.25:
                    pending = ('SELL', 0.0)

    if mode == 'BH':
        btc = 10000.0 * (1.0 - fee) / sub[0]['open']
        eq = [btc * w['close'] for w in sub]
        held_w = float(n)

    arr = np.array([10000.0] + eq, dtype=float)
    mult = float(arr[-1] / 10000.0)
    years = n / 52.0
    cagr = (mult ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0
    dd_max = float(np.max(1.0 - arr / np.maximum.accumulate(arr)) * 100.0)
    calmar = (cagr / dd_max) if dd_max > 0 else 0.0
    wins = sum(1 for t in closed if t['ret_pct'] > 0)
    entry_dds = [t['dd26'] for t in closed] + ([open_entry['dd26']] if open_entry else [])

    return {
        'mode': mode,
        'start': start_date,
        'end': sub[-1]['week_open'],
        'weeks': n,
        'fee_pct': fee * 100.0,
        'multiple': round(mult, 2),
        'cagr_pct': round(cagr, 1),
        'max_dd_pct': round(dd_max, 1),
        'calmar': round(calmar, 2),
        'exposure_pct': round(held_w / n * 100.0, 1),
        'closed_trades': len(closed),
        'win_rate_pct': round(wins / len(closed) * 100.0, 1) if closed else None,
        'median_post_entry_dd26_pct': round(float(np.median(entry_dds)), 1) if entry_dds else None,
        'median_post_exit_btc26w_pct': round(float(np.median(post_exit_26)), 1) if post_exit_26 else None,
        'eq': eq,
    }


def block_boot_rel(eq_a: list[float], eq_b: list[float], n_boot: int = 3000) -> dict:
    a = np.array([10000.0] + eq_a, dtype=float)
    b = np.array([10000.0] + eq_b, dtype=float)
    diff = np.log(a[1:] / a[:-1]) - np.log(b[1:] / b[:-1])
    N = len(diff)
    nb = int(math.ceil(N / 13.0))
    rng = np.random.default_rng(20261124)
    vals = []
    for _ in range(n_boot):
        st = rng.integers(0, N, nb)
        ix = np.concatenate([(s + np.arange(13)) % N for s in st])[:N]
        vals.append(float(np.exp(np.sum(diff[ix]))))
    arr = np.array(vals)
    return {
        'rel_multiple': round(float(np.exp(np.sum(diff))), 2),
        'p05': round(float(np.quantile(arr, 0.05)), 2),
        'p95': round(float(np.quantile(arr, 0.95)), 2),
        'prob_gt_1': round(float(np.mean(arr > 1.0) * 100.0), 1),
    }


def main():
    weeks = load_weekly_dataset()
    modes = [
        'BH',
        'S1_SMA40w',
        'S2_CipherB_1W',
        'S3_CipherB_ValueVeto',
        'S4_CipherB_Veto_TailThrottle',
    ]
    labels = {
        'BH': 'Buy & Hold (бенчмарк)',
        'S1_SMA40w': 'S1: Тижневий тренд (SMA40w)',
        'S2_CipherB_1W': 'S2: Cipher B 1W (±2.0 гістерезис)',
        'S3_CipherB_ValueVeto': 'S3: Cipher B 1W + MVRV Value-Veto (≤25%)',
        'S4_CipherB_Veto_TailThrottle': 'S4: S3 + 50% тротлінг при TailRisk ≥45%',
    }

    windows = {}
    for wkey, start in [('full_2018_2026', '2018-01-01'), ('modern_2021_2026', '2021-01-01')]:
        res_05 = {}
        res_10 = {}
        for m in modes:
            r05 = run_sim(weeks, start, m, fee=0.005)
            r10 = run_sim(weeks, start, m, fee=0.010)
            res_05[m] = r05
            res_10[m] = r10

        rows = []
        for m in modes:
            r05 = res_05[m]
            r10 = res_10[m]
            boot = block_boot_rel(r05['eq'], res_05['BH']['eq']) if m != 'BH' else None
            row = {k: v for k, v in r05.items() if k != 'eq'}
            row['label'] = labels[m]
            row['multiple_fee10'] = r10['multiple']
            row['max_dd_fee10_pct'] = r10['max_dd_pct']
            row['boot_vs_bh'] = boot
            rows.append(row)

        windows[wkey] = {
            'start': rows[0]['start'],
            'end': rows[0]['end'],
            'weeks': rows[0]['weeks'],
            'strategies': rows,
        }

    out = {
        'generated_at': datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
        'methodology': 'Zero-lookahead causal expanding MVRV rank + closed weekly Cipher B (+/-2.0) + causal B1_vol13 tail risk. Execution: next week open, 0.5% and 1.0% one-way fees.',
        'windows': windows,
    }
    for rel in ('data/causal_backtest.json', 'web/causal_backtest.json'):
        (ROOT / rel).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
