#!/usr/bin/env python3
"""Zero-Lookahead Production-Parity Causal Strategy Backtest.

Runs the EXACT same closed Monday-opening weekly OHLC archive
(data/history/btc_weekly_ohlc.json, real H/L/O/C since 2011), WaveTrend
calculation (compute_cipherb_wt_on_ohlc, SMA3), and causal MVRV percentile
rank (data/history/scores.json) used by live production (scraper/causal_core.py
+ scraper/allocation_engine.py):

  - BH: Buy & Hold benchmark
  - S1_SMA40w: Weekly close > SMA40w / < SMA40w (100% sleeve)
  - S2_CipherB_1W: Closed weekly Cipher B (wt1 - wt2 >= +2.0 / <= -2.0)
  - S3_CipherB_ValueVeto: S2 + causal expanding MVRV rank <= 0.25 sell-veto
  - D1_Causal_DualSleeve (ACTIVE ENGINE): 50% S1_SMA40w + 50% S3_CipherB_ValueVeto

Execution: strictly next-week open (t+1), true intra-week high/low MaxDD,
0.5% and 1.0% one-way fees, 13-week circular block bootstrap vs Buy & Hold.
"""
from __future__ import annotations

import datetime
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.causal_core import _load_daily_mvrv_ranks, compute_cipherb_wt_on_ohlc
from scraper.ohlc_store import load_closed_weekly_ohlc
from scraper.tail_risk import predict_tail_risk_13w


def load_weekly_dataset(as_of_date: datetime.date | None = None) -> list[dict]:
    if as_of_date is None:
        scores = json.loads((ROOT / 'data/history/scores.json').read_text(encoding='utf-8'))
        as_of_date = datetime.date.fromisoformat(str(scores[-1]['date'])[:10])

    weeks = load_closed_weekly_ohlc(
        as_of_date=as_of_date,
        ohlc_path=str(ROOT / 'data/history/btc_weekly_ohlc.json'),
    )
    wt1, wt2 = compute_cipherb_wt_on_ohlc(weeks)
    mvrv_ranks = _load_daily_mvrv_ranks(
        scores_path=str(ROOT / 'data/history/scores.json'),
        as_of_date=as_of_date,
    )

    closes = [w['close'] for w in weeks]
    mv_idx = 0
    last_mv: float | None = None
    out: list[dict] = []
    for i, w in enumerate(weeks):
        wk_close = w['week_close']
        while mv_idx < len(mvrv_ranks) and mvrv_ranks[mv_idx][0] <= wk_close:
            last_mv = mvrv_ranks[mv_idx][1]
            mv_idx += 1
        sma40 = sum(closes[i - 39:i + 1]) / 40.0 if i >= 39 else None
        out.append({
            'week_open': w['date'],
            'week_close': wk_close.isoformat(),
            'open': w['open'],
            'high': w['high'],
            'low': w['low'],
            'close': w['close'],
            'sma40': sma40,
            'wt1': wt1[i],
            'wt2': wt2[i],
            'mvrv_pct': last_mv,
        })
    return out


def run_sim(weeks: list[dict], start_date: str, mode: str, fee: float = 0.005) -> dict:
    sub = [w for w in weeks if w['week_open'] >= start_date]
    n = len(sub)
    cash = 10000.0
    btc = 0.0
    cur_target = 0.0
    pending_target: float | None = None

    s1_in = False
    s2_in = False

    eq: list[float] = []
    peak_eq = 10000.0
    dd_intrawk = 0.0
    held_w = 0.0
    rebalances = 0
    closed_trades = 0
    wins = 0
    entry_eq: float | None = None
    entry_dds: list[float] = []

    for i, w in enumerate(sub):
        op, hi, lo, cl = w['open'], w['high'], w['low'], w['close']

        if mode == 'BH' and i == 0:
            btc = cash * (1.0 - fee) / op
            cash = 0.0
            cur_target = 1.0

        if pending_target is not None:
            eq_open = cash + btc * op
            desired_btc_val = eq_open * pending_target
            cur_btc_val = btc * op
            delta = desired_btc_val - cur_btc_val
            if abs(delta) > 1e-6:
                rebalances += 1
                if cur_target == 0.0 and pending_target > 0.0:
                    entry_eq = eq_open
                    fut_lows = [sub[k]['low'] for k in range(i, min(n, i + 26))]
                    entry_dds.append(max(0.0, 1.0 - min(fut_lows) / op) * 100.0)
                if delta > 0:
                    spend = min(cash, delta)
                    btc += spend * (1.0 - fee) / op
                    cash -= spend
                else:
                    sell_val = min(cur_btc_val, -delta)
                    btc -= sell_val / op
                    cash += sell_val * (1.0 - fee)
                if pending_target == 0.0 and entry_eq is not None:
                    closed_trades += 1
                    if (cash + btc * op) > entry_eq:
                        wins += 1
                    entry_eq = None
            cur_target = pending_target
            pending_target = None

        eq_hi = cash + btc * hi
        eq_lo = cash + btc * lo
        eq_cl = cash + btc * cl
        peak_eq = max(peak_eq, eq_hi)
        dd_intrawk = max(dd_intrawk, 1.0 - eq_lo / peak_eq)
        eq.append(eq_cl)
        held_w += (btc * cl) / max(eq_cl, 1e-8)

        if mode == 'BH':
            continue

        sma40 = w['sma40']
        if sma40 is not None:
            if not s1_in and cl > sma40:
                s1_in = True
            elif s1_in and cl < sma40:
                s1_in = False

        wt1, wt2 = w['wt1'], w['wt2']
        spread = (wt1 - wt2) if (wt1 is not None and wt2 is not None) else None
        mv_p = w['mvrv_pct']
        if spread is not None:
            if not s2_in and spread >= 2.0:
                s2_in = True
            elif s2_in and spread <= -2.0:
                if mode == 'S2_CipherB_1W' or mv_p is None or mv_p > 0.25:
                    s2_in = False

        if mode == 'S1_SMA40w':
            nxt = 1.0 if s1_in else 0.0
        elif mode in ('S2_CipherB_1W', 'S3_CipherB_ValueVeto'):
            nxt = 1.0 if s2_in else 0.0
        elif mode == 'D1_Causal_DualSleeve':
            nxt = (0.5 if s1_in else 0.0) + (0.5 if s2_in else 0.0)
        else:
            raise ValueError(mode)

        if abs(nxt - cur_target) > 1e-9:
            pending_target = nxt

    arr = np.array([10000.0] + eq, dtype=float)
    mult = float(arr[-1] / 10000.0)
    years = n / 52.0
    cagr = (mult ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0
    dd_close = float(np.max(1.0 - arr / np.maximum.accumulate(arr)) * 100.0)
    dd_iw = dd_intrawk * 100.0
    calmar = (cagr / dd_iw) if dd_iw > 0 else 0.0

    return {
        'mode': mode,
        'start': sub[0]['week_open'],
        'end': sub[-1]['week_open'],
        'weeks': n,
        'fee_pct': fee * 100.0,
        'multiple': round(mult, 2),
        'cagr_pct': round(cagr, 1),
        'max_dd_pct': round(dd_iw, 1),
        'max_dd_close_pct': round(dd_close, 1),
        'calmar': round(calmar, 2),
        'exposure_pct': round(held_w / n * 100.0, 1),
        'closed_trades': closed_trades,
        'rebalances': rebalances,
        'win_rate_pct': round(wins / closed_trades * 100.0, 1) if closed_trades else None,
        'median_post_entry_dd26_pct': round(float(np.median(entry_dds)), 1) if entry_dds else None,
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
    tail_oos = predict_tail_risk_13w(
        target_date=datetime.date.fromisoformat(weeks[-1]['week_close']) + datetime.timedelta(days=1),
        scores_path=str(ROOT / 'data/history/scores.json'),
        ohlc_path=str(ROOT / 'data/history/btc_weekly_ohlc.json'),
    )
    modes = [
        'BH',
        'D1_Causal_DualSleeve',
        'S1_SMA40w',
        'S2_CipherB_1W',
        'S3_CipherB_ValueVeto',
    ]
    labels = {
        'BH': 'Buy & Hold (бенчмарк)',
        'D1_Causal_DualSleeve': 'D1 (АКТИВНИЙ): 50% SMA40w + 50% Cipher B & MVRV Veto',
        'S1_SMA40w': 'S1: Тижневий тренд (SMA40w)',
        'S2_CipherB_1W': 'S2: Cipher B 1W (SMA3, ±2.0 гістерезис)',
        'S3_CipherB_ValueVeto': 'S3: Cipher B 1W + MVRV Value-Veto (≤25%)',
    }

    windows = {}
    for wkey, start in [('full_2018_2026', '2017-01-01'), ('modern_2021_2026', '2021-01-01')]:
        res_05 = {m: run_sim(weeks, start, m, fee=0.005) for m in modes}
        res_10 = {m: run_sim(weeks, start, m, fee=0.010) for m in modes}
        rows = []
        for m in modes:
            r05, r10 = res_05[m], res_10[m]
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
        'methodology': 'Production-parity zero-lookahead test on closed Monday weekly OHLC (2011–2026, real H/L/O/C) + causal MVRV rank + WaveTrend SMA3. Execution: next-week open (t+1), true intra-week MaxDD, 0.5% and 1.0% one-way fees.',
        'ohlc_bars': len(weeks),
        'tail_risk_oos': tail_oos.get('oos_validation'),
        'windows': windows,
    }
    for rel in ('data/causal_backtest.json', 'web/causal_backtest.json'):
        (ROOT / rel).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
