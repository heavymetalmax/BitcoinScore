"""
Generate web/sparklines.json — last 365 days of mapped 0-100 scores per metric
from data/history/scores.json (ONE database).

Run: python3 tools/generate_sparklines.py
"""
import json, math, os
from datetime import datetime, timedelta


def map_nupl(v):
    if v is None: return None
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
    c = max(-2, min(15, float(v)))
    return round(((15 - c) / 17) * 100)


def map_etf(v):
    if v is None: return None
    v = float(v)
    MIN_VAL, MID_VAL, MAX_VAL = -1000.0, 375.0, 2000.0
    if v <= MIN_VAL: return 0
    if v >= MAX_VAL: return 100
    if v < MID_VAL:
        return round(((v - MIN_VAL) / (MID_VAL - MIN_VAL)) * 50)
    return round(50 + ((v - MID_VAL) / (MAX_VAL - MID_VAL)) * 50)


def map_yc(v):
    # 10Y-2Y spread (%): -1.0 = max inversion/risk=100; +2.0 = steep/risk=0
    if v is None: return None
    v = max(-1.0, min(2.0, float(v)))
    return round(((2.0 - v) / 3.0) * 100)


def map_dxy(v):
    # FRED Broad Dollar Index: <=108→0, 116→50, >=128→100
    if v is None: return None
    v = float(v)
    if v <= 108.0: return 0
    if v >= 128.0: return 100
    if v <= 116.0:
        return round((v - 108.0) / (116.0 - 108.0) * 50)
    return round(50 + (v - 116.0) / (128.0 - 116.0) * 50)


def load_dxy_sparkline(cutoff):
    path = 'data/history/dxy_history.json'
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        raw = json.load(f)
    series = raw.get('series', raw) if isinstance(raw, dict) else raw
    result = {}
    for pt in series:
        date = pt.get('date', '')
        if not date or date < cutoff:
            continue
        result[date] = map_dxy(pt.get('value'))
    return result


def load_etf_sparkline(cutoff):
    """Load ETF 7d flows history → list of (date_str, score)."""
    path = 'data/history/etf_flows.json'
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        rows = json.load(f)
    result = {}
    for r in rows:
        ts = r.get('timestamp', '')
        date = ts[:10] if ts else None
        if not date or date < cutoff:
            continue
        v = r.get('etf_flow_7d')
        if v is None:
            v = r.get('etf_flow_14d')
        result[date] = map_etf(v)
    return result


def load_yc_sparkline(cutoff):
    """Load yield-curve spread history. Uses yc_inversion_history blue series
    (weekly digitised data) as proxy when no daily spread history exists."""
    # Try yield_curve.json first (scraper-built, daily but sparse)
    path_daily = 'data/history/yield_curve.json'
    daily = {}
    if os.path.exists(path_daily):
        with open(path_daily) as f:
            rows = json.load(f)
        if isinstance(rows, list):
            for r in rows:
                ts = r.get('timestamp', '')
                date = ts[:10] if ts else None
                v = r.get('yield_curve') or r.get('spread')
                if date and date >= cutoff and v is not None:
                    daily[date] = map_yc(v)
    # Supplement with inversion-ratio proxy (MacroMicro weekly digitised data)
    path_inv = 'data/history/yc_inversion_history.json'
    if os.path.exists(path_inv):
        with open(path_inv) as f:
            inv = json.load(f)
        series = inv.get('blue', {}).get('series', [])
        # inversion ratio 0-100% → yield-curve score proxy
        # 0% inverted = curve positive → low risk; 100% = deep inversion → high risk
        for pt in series:
            date = pt.get('date', '')
            if not date or date < cutoff:
                continue
            if date not in daily:
                pct = pt.get('value', 0)
                daily[date] = round(max(0, min(100, float(pct))))
    return daily


def main():
    cutoff = (datetime.today() - timedelta(days=365)).strftime('%Y-%m-%d')

    with open('data/history/scores.json') as f:
        series = json.load(f)

    recent = [r for r in series if r.get('date', '') >= cutoff]

    etf_by_date = load_etf_sparkline(cutoff)
    yc_by_date  = load_yc_sparkline(cutoff)
    dxy_by_date = load_dxy_sparkline(cutoff)

    try:
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        from scraper.normalizer import normalize_metric as _norm_m
    except Exception:
        _norm_m = None

    def _nm(metric_key, val, date_str, fallback_fn):
        if isinstance(val, dict):
            val = val.get('value') if val.get('value') is not None else val.get('avg_7d')
        if val is None or isinstance(val, dict):
            return None
        if _norm_m is not None:
            try:
                res = _norm_m(metric_key, val, date_str)
                if res is not None:
                    return res
            except Exception:
                pass
        return fallback_fn(val)

    result = {
        'dates':              [r['date'] for r in recent],
        'nupl':               [_nm('nupl',           r.get('nupl'),           r['date'], map_nupl)    for r in recent],
        'mvrv_z_score':       [_nm('mvrv_z_score',   r.get('mvrv'),           r['date'], map_mvrv)    for r in recent],
        'rhodl_ratio':        [_nm('rhodl_ratio',    r.get('rhodl_ratio'),    r['date'], map_rhodl)   for r in recent],
        'cvdd_ratio':         [_nm('cvdd_ratio',     r.get('cvdd_ratio'),     r['date'], map_cvdd)    for r in recent],
        'asopr':              [_nm('asopr',          r.get('asopr'),          r['date'], map_asopr)   for r in recent],
        'cipherb':            [map_cipherb(r.get('cipherb_daily'))                                    for r in recent],
        'mayer_multiple':     [_nm('mayer_multiple', r.get('mayer_multiple'), r['date'], map_mayer)   for r in recent],
        'etf_flows':          [etf_by_date.get(r['date'])                                             for r in recent],
        'fear_greed':         [_nm('fear_greed',     r.get('fear_greed'),     r['date'], map_fg)      for r in recent],
        'yield_curve_spread': [yc_by_date.get(r['date'])                                              for r in recent],
        'm2_yoy':             [_nm('m2_yoy',         r.get('m2_yoy'),         r['date'], map_m2)      for r in recent],
        'dxy':                [dxy_by_date.get(r['date'])                                             for r in recent],
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
