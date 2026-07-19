#!/usr/bin/env python3
"""V4 signal test drive: compare against ideal trader and buy-and-hold.

Loads historical data from scores.json, runs V4 scoring for each day,
then simulates three strategies:
  1. Buy-and-hold BTC
  2. Ideal trader (buys at confirmed BOTTOM, sells at confirmed TOP from cycle_extremes.json)
  3. V4 signal strategy (buy when score ≤ BUY_THR, sell when score ≥ SELL_THR)

Optimised: pre-loads HMM pickle once, calls normalize_metric per row, avoids
re-reading scores.json on every iteration. Runs ~3000 days in ~60s.
"""
import sys, os, json, datetime, pickle, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from scraper.normalizer import normalize_metric
from scraper.cycle_normalizer import price_cycle_percentile
from scraper.score import _map_pi_gap, _wavg, BASKET_OC, BASKET_MS, BASKET_MC, BASKET_CP
from scraper.utility_evaluator import (
    evaluate_all_utilities_continuous, blend_phase_with_cycle_prior, halving_cycle_day_for
)
from scraper.bottom_confluence import compute_bottom_confluence, load_calibration as _load_bc_cal, BOTTOM_PHASE_THRESHOLD
from scraper.tiz import compute_tiz_causal
from scraper.scoring import _oc_coherence
from tools.train_v3_hmm_model import HMMPhaseClassifier  # noqa — needed for pickle

BUY_THR   = 25
SELL_THR  = 75
START     = '2018-12-01'   # start just before confirmed 2018 bottom
CAPITAL   = 10_000.0

# Major confirmed cycle extremes only (no 2018 mini-bounces).
# Ideal trader catches THESE moves — realistic cycle-level benchmark.
IDEAL_TRADES = [
    # (date, action, expected_price_approx)
    ('2018-12-15', 'BUY'),   # confirmed 2018 bottom
    ('2019-06-26', 'SELL'),  # 2019 local top
    ('2020-03-12', 'BUY'),   # COVID crash bottom
    ('2021-04-13', 'SELL'),  # Spring 2021 ATH
    ('2021-07-20', 'BUY'),   # Summer 2021 dip
    ('2021-11-08', 'SELL'),  # Nov 2021 ATH
    ('2022-11-21', 'BUY'),   # FTX / cycle bottom
    ('2025-10-06', 'SELL'),  # 2025 ATH
]
IDEAL_SIGNALS = {d: a for d, a in IDEAL_TRADES}

# ── Load calibration constants ────────────────────────────────────────────────
_cal = json.load(open('data/v3_calibration.json'))
_cd  = _cal['coherence_dampening']
_tiz_cfg = _cal['tiz']
_dxy_cfg = _cal['dxy_modifier']

# ── Pre-load HMM model once ───────────────────────────────────────────────────
_HMM_PIPELINE = None
_MODEL_PATH = 'data/v3_phase_model.pkl'
if os.path.exists(_MODEL_PATH):
    class _Unpickler(pickle.Unpickler):
        def find_class(self, module, name):
            if name == 'HMMPhaseClassifier':
                return HMMPhaseClassifier
            return super().find_class(module, name)
    with open(_MODEL_PATH, 'rb') as f:
        _HMM_PIPELINE = _Unpickler(f).load()['pipeline']

_HMM_METRIC_ORDER = [
    'nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'mayer_multiple',
    'asopr', 'etf_flows', 'cipherb_weekly', 'cipherb_daily', 'fear_greed', 'm2_yoy',
]


def _fast_score(row: dict, scores_history: list, norm_cache: dict, prev_norm: dict | None) -> dict | None:
    """Compute V4 score for a scores.json row. Returns None if insufficient data."""
    date_str = row['date']
    td = datetime.date.fromisoformat(date_str)
    btc_price = row.get('btc_price')

    # ── Normalize ─────────────────────────────────────────────────────────────
    n = {}
    _KEY_MAP = {
        'nupl': 'nupl', 'mvrv': 'mvrv_z_score', 'rhodl_ratio': 'rhodl_ratio',
        'cvdd_ratio': 'cvdd_ratio', 'asopr': 'asopr', 'puell': 'puell',
        'mayer_multiple': 'mayer_multiple', 'fear_greed': 'fear_greed',
        'm2_yoy': 'm2_yoy', 'yield_curve_spread': 'yield_curve_spread',
        'etf_flows': 'etf_flows', 'funding_rate': 'funding_rate', 'dxy': 'dxy',
        'lth_supply': 'lth_supply',
    }
    for raw_k, norm_k in _KEY_MAP.items():
        val = row.get(raw_k)
        n[norm_k] = normalize_metric(norm_k, val, td) if val is not None else None

    pi_raw = row.get('pi_gap_pct')
    n['pi_gap'] = _map_pi_gap(pi_raw)

    cb_daily = row.get('cipherb_daily')
    cb_weekly = row.get('cipherb_weekly') or cb_daily
    n['cipherb'] = round(cb_weekly) if cb_weekly is not None else None
    n['cipherb_weekly'] = round(cb_weekly) if cb_weekly is not None else None
    n['cipherb_daily']  = round(cb_daily)  if cb_daily  is not None else None
    n['btc_price_cycle'] = price_cycle_percentile(td, btc_price)

    # Need at least NUPL + MVRV to score
    if n.get('nupl') is None and n.get('mvrv_z_score') is None:
        return None

    # ── Phase weights (HMM + cycle prior) ────────────────────────────────────
    w_top = w_bot = 0.0
    if _HMM_PIPELINE is not None and prev_norm is not None:
        vec = []
        for m in _HMM_METRIC_ORDER:
            vec.append(n.get(m))
        for m in _HMM_METRIC_ORDER:
            s_now  = n.get(m)
            s_prev = prev_norm.get(m)
            vec.append(s_now - s_prev if (s_now is not None and s_prev is not None) else None)
        row_arr = [v if v is not None else float('nan') for v in vec]
        probs = _HMM_PIPELINE.predict_proba(np.array([row_arr], dtype=float))[0]
        w_top = float(probs[2])

    try:
        bc = _load_bc_cal()
        confluence = compute_bottom_confluence(n, bc)
        if confluence is not None:
            w_bot = confluence / 100.0
    except Exception:
        pass

    total = w_bot + w_top
    if total > 1.0:
        w_bot /= total; w_top /= total; w_neutral = 0.0
    else:
        w_neutral = max(0.0, 1.0 - total)

    w_bot, w_neutral, w_top = blend_phase_with_cycle_prior(w_bot, w_neutral, w_top, td)

    # ── TiZ ──────────────────────────────────────────────────────────────────
    tiz_score, tiz_days, tiz_cal = compute_tiz_causal(scores_history, td)
    tiz_maturity = round(tiz_days / tiz_cal, 3) if tiz_days > 0 else None

    # ── Utilities ─────────────────────────────────────────────────────────────
    utilities = evaluate_all_utilities_continuous(n, w_top, w_bot, w_neutral, tiz_maturity)

    # ── V4 baskets ────────────────────────────────────────────────────────────
    OC = _wavg(BASKET_OC, n, utilities)
    MS = _wavg(BASKET_MS, n, utilities)
    MC = _wavg(BASKET_MC, n, utilities)
    CP = _wavg(BASKET_CP, n, utilities)

    CP_safe = CP if CP is not None else 50
    OC_read = OC * (0.60 + 0.40 * CP_safe / 100) if OC is not None else None

    w_mc = w_bot * 0.05 + w_neutral * 0.25 + w_top * 0.05
    if OC_read is not None:
        F_s = (1.0 - w_mc) * OC_read + w_mc * MC if MC is not None else OC_read
    elif MC is not None:
        F_s = float(MC)
    else:
        F_s = 50.0

    if tiz_score is not None:
        tw = _tiz_cfg['weight']
        F_s = (1.0 - tw * w_bot) * F_s + tw * w_bot * tiz_score

    if OC is not None:
        F_s = max(F_s, 0.70 * OC)

    oc_coh = _oc_coherence(n)
    coh_fl = _cd['bottom_coh_floor'] * w_bot + _cd['neutral_coh_floor'] * w_neutral + _cd['top_coh_floor'] * w_top
    neu_s  = _cd['bottom_neutral_target'] * w_bot + _cd['neutral_neutral_target'] * w_neutral + _cd['top_neutral_target'] * w_top
    coh_f  = coh_fl + (1.0 - coh_fl) * oc_coh
    F_s    = neu_s + (F_s - neu_s) * coh_f

    div   = max(0, CP_safe - OC) if OC is not None else 0
    w_ms  = w_bot * 0.30 + w_neutral * 0.60 + w_top * 0.80
    w_div = w_top * 0.40
    denom = w_ms + w_div
    if MS is not None and denom > 0:
        F_v = (w_ms * MS + w_div * div) / denom
    elif MS is not None:
        F_v = float(MS)
    else:
        F_v = float(div) if div > 0 else 50.0

    pb  = w_bot * 0.20 + w_neutral * 0.50 + w_top * 1.00
    F   = F_s + (100.0 - F_s) * (F_v / 100.0) * pb

    dxy = n.get('dxy')
    if dxy is not None:
        if dxy > _dxy_cfg['high']:
            F -= min(_dxy_cfg['max_adj'], (dxy - _dxy_cfg['high']) * _dxy_cfg['scale'])
        elif dxy < _dxy_cfg['low']:
            F += min(_dxy_cfg['max_adj'], (_dxy_cfg['low'] - dxy) * _dxy_cfg['scale'])

    if pi_raw is not None and float(pi_raw) <= 0:
        F = max(F, 85)

    return {
        'date': date_str,
        'score': max(0, min(100, round(F))),
        'btc_price': btc_price,
        'w_bot': round(w_bot, 3),
        'w_top': round(w_top, 3),
        'OC': round(OC) if OC is not None else None,
        'MS': round(MS) if MS is not None else None,
        'CP': round(CP) if CP is not None else None,
        'F_structural': round(F_s, 1),
    }


def _fmt_pct(v, decimals=1):
    sign = '+' if v >= 0 else ''
    return f'{sign}{v:.{decimals}f}%'


def simulate(results, buy_thr, sell_thr, min_tiz, min_wtop_sell):
    """Fast simulation on precomputed results list. Returns (return_pct, max_dd, trades)."""
    cash, btc = CAPITAL, 0.0
    peak = CAPITAL
    max_dd = 0.0
    trades = []
    days_in_zone = 0

    for r in results:
        score = r['score']
        p     = r['btc_price']
        w_top = r['w_top']
        if p is None:
            continue

        if score <= buy_thr:
            days_in_zone += 1
        else:
            days_in_zone = 0

        if score <= buy_thr and days_in_zone >= min_tiz and cash > 10:
            btc = cash / p; cash = 0.0
            trades.append(('B', r['date'], p, score, w_top))
            days_in_zone = 0

        elif score >= sell_thr and w_top >= min_wtop_sell and btc > 0:
            cash = btc * p; btc = 0.0
            trades.append(('S', r['date'], p, score, w_top))

        val = cash + btc * p
        if val > peak: peak = val
        dd = (peak - val) / peak
        if dd > max_dd: max_dd = dd

    final = cash + btc * results[-1]['btc_price']
    ret = (final - CAPITAL) / CAPITAL * 100
    return ret, max_dd, trades


def run():
    # ── Load data ─────────────────────────────────────────────────────────────
    print('Loading scores.json...')
    all_rows = json.load(open('data/history/scores.json', encoding='utf-8'))
    scored_rows = [
        r for r in all_rows
        if r.get('btc_price') is not None and r.get('nupl') is not None and r['date'] >= START
    ]
    scored_rows.sort(key=lambda r: r['date'])

    scores_history = [
        (r['date'], r.get('final_score'), r.get('phase'), r.get('w_bot'))
        for r in all_rows if r.get('date')
    ]
    print(f'  {len(scored_rows)} rows with raw metrics from {scored_rows[0]["date"]} to {scored_rows[-1]["date"]}')

    # ── Ideal trader signal map (uses nearest available price day) ────────────
    ideal_signals = {d: a for d, a in IDEAL_SIGNALS.items() if d >= START}

    # ── Run V4 scoring ────────────────────────────────────────────────────────
    print('\nScoring all dates with V4 engine (pre-loaded HMM)...')
    results = []
    norm_cache = {}
    prev_norm = None

    for i, row in enumerate(scored_rows):
        if i % 200 == 0:
            print(f'  {i}/{len(scored_rows)}  {row["date"]}')

        res = _fast_score(row, scores_history, norm_cache, prev_norm)
        if res is not None:
            results.append(res)
        prev_norm = res  # use score dict as prev for next day HMM delta

    print(f'  Scored {len(results)} dates')

    # ── Simulate ──────────────────────────────────────────────────────────────
    results.sort(key=lambda r: r['date'])
    p0    = results[0]['btc_price']
    p_end = results[-1]['btc_price']

    # Buy-and-hold
    bh_peak = p0; bh_max_dd = 0.0
    for r in results:
        p = r['btc_price']
        if not p: continue
        if p > bh_peak: bh_peak = p
        dd = (bh_peak - p) / bh_peak
        if dd > bh_max_dd: bh_max_dd = dd
    bh_return = (p_end - p0) / p0 * 100

    # Ideal trader
    it_cash, it_btc = CAPITAL, 0.0; it_trades = []
    price_by_date = {r['date']: r['btc_price'] for r in results if r['btc_price']}
    for target_date, action in sorted(ideal_signals.items()):
        td = datetime.date.fromisoformat(target_date); p = None
        for off in range(0, 4):
            for sign in (0, 1, -1):
                d = (td + datetime.timedelta(days=off * sign)).isoformat()
                if d in price_by_date: p = price_by_date[d]; break
            if p: break
        if not p: continue
        if action == 'BUY' and it_cash > 10:
            it_btc = it_cash / p; it_cash = 0.0
            it_trades.append(('BUY', target_date, p))
        elif action == 'SELL' and it_btc > 0:
            it_cash = it_btc * p; it_btc = 0.0
            it_trades.append(('SELL', target_date, p))
    it_final  = it_cash + it_btc * p_end
    it_return = (it_final - CAPITAL) / CAPITAL * 100

    # Baseline V4 (default thresholds)
    v4_ret, v4_dd, v4_trades = simulate(results, BUY_THR, SELL_THR, 1, 0.0)

    # ── Key-date snapshot: what did V4 score at ideal trade dates? ────────────
    score_by_date = {r['date']: r for r in results}

    print(f'\n{"="*66}')
    print(f'  V4 SCORES AT IDEAL TRADER KEY DATES')
    print(f'{"="*66}')
    print(f'  {"Date":12} {"Action":5} {"BTC":>10}  {"Score":>5} {"w_top":>6} {"w_bot":>6} {"OC":>4} {"MS":>4} {"CP":>4}')
    print(f'  {"-"*64}')
    for target_date, action in sorted(IDEAL_SIGNALS.items()):
        td = datetime.date.fromisoformat(target_date); r = None
        for off in range(0, 5):
            for sign in (0, 1, -1):
                d = (td + datetime.timedelta(days=off * sign)).isoformat()
                if d in score_by_date: r = score_by_date[d]; break
            if r: break
        if not r: print(f'  {target_date}  {action:5}  — no data'); continue
        flag = ' ✓' if (action == 'BUY' and r['score'] <= BUY_THR) or (action == 'SELL' and r['score'] >= SELL_THR) else ' ✗'
        print(f'  {r["date"]:12} {action:5} ${r["btc_price"]:>9,.0f}  {r["score"]:>5}{flag} {r["w_top"]:>6.2f} {r["w_bot"]:>6.2f} {str(r["OC"]):>4} {str(r["MS"]):>4} {str(r["CP"]):>4}')

    # ── Grid search: learn thresholds from ideal trader ───────────────────────
    print(f'\n{"="*66}')
    print(f'  GRID SEARCH — learning buy/sell thresholds')
    print(f'  (min_tiz_days = days in buy zone before entry)')
    print(f'  (min_wtop_sell = minimum w_top required to trigger sell)')
    print(f'{"="*66}')

    best = []
    for buy_thr in range(15, 36, 5):
        for sell_thr in range(65, 96, 5):
            for min_tiz in (1, 7, 14, 21):
                for min_wtop in (0.0, 0.20, 0.35, 0.50):
                    ret, dd, trades = simulate(results, buy_thr, sell_thr, min_tiz, min_wtop)
                    n_complete = sum(1 for t in trades if t[0] == 'S')
                    best.append((ret, dd, buy_thr, sell_thr, min_tiz, min_wtop, n_complete, trades))

    best.sort(key=lambda x: -x[0])  # sort by return descending

    print(f'\n  TOP-10 parameter combinations by return:')
    print(f'  {"Buy":>4} {"Sell":>4} {"TiZ":>4} {"wTop":>5}  {"Return":>9}  {"MaxDD":>7}  {"Sells":>5}')
    print(f'  {"-"*52}')
    shown = set()
    count = 0
    for row in best:
        ret, dd, bt, st, tiz, wt, n_sell, trades = row
        key = (bt, st, tiz, wt)
        if key in shown: continue
        shown.add(key)
        print(f'  {bt:>4} {st:>4} {tiz:>4} {wt:>5.2f}  {_fmt_pct(ret):>9}  {_fmt_pct(dd*100):>7}  {n_sell:>5}')
        count += 1
        if count >= 10: break

    # Pick best combo and show its trades
    ret_b, dd_b, bt_b, st_b, tiz_b, wt_b, _, opt_trades = best[0]
    print(f'\n  Best combo: buy≤{bt_b} sell≥{st_b} min_tiz={tiz_b}d min_wtop={wt_b:.2f}')
    print(f'  Return: {_fmt_pct(ret_b)}  MaxDD: {_fmt_pct(dd_b*100)}  Vs B&H: {_fmt_pct(ret_b - bh_return)}')
    print(f'\n  Trades with best combo:')
    for t in opt_trades:
        tag = 'BUY ' if t[0] == 'B' else 'SELL'
        print(f'    {tag} {t[1]}  ${t[2]:>10,.0f}  score={t[3]}  w_top={t[4]:.2f}')

    # ── Summary table ──────────────────────────────────────────────────────────
    print(f'\n{"="*66}')
    print(f'  SUMMARY  ({results[0]["date"]} → {results[-1]["date"]})')
    print(f'  BTC: ${p0:,.0f} → ${p_end:,.0f}')
    print(f'{"="*66}')
    print(f'  {"Strategy":<28} {"Return":>9}  {"MaxDD":>7}  {"Trades":>6}  {"Final $":>10}')
    print(f'  {"-"*64}')
    bh_val = CAPITAL * p_end / p0
    it_val = it_final
    v4_val = CAPITAL * (1 + v4_ret / 100)
    opt_val= CAPITAL * (1 + ret_b / 100)
    print(f'  {"Buy & Hold":28} {_fmt_pct(bh_return):>9}  {_fmt_pct(bh_max_dd*100):>7}  {"—":>6}  ${bh_val:>9,.0f}')
    print(f'  {"Ideal Trader (8 cycle trades)":28} {_fmt_pct(it_return):>9}  {"—":>7}  {len(it_trades):>6}  ${it_val:>9,.0f}')
    print(f'  {"V4 default (≤25/≥75/tiz=1)":28} {_fmt_pct(v4_ret):>9}  {_fmt_pct(v4_dd*100):>7}  {len(v4_trades):>6}  ${v4_val:>9,.0f}')
    print(f'  {f"V4 learned (≤{bt_b}/≥{st_b}/tiz={tiz_b}d/wt≥{wt_b:.2f})":28} {_fmt_pct(ret_b):>9}  {_fmt_pct(dd_b*100):>7}  {len(opt_trades):>6}  ${opt_val:>9,.0f}')
    print()


if __name__ == '__main__':
    run()
