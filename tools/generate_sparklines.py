"""
Generate web/sparklines.json — last 365 days of mapped 0-100 scores per metric
from data/history/unified_history.json.

Run: python3 tools/generate_sparklines.py
"""
import json, math, os
from datetime import datetime, timedelta


def map_nupl(v):
    if v is None: return None
    v = v * 100  # fraction → percentage
    v = max(-50.0, min(100.0, v))
    if v <= 40.0:
        score = 8 + ((v - (-20.0)) / (40.0 - (-20.0))) * (50 - 8)
    else:
        score = 50 + ((v - 40.0) / (75.0 - 40.0)) * (100 - 50)
    return round(max(0, min(100, score)))


def map_mvrv(v):
    if v is None: return None
    v = max(-0.5, min(5.0, v))
    if v <= 1.0:
        score = 8 + ((v - (-0.3)) / (1.0 - (-0.3))) * (50 - 8)
    else:
        score = 50 + ((v - 1.0) / (5.0 - 1.0)) * (100 - 50)
    return round(max(0, min(100, score)))


def map_asopr(v):
    if v is None: return None
    v = v + 1.0  # unified_history stores (aSOPR - 1)
    if v <= 1.00:
        s = ((v - 0.88) / 0.12) * 50
    else:
        s = 50 + ((v - 1.00) / 0.12) * 50
    return round(max(0, min(100, s)))


def map_cvdd(v):
    if v is None: return None
    c = max(1, min(5, v))
    return round(math.log10(c) / math.log10(5) * 100)


def map_rhodl(v):
    if v is None: return None
    v = max(200, min(10000, v))
    score = (math.log10(v) - math.log10(200)) / (math.log10(10000) - math.log10(200)) * 100
    return round(max(0, min(100, score)))


def map_cipherb(v):
    if v is None: return None
    return round(max(0, min(100, v)))


def map_mayer(v):
    if v is None: return None
    c = max(0.5, min(2.1, v))
    return round((c - 0.5) / 1.6 * 100)


def map_fg(v):
    if v is None: return None
    return round(max(0, min(100, v)))


def map_m2(v):
    if v is None: return None
    c = max(-5, min(10, v))
    return round(((10 - c) / 15) * 100)


def main():
    cutoff = (datetime.today() - timedelta(days=365)).strftime('%Y-%m-%d')

    with open('data/history/unified_history.json') as f:
        series = json.load(f)['series']

    recent = [r for r in series if r['date'] >= cutoff]

    result = {
        'dates':          [r['date'] for r in recent],
        'nupl':           [map_nupl(r.get('nupl'))           for r in recent],
        'mvrv_z_score':   [map_mvrv(r.get('mvrv'))           for r in recent],
        'rhodl_ratio':    [map_rhodl(r.get('rhodl_ratio'))   for r in recent],
        'cvdd_ratio':     [map_cvdd(r.get('cvdd_ratio'))     for r in recent],
        'asopr':          [map_asopr(r.get('asopr'))         for r in recent],
        'cipherb':        [map_cipherb(r.get('cipherb_daily')) for r in recent],
        'mayer_multiple': [map_mayer(r.get('mayer_multiple')) for r in recent],
        'fear_greed':     [map_fg(r.get('fear_greed'))       for r in recent],
        'm2_yoy':         [map_m2(r.get('m2_yoy'))           for r in recent],
    }

    out_path = 'web/sparklines.json'
    with open(out_path, 'w') as f:
        json.dump(result, f)

    dates = result['dates']
    print(f"Generated {out_path}: {len(dates)} dates ({dates[0] if dates else '?'} → {dates[-1] if dates else '?'})")
    for k, vals in result.items():
        if k == 'dates':
            continue
        n = sum(1 for v in vals if v is not None)
        print(f"  {k:<20} {n}/{len(vals)} non-null")


if __name__ == '__main__':
    main()
