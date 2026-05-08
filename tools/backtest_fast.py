"""
Fast partial backtest: uses local data only (M2 FRED, F&G, CipherB local files, Real Yield FRED DFII10).
OC group averages are taken from the last full backtest run (stable, SOPR removal < 2pt effect).
Outputs recalculated OC_score, Tech_score, Final with current weights and blending.
"""
import sys
import json
import datetime
from datetime import timedelta
sys.path.insert(0, '.')

from tools.backtest import (
    load_us_m2_yoy, load_cipherb_series, fetch_fg_series,
    closest, parse_date, fetch_fred_series
)
from scraper.scoring import map_m2, map_fear_greed, map_real_yield, TECH_WEIGHTS, weighted_score

m2_series  = load_us_m2_yoy()
fg_series  = fetch_fg_series()
cb_series  = load_cipherb_series()
ry_series  = fetch_fred_series('DFII10')

# 13-week momentum: delta in percentage points over ~91 days
# Range: -1.5pp (sharp easing) to +1.5pp (sharp tightening)
# Rising real yield = tightening = high risk; falling = easing = low risk
def map_ry_momentum(delta):
    if delta is None: return None
    delta = max(-1.5, min(1.5, delta))
    return round(((delta + 1.5) / 3.0) * 100)

def ry_13w(series, td):
    v_now  = closest(series, td)
    v_prev = closest(series, td - timedelta(weeks=13))
    if v_now is None or v_prev is None: return None, None
    return v_now - v_prev, v_now

# (date, label, btc, old_oc_avg, old_smc)
MILESTONES = [
    ('2018-12-15','Cycle bear bottom',   3200,  18,  0),
    ('2019-06-26','Local peak',         13880,  69, 100),
    ('2020-03-13','COVID crash',         3800,  31,  0),
    ('2020-10-01','Pre-bull start',     10800,  52, 100),
    ('2021-04-14','Spring ATH',         63500,  91, 100),
    ('2021-07-20','Summer dip',         29800,  56,  52),
    ('2021-11-10','Nov 2021 ATH',       69000,  84, 100),
    ('2022-06-18','Capitulation',       17600,  29,   0),
    ('2022-11-21','FTX bottom',         15500,  22,   0),
    ('2023-01-14','Recovery start',     21000,  33,   0),
    ('2024-03-14','2024 ATH',           73500,  77, 100),
    ('2025-01-20','Jan 2025 peak',     109000,  73, 100),
    ('2025-09-26','Close ATH',         123000,  63, 100),
    ('2025-09-29','Intraday ATH',      129000,  65, 100),
    ('2025-11-10','Post-ATH dump',      94000,  60,  56),
    ('2026-04-27','Today',              77500,  47,   8),
]

def f(v):
    return f'{v:3d}' if v is not None else '  -'

def score_with_ry(oc_avg, smc, s_m2, s_fg, s_cb, s_ry):
    tech_map = {'cipherb': s_cb, 'smc': smc, 'fear_greed': s_fg, 'real_yield': s_ry, 'm2_yoy': s_m2}
    tech_avg = weighted_score(TECH_WEIGHTS, tech_map)
    if tech_avg is None: return None, None, None
    return round(oc_avg * 0.8 + tech_avg * 0.2), round(oc_avg * 0.2 + tech_avg * 0.8), round(oc_avg * 0.5 + tech_avg * 0.5)

print(f"\n{'─'*118}")
print(f"  АБСОЛЮТНЕ значення DFII10  vs  13-тижневий MOMENTUM DFII10")
print(f"{'─'*118}")
print(f"{'Date':<12} {'Label':<22} {'BTC':>8}   {'OC':>3} {'Tc':>3} {'Fn':>3}   {'OC':>3} {'Tc':>3} {'Fn':>3}   M2   FG   CB  SMC  RY_abs  RY_mom(Δ)")
print(f"{'':>46}   {'── absolute ──':>11}   {'── momentum ──':>11}")
print('─'*118)

for (date_str, label, btc, oc_avg, smc) in MILESTONES:
    td   = parse_date(date_str)
    m2v  = closest(m2_series, td)
    fgv  = closest(fg_series, td)
    cbv  = closest(cb_series, td)
    ryv  = closest(ry_series, td)
    ry_delta, _ = ry_13w(ry_series, td)

    s_m2  = map_m2(m2v)
    s_fg  = map_fear_greed(fgv) if fgv is not None else None
    s_cb  = round(max(0, min(100, cbv))) if cbv is not None else None
    s_ry_abs = map_real_yield(ryv) if ryv is not None else None
    s_ry_mom = map_ry_momentum(ry_delta)

    oc_a, tc_a, fn_a = score_with_ry(oc_avg, smc, s_m2, s_fg, s_cb, s_ry_abs)
    oc_m, tc_m, fn_m = score_with_ry(oc_avg, smc, s_m2, s_fg, s_cb, s_ry_mom)

    delta_str = f'{ry_delta:+.2f}' if ry_delta is not None else '  -'
    ryv_str   = f'{ryv:.2f}' if ryv is not None else ' -'
    print(f"{date_str:<12} {label:<22} ${btc:>7,}   {f(oc_a)} {f(tc_a)} {f(fn_a)}   {f(oc_m)} {f(tc_m)} {f(fn_m)}   {f(s_m2)}  {f(s_fg)}  {f(s_cb)}  {f(smc)}  {ryv_str:>5}({f(s_ry_abs)})  {delta_str}({f(s_ry_mom)})")

print()
print("Note: TECH_WEIGHTS unchanged (real_yield=10%). Only the mapping function differs between columns.")
print("      Momentum = DFII10(T) − DFII10(T−13w). Range clipped to ±1.5pp → score 0–100.")
