#!/usr/bin/env python3
"""Zero-Lookahead Production-Parity Causal Strategy Backtest & Pattern Generator.

Runs the EXACT same closed Monday-opening weekly OHLC archive
(data/history/btc_weekly_ohlc.json, real H/L/O/C since 2011), WaveTrend
calculation (compute_cipherb_wt_on_ohlc, SMA3), causal MVRV/Puell/CVDD ranks
(data/history/scores.json), and vol-scaled Cycle Amplitude used by live
production (scraper/causal_core.py + scraper/allocation_engine.py).

Outputs BOTH:
  - data/causal_backtest.json     & web/causal_backtest.json
  - data/strategy_patterns.json   & web/strategy_patterns.json
"""
from __future__ import annotations

import bisect
import datetime
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.causal_core import compute_causal_weekly_history
from scraper.tail_risk import predict_tail_risk_13w


def load_weekly_dataset(as_of_date: datetime.date | None = None) -> list[dict]:
    scores_path = ROOT / 'data/history/scores.json'
    if as_of_date is None:
        scores = json.loads(scores_path.read_text(encoding='utf-8'))
        as_of_date = datetime.date.fromisoformat(str(scores[-1]['date'])[:10])

    return compute_causal_weekly_history(
        as_of_date=as_of_date,
        ohlc_path=str(ROOT / 'data/history/btc_weekly_ohlc.json'),
        scores_path=str(scores_path),
        mvrv_ratio_path=str(ROOT / 'data/history/mvrv_ratio_history.json'),
    )


def run_sim(weeks: list[dict], start_date: str, mode: str, fee: float = 0.005, end_date: str | None = None) -> dict:
    sub = [w for w in weeks if w['week_open'] >= start_date and (end_date is None or w['week_open'] <= end_date)]
    n = len(sub)
    cash = 10000.0
    btc = 0.0
    cur_target = 0.0
    pending_target: float | None = None

    s1_in = False
    s2_in = False
    v52_in = False

    eq: list[float] = []
    peak_eq = 10000.0
    dd_intrawk = 0.0
    held_w = 0.0
    rebalances = 0
    closed_trades = 0
    wins = 0
    entry_eq: float | None = None
    entry_dds: list[float] = []
    last_trade: dict | None = None

    for i, w in enumerate(sub):
        op, hi, lo, cl = w['open'], w['high'], w['low'], w['close']

        if mode == 'BH' and i == 0:
            btc = cash * (1.0 - fee) / op
            cash = 0.0
            cur_target = 1.0
            last_trade = {'date': w['week_open'], 'action': 'BUY', 'price': op, 'bri': w.get('causal_v52')}

        if pending_target is not None:
            eq_open = cash + btc * op
            desired_btc_val = eq_open * pending_target
            cur_btc_val = btc * op
            delta = desired_btc_val - cur_btc_val
            if abs(delta) > 1e-6:
                rebalances += 1
                act = 'BUY' if delta > 0 else 'SELL'
                last_trade = {'date': w['week_open'], 'action': act, 'price': op, 'bri': w.get('causal_v52')}
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

        v52 = w.get('causal_v52')
        if v52 is not None:
            if not v52_in and v52 <= 20.0:
                v52_in = True
            elif v52_in and v52 >= 80.0:
                v52_in = False

        if mode == 'S1_SMA40w':
            nxt = 1.0 if s1_in else 0.0
        elif mode in ('S2_CipherB_1W', 'S3_CipherB_ValueVeto'):
            nxt = 1.0 if s2_in else 0.0
        elif mode == 'D1_Causal_DualSleeve':
            nxt = (0.5 if s1_in else 0.0) + (0.5 if s2_in else 0.0)
        elif mode == 'V52_Index_Threshold':
            nxt = 1.0 if v52_in else 0.0
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
        'return_pct': round((mult - 1.0) * 100.0, 1),
        'cagr_pct': round(cagr, 1),
        'max_dd_pct': round(dd_iw, 1),
        'max_dd_close_pct': round(dd_close, 1),
        'calmar': round(calmar, 2),
        'exposure_pct': round(held_w / n * 100.0, 1),
        'closed_trades': closed_trades,
        'trades': rebalances,
        'rebalances': rebalances,
        'win_rate_pct': round(wins / closed_trades * 100.0, 1) if closed_trades else None,
        'median_post_entry_dd26_pct': round(float(np.median(entry_dds)), 1) if entry_dds else None,
        'end_alloc_pct': round(cur_target * 100.0, 1),
        'last_trade': last_trade,
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


def build_strategy_patterns(weeks: list[dict], gen_ts: str) -> dict:
    specs = [
        {
            'mode': 'D1_Causal_DualSleeve',
            'name': 'Режим 50 / 50 (Тренд + Імпульс)',
            'family': 'Hybrid',
            'role_tag': 'ОСНОВНИЙ · 50/50',
            'rule_short': '50% тижневий тренд SMA40w + 50% Cipher B 1W (±2.0) із захистом дна MVRV ≤ 25%',
            'live_spec': {'type': 'causal_50_50'},
        },
        {
            'mode': 'S1_SMA40w',
            'name': 'Режим 100 / 100 (Один ордер SMA40w)',
            'family': 'Trend',
            'role_tag': 'ПРОСТИЙ · 100/100',
            'rule_short': 'Одномоментний вхід 100% при закритті тижня > SMA40w та повний вихід < SMA40w',
            'live_spec': {'type': 'causal_100_100'},
        },
        {
            'mode': 'S3_CipherB_ValueVeto',
            'name': 'Cipher B 1W + Захист дна MVRV',
            'family': 'Cipher B',
            'role_tag': 'ІМПУЛЬС + ЗАХИСТ',
            'rule_short': 'Вхід 100% по 1W (wt1−wt2) ≥ +2.0; вихід ≤ −2.0 лише якщо MVRV ранг > 25%',
            'live_spec': {'type': 'causal_s3'},
        },
        {
            'mode': 'S2_CipherB_1W',
            'name': 'Чистий Cipher B 1W (±2.0 гістерезис)',
            'family': 'Cipher B',
            'role_tag': 'ІМПУЛЬС',
            'rule_short': 'Вхід 100% по 1W (wt1−wt2) ≥ +2.0; вихід при ≤ −2.0 без фільтра собівартості',
            'live_spec': {'type': 'causal_s2'},
        },
        {
            'mode': 'V52_Index_Threshold',
            'name': 'Індекс v5.2 (Пороги ≤ 25 / ≥ 75)',
            'family': 'Index v5.2',
            'role_tag': 'ТЕРМОМЕТР',
            'rule_short': 'Вхід 100% коли Індекс v5.2 (з амплітудою) ≤ 25; вихід коли ≥ 75',
            'live_spec': {'type': 'causal_v52_idx', 'buy': 25, 'sell': 75},
        },
        {
            'mode': 'BH',
            'name': 'Buy & Hold (Бенчмарк)',
            'family': 'Benchmark',
            'role_tag': 'БЕНЧМАРК',
            'rule_short': 'Постійне утримання 100% BTC без ребалансів',
            'live_spec': {'type': 'causal_bh'},
        },
    ]

    top_patterns = []
    for sp in specs:
        m = sp['mode']
        r_full = run_sim(weeks, '2017-01-01', m, fee=0.005)
        r_mod = run_sim(weeks, '2018-01-01', m, fee=0.005)
        r_c1 = run_sim(weeks, '2018-01-01', m, fee=0.005, end_date='2022-06-15')
        r_c2 = run_sim(weeks, '2022-06-16', m, fee=0.005)
        r_6y = run_sim(weeks, '2021-01-01', m, fee=0.005)

        def clean(r):
            return {k: v for k, v in r.items() if k != 'eq'}

        top_patterns.append({
            'mode': m,
            'name': sp['name'],
            'family': sp['family'],
            'role_tag': sp['role_tag'],
            'rule_short': sp['rule_short'],
            'live_spec': sp['live_spec'],
            'full': clean(r_full),
            'modern_2018': clean(r_mod),
            'cycle1': clean(r_c1),
            'cycle2': clean(r_c2),
            'pool_6y': clean(r_6y),
            'pool_full': clean(r_full),
            'last_trade': r_mod.get('last_trade'),
        })

    last_w = weeks[-1]
    return {
        'generated_at': gen_ts,
        'methodology': {
            'zero_lookahead': True,
            'ohlc_source': 'data/history/btc_weekly_ohlc.json',
            'execution_lag': 't+1 Monday open',
            'fee_per_side_pct': 0.5,
            'strategies_tested': len(top_patterns),
        },
        'latest_snapshot': {
            'date': last_w['week_open'],
            'price': last_w['close'],
            'causal_v52': last_w.get('causal_v52'),
            'w_diff': round(last_w['wt1'] - last_w['wt2'], 2) if (last_w['wt1'] is not None and last_w['wt2'] is not None) else None,
        },
        'top_patterns': top_patterns,
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
        'V52_Index_Threshold',
    ]
    labels = {
        'BH': 'Buy & Hold (бенчмарк)',
        'D1_Causal_DualSleeve': 'Режим 50/50 (Поетапний: Тренд SMA40w + Імпульс Cipher B & Захист дна)',
        'S1_SMA40w': 'Режим 100/100 (Один ордер: Тижневий тренд SMA40w)',
        'S2_CipherB_1W': 'Лише Cipher B 1W (SMA3, ±2.0 гістерезис)',
        'S3_CipherB_ValueVeto': 'Cipher B 1W + Захист дна MVRV (≤25%)',
        'V52_Index_Threshold': 'Індекс v5.2 (Пороги ≤20 / ≥80)',
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

    gen_ts = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    out = {
        'generated_at': gen_ts,
        'methodology': 'Production-parity zero-lookahead test on closed Monday weekly OHLC (2011–2026, real H/L/O/C) + causal MVRV/Puell/CVDD + vol-scaled amplitude + WaveTrend SMA3. Execution: next-week open (t+1), true intra-week MaxDD, 0.5% and 1.0% one-way fees.',
        'ohlc_bars': len(weeks),
        'tail_risk_oos': tail_oos.get('oos_validation'),
        'windows': windows,
    }
    for rel in ('data/causal_backtest.json', 'web/causal_backtest.json'):
        (ROOT / rel).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')

    patterns_out = build_strategy_patterns(weeks, gen_ts)
    for rel in ('data/strategy_patterns.json', 'web/strategy_patterns.json'):
        (ROOT / rel).write_text(json.dumps(patterns_out, indent=2, ensure_ascii=False), encoding='utf-8')

    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
