"""
Backtest using scores.json (ONE database) — no live fetches, fully offline.

For each milestone date, reads raw metric values from scores.json,
applies current scoring functions, and shows v1 (equal weights) and v2
(regime-aware) scores side by side.

Run: python tools/backtest_unified.py
"""
import sys, json, datetime
sys.path.insert(0, '.')

from scraper.scoring import (
    map_nupl, map_mvrv, map_asopr, map_rhodl, map_cvdd,
    map_mayer_multiple, map_fear_greed, map_m2, map_etf_flow,
    _adaptive, OC_WEIGHTS, TECH_WEIGHTS, weighted_score,
    _load_metric_history, ADAPTIVE_DEBUG,
)
from scraper.scoring_v2 import (
    OC_BOTTOM, OC_TOP, OC_NEUTRAL,
    TECH_BOTTOM, TECH_TOP, TECH_NEUTRAL,
    map_puell, BOTTOM_THRESHOLD, TOP_THRESHOLD,
)

MILESTONES = [
    ("2018-12-15", "Bear bottom",     3_200),
    ("2019-06-26", "Local peak",     13_880),
    ("2020-03-13", "COVID crash",     3_800),
    ("2020-10-01", "Pre-bull",       10_800),
    ("2021-04-14", "Spring ATH",     63_500),
    ("2021-07-20", "Summer dip",     29_800),
    ("2021-11-10", "Nov ATH",        69_000),
    ("2022-06-18", "Capitulation",   17_600),
    ("2022-11-21", "FTX bottom",     15_500),
    ("2023-01-14", "Recovery",       21_000),
    ("2024-03-14", "2024 ATH",       73_500),
    ("2025-01-20", "Jan 2025 ATH",  109_000),
    ("2026-04-07", "Correction",     71_924),
    ("2026-06-12", "Today",          None),
]


def load_unified():
    rows = json.load(open('data/history/scores.json', encoding='utf-8'))
    return {r['date']: r for r in rows}


def closest_row(idx, target_date):
    td = datetime.date.fromisoformat(target_date)
    for offset in range(0, 8):
        for sign in (0, 1, -1):
            d = (td + datetime.timedelta(days=offset * sign)).isoformat()
            if d in idx:
                return idx[d], d
    return None, None


def score_row(row):
    """Map scores.json row → slider map dict."""
    nupl_raw  = row.get('nupl')
    mvrv_raw  = row.get('mvrv')
    cvdd_raw  = row.get('cvdd_ratio')
    rhodl_raw = row.get('rhodl_ratio')
    asopr_raw = row.get('asopr')
    puell_raw = row.get('puell')
    mayer_raw = row.get('mayer_multiple')
    fg_raw    = row.get('fear_greed')
    m2_raw    = row.get('m2_yoy')
    cb_raw    = row.get('cipherb_daily')

    sm = {
        'nupl':           _adaptive('nupl',       nupl_raw,  map_nupl(nupl_raw)),
        'mvrv_z_score':   _adaptive('mvrv',       mvrv_raw,  map_mvrv(mvrv_raw)),
        'cvdd_ratio':     _adaptive('cvdd_ratio',  cvdd_raw,  map_cvdd(cvdd_raw)),
        'rhodl_ratio':    map_rhodl(rhodl_raw),
        'asopr':          map_asopr(asopr_raw),
        'puell':          _adaptive('puell',       puell_raw, map_puell(puell_raw)),
        'mayer_multiple': _adaptive('mayer',       mayer_raw, map_mayer_multiple(mayer_raw)),
        'fear_greed':     map_fear_greed(fg_raw),
        'm2_yoy':         map_m2(m2_raw),
        'cipherb':        round(cb_raw) if cb_raw is not None else None,
        # etf_flows/yield_curve not in unified — excluded (weights re-normalise)
    }
    return sm


def detect_regime(prelim):
    if prelim <= BOTTOM_THRESHOLD: return 'bottom', OC_BOTTOM, TECH_BOTTOM
    if prelim >= TOP_THRESHOLD:    return 'top',    OC_TOP,    TECH_TOP
    return 'neutral', OC_NEUTRAL, TECH_NEUTRAL


def run():
    unified = load_unified()
    print(f"\n{'Date':12s} {'Label':18s} {'BTC':>8s}  "
          f"{'OC':>3s} {'Tech':>4s} {'V1':>3s}  "
          f"{'V2':>3s} {'Regime':>7s}  {'NUPL':>5s} {'MVRV':>5s} {'Mayer':>5s} {'CB':>3s} {'M2':>3s}")
    print("─" * 100)

    for date_str, label, btc in MILESTONES:
        row, actual_date = closest_row(unified, date_str)
        if row is None:
            print(f"{date_str:12s} {label:18s} {'?':>8s}  (no data)")
            continue

        ADAPTIVE_DEBUG.clear()
        sm = score_row(row)

        # V1: equal OC/Tech weights
        oc_avg   = weighted_score(OC_WEIGHTS,   sm)
        tech_avg = weighted_score(TECH_WEIGHTS, sm)
        v1_final = round((oc_avg + tech_avg) / 2) if oc_avg and tech_avg else (oc_avg or tech_avg)

        # V2: regime-aware weights
        prelim = v1_final or 50
        regime, oc_w, tech_w = detect_regime(prelim)
        oc2   = weighted_score(oc_w,   sm)
        tch2  = weighted_score(tech_w, sm)
        v2_final = round((oc2 + tch2) / 2) if oc2 and tch2 else (oc2 or tch2)

        btc_str = f"${btc//1000}k" if btc else "live"
        nupl_s  = sm.get('nupl')
        mvrv_s  = sm.get('mvrv_z_score')
        mayer_s = sm.get('mayer_multiple')
        cb_s    = sm.get('cipherb')
        m2_s    = sm.get('m2_yoy')

        def f(v): return f'{v:3d}' if v is not None else '  -'
        flag = " ◄" if date_str in ("2018-12-15","2022-11-21","2021-04-14","2021-11-10","2024-03-14","2025-01-20") else ""

        print(f"{actual_date:12s} {label:18s} {btc_str:>8s}  "
              f"{f(oc_avg)} {f(tech_avg)} {f(v1_final)}  "
              f"{f(v2_final)} {regime:>7s}  "
              f"{f(nupl_s)} {f(mvrv_s)} {f(mayer_s)} {f(cb_s)} {f(m2_s)}"
              f"{flag}")

    print("\nLegend: OC=on-chain avg  Tech=tech/macro avg  V1=50/50 blend  V2=regime-weighted")
    print("        Cols: NUPL MVRV Mayer CipherB M2 — all 0-100 risk scores")


if __name__ == '__main__':
    run()
