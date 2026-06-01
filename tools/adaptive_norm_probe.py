"""
Adaptive-normalisation probe.

Demonstrates, on real multi-cycle history, why FIXED indicator thresholds
drift out of calibration as Bitcoin matures (on-chain cycle peaks shrink,
bottoms rise), and how an ADAPTIVE rolling-percentile — or a BLEND of the two
— keeps the 0-100 risk score calibrated across cycles.

Reproduces the in-browser finding for NUPL:
    cycle tops read ~100 under adaptive but degrade (91→76) under fixed;
    cycle bottoms read ~0 under adaptive but creep up (3→41) under fixed.

Needs a backfilled history file (run tools/backfill_onchain_history.py first):
    python tools/adaptive_norm_probe.py nupl
"""
import sys, os, json, datetime
sys.path.insert(0, '.')
from scraper import scoring

# metric -> (history file, project map fn, higher_value_is_higher_risk)
METRICS = {
    'nupl':  ('data/history/nupl_history.json',  scoring.map_nupl,  True),
    'mvrv':  ('data/history/mvrv_history.json',  scoring.map_mvrv,  True),
    'rhodl': ('data/history/rhodl_history.json', scoring.map_rhodl, True),
    'asopr': ('data/history/asopr_history.json', scoring.map_asopr, True),
    'cvdd':  ('data/history/cvdd_history.json',  scoring.map_cvdd,  True),
}

WIN_YEARS = 4          # trailing window for the adaptive percentile
BLEND = 0.5            # final = BLEND*adaptive + (1-BLEND)*fixed  (the recommended hybrid)

def parse(d): return datetime.date.fromisoformat(d[:10])

def adaptive_percentile(dates, vals, i, win_years, higher_is_risk):
    t = parse(dates[i]); lo = t - datetime.timedelta(days=int(win_years*365))
    cnt = le = 0
    for j in range(i+1):
        if parse(dates[j]) >= lo and vals[j] is not None:
            cnt += 1
            if (vals[j] <= vals[i]) == higher_is_risk:
                le += 1
    return round(le/cnt*100) if cnt else None

def main(metric):
    if metric not in METRICS:
        print(f"unknown metric '{metric}' (known: {', '.join(METRICS)})"); return
    path, mapfn, hi = METRICS[metric]
    if not os.path.exists(path):
        print(f"missing {path} — run:  python tools/backfill_onchain_history.py {metric}"); return

    data = json.load(open(path))
    series = data['series']
    dates = [r[0] for r in series]; vals = [r[1] for r in series]
    n = len(series)
    print(f"\n{metric.upper()}  {dates[0]} → {dates[-1]}  ({n} points)\n")

    def closest(d):
        td = parse(d); return min(range(n), key=lambda i: abs((parse(dates[i])-td).days))

    # canonical cycle extremes
    EXTREMES = [
        ('TOP 2013','2013-11-30'), ('TOP 2017','2017-12-15'), ('TOP 2021','2021-02-21'),
        ('TOP 2024','2024-03-14'), ('BOTTOM 2018','2018-12-15'), ('BOTTOM 2022','2022-11-21'),
        ('LATEST', dates[-1]),
    ]
    print(f"{'point':<12}{'date':<12}{'raw':>9}{'fixed':>7}{'adaptive':>10}{'blend':>7}")
    print('─'*57)
    for lab, d in EXTREMES:
        i = closest(d); raw = vals[i]
        fx = mapfn(raw)
        ad = adaptive_percentile(dates, vals, i, WIN_YEARS, hi)
        bl = round(BLEND*ad + (1-BLEND)*fx) if (ad is not None and fx is not None) else None
        print(f"{lab:<12}{dates[i]:<12}{raw:>9.3f}{fx:>7}{ad:>10}{bl:>7}")

    print(f"\nfixed = current project mapping · adaptive = {WIN_YEARS}y rolling percentile")
    print(f"blend = {BLEND:.0%} adaptive + {1-BLEND:.0%} fixed  (recommended hybrid:")
    print("relative sensitivity to maturation, but anchored so absolute de-risking still shows)")

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'nupl')
