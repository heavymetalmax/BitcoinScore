"""
Bitcoin Buy Risk — historical backtest.

Data sources:
  unified_history.json   on-chain + macro, 2010+  (no Playwright needed)
  etf_flows.json         spot ETF flows, 2024-01+
  FRED T10Y2Y            yield curve spread, 2000+

Correctness rules:
  - nearest_strict: metric treated as None if nearest point > MAX_STALENESS days away
  - adaptive calibration: rolling percentile uses only data available ≤ milestone date
  - composite score computed only when all CORE_REQUIRED metrics are present
"""
import sys, json, math, datetime, bisect, ssl, urllib.request
sys.path.insert(0, '.')

from scraper.scoring import (
    map_nupl, map_mvrv, map_cvdd, map_rhodl, map_asopr,
    map_mayer_multiple, map_fear_greed, map_m2, map_yield_curve, map_etf_flow,
    OC_WEIGHTS, TECH_WEIGHTS, weighted_score,
)

MAX_STALENESS = 45           # days — beyond this treat metric as absent
ADAPTIVE_WIN  = 4 * 365      # days for rolling-percentile window
ADAPTIVE_BLEND = 0.50        # weight on percentile vs fixed map

# Metrics that must all be present for a composite score to be meaningful.
# ETF flows excluded: pre-2024 they don't exist (not the same as "missing").
# yield_curve_spread excluded from CORE: FRED data fetched live, use as bonus when available
CORE_REQUIRED = {'nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio',
                 'cipherb', 'mayer_multiple', 'fear_greed', 'm2_yoy'}

MILESTONES = [
    ("2018-12-15", "2018 cycle bottom",    3_200),
    ("2019-06-26", "2019 local peak",     13_880),
    ("2020-03-13", "COVID crash",          3_800),
    ("2020-10-01", "Pre-bull start",      10_800),
    ("2021-04-14", "Spring ATH",          63_500),
    ("2021-07-20", "Summer dip",          29_800),
    ("2021-11-10", "Nov 2021 ATH",        69_000),
    ("2022-06-18", "Capitulation",        17_600),
    ("2022-11-21", "FTX bottom",          15_500),
    ("2023-01-14", "Recovery start",      21_000),
    ("2024-03-14", "2024 ATH",            73_500),
    ("2025-01-20", "Jan 2025 top",       109_000),
    ("2025-09-29", "Intraday ATH",       129_000),
    ("2025-11-10", "Post-ATH dump",       94_000),
    ("2026-04-25", "Local low",           77_500),
    ("2026-06-12", "Today",               97_000),
]

# ── Data loading ─────────────────────────────────────────────────────────────

def _series(uh_rows, key, tx=None):
    """Build sorted [(date_str, value)] from unified_history rows."""
    out = []
    for r in uh_rows:
        v = r.get(key)
        if v is not None:
            out.append((r['date'], tx(v) if tx else v))
    out.sort()
    return out


def load_data():
    print("Loading data files...")
    uh = json.load(open('data/history/unified_history.json', encoding='utf-8'))
    rows = uh['series']

    series = {
        'nupl':           _series(rows, 'nupl',         tx=lambda v: v * 100),
        'mvrv':           _series(rows, 'mvrv'),
        'rhodl_ratio':    _series(rows, 'rhodl_ratio'),
        'cvdd_ratio':     _series(rows, 'cvdd_ratio'),
        'asopr':          _series(rows, 'asopr',        tx=lambda v: v + 1.0),
        'mayer_multiple': _series(rows, 'mayer_multiple'),
        'fear_greed':     _series(rows, 'fear_greed'),
        'm2_yoy':         _series(rows, 'm2_yoy'),
        'cipherb':        _series(rows, 'cipherb_daily'),
    }

    # ETF flows
    import os
    etf_path = os.path.join('data', 'history', 'etf_flows.json')
    etf = []
    if os.path.exists(etf_path):
        raw = json.load(open(etf_path, encoding='utf-8'))
        for p in raw:
            d = p.get('timestamp', '')[:10]
            v = p.get('etf_flow_14d')
            if d and v is not None:
                etf.append((d, v))
        etf.sort()
    series['etf_flows'] = etf

    # Yield curve — prefer local file, fall back to FRED
    yc = []
    yc_local = os.path.join('data', 'history', 'yield_curve_history.json')
    if os.path.exists(yc_local):
        raw_yc = json.load(open(yc_local, encoding='utf-8'))
        for r in raw_yc.get('series', raw_yc) if isinstance(raw_yc, dict) else raw_yc:
            if isinstance(r, dict) and r.get('date') and r.get('value') is not None:
                yc.append((r['date'][:10], float(r['value'])))
        print(f"  yield_curve_spread: {len(yc)} pts from local file")
    else:
        try:
            url = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10Y2Y'
            req = urllib.request.Request(url, headers={'User-Agent': 'curl/7.88'})
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                for line in r.read().decode().strip().splitlines():
                    if line.startswith('DATE') or not line: continue
                    parts = line.split(',')
                    if len(parts) == 2:
                        try: yc.append((parts[0], float(parts[1])))
                        except ValueError: pass
            print(f"  yield_curve_spread: {len(yc)} pts from FRED")
        except Exception as e:
            print(f"  yield_curve_spread: FRED unavailable ({e.__class__.__name__}) — skipped")
    series['yield_curve_spread'] = yc

    for k, s in series.items():
        if s:
            print(f"  {k:<20s}: {len(s):5d} pts  ({s[0][0]} → {s[-1][0]})")
    return series


# ── Core helpers ─────────────────────────────────────────────────────────────

def nearest_strict(series, target_date, max_days=MAX_STALENESS):
    """Return (value, days_diff) of nearest point, or (None, None) if > max_days away."""
    if not series:
        return None, None
    dates = [d for d, _ in series]
    td = target_date.isoformat()
    idx = bisect.bisect_left(dates, td)
    best_v, best_d = None, None
    for i in (idx - 1, idx):
        if 0 <= i < len(series):
            d, v = series[i]
            diff = abs((target_date - datetime.date.fromisoformat(d)).days)
            if best_d is None or diff < best_d:
                best_v, best_d = v, diff
    if best_d is None or best_d > max_days:
        return None, None
    return best_v, best_d


def pct_rank_at(series, target_date, value):
    """Rolling-percentile of value in data ≤ target_date, last ADAPTIVE_WIN days."""
    if value is None or not series:
        return None
    hi = target_date.isoformat()
    lo = (target_date - datetime.timedelta(days=ADAPTIVE_WIN)).isoformat()
    win = [v for d, v in series if lo <= d <= hi]
    if len(win) < 24:
        return None
    return round(sum(1 for v in win if v <= value) / len(win) * 100)


def adaptive(static_score, pct):
    if static_score is None: return None
    if pct is None: return static_score
    return round(ADAPTIVE_BLEND * pct + (1 - ADAPTIVE_BLEND) * static_score)


# ── Score computation at a single date ───────────────────────────────────────

def compute_at(target_date, series):
    td = target_date

    raw = {}
    days_off = {}
    for k in series:
        v, d = nearest_strict(series[k], td)
        raw[k] = v
        days_off[k] = d

    # unit-correct (already applied via tx in _series — confirm)
    nupl_v   = raw['nupl']
    mvrv_v   = raw['mvrv']
    rhodl_v  = raw['rhodl_ratio']
    cvdd_v   = raw['cvdd_ratio']
    asopr_v  = raw['asopr']
    mayer_v  = raw['mayer_multiple']
    fg_v     = raw['fear_greed']
    m2_v     = raw['m2_yoy']
    cb_v     = raw['cipherb']
    etf_v    = raw['etf_flows']
    yc_v     = raw['yield_curve_spread']

    s_nupl  = adaptive(map_nupl(nupl_v),         pct_rank_at(series['nupl'],   td, nupl_v))
    s_mvrv  = adaptive(map_mvrv(mvrv_v),         pct_rank_at(series['mvrv'],   td, mvrv_v))
    s_rhodl = map_rhodl(rhodl_v)
    s_cvdd  = adaptive(map_cvdd(cvdd_v),         pct_rank_at(series['cvdd_ratio'], td, cvdd_v))
    s_asopr = map_asopr(asopr_v)
    s_mayer = adaptive(map_mayer_multiple(mayer_v), pct_rank_at(series['mayer_multiple'], td, mayer_v))
    s_fg    = map_fear_greed(fg_v)
    s_m2    = map_m2(m2_v)
    s_cb    = round(max(0, min(100, cb_v))) if cb_v is not None else None
    s_etf   = map_etf_flow(etf_v)
    s_yc    = map_yield_curve(yc_v)

    oc_map = {
        'nupl': s_nupl, 'mvrv_z_score': s_mvrv,
        'rhodl_ratio': s_rhodl, 'cvdd_ratio': s_cvdd, 'asopr': s_asopr,
    }
    tech_map = {
        'cipherb': s_cb, 'mayer_multiple': s_mayer, 'fear_greed': s_fg,
        'm2_yoy': s_m2, 'yield_curve_spread': s_yc, 'etf_flows': s_etf,
    }

    oc   = weighted_score(OC_WEIGHTS,   oc_map)
    tech = weighted_score(TECH_WEIGHTS, tech_map)

    # Check core coverage — composite valid only when all CORE_REQUIRED present
    present = {k for k, v in {**oc_map, **tech_map}.items()
               if k in CORE_REQUIRED and v is not None}
    full_coverage = (present == CORE_REQUIRED)

    if full_coverage:
        if oc is not None and tech is not None:
            final = round(oc * 0.5 + tech * 0.5)
        elif oc is not None: final = oc
        elif tech is not None: final = tech
        else: final = None
    else:
        final = None

    return {
        'raw': raw, 'days_off': days_off,
        'scores': {**oc_map, **tech_map},
        'oc': oc, 'tech': tech, 'final': final,
        'present': present, 'full_coverage': full_coverage,
    }


# ── Output ────────────────────────────────────────────────────────────────────

SCORE_COLS = ['nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'asopr',
              'cipherb', 'mayer_multiple', 'fear_greed', 'm2_yoy', 'yield_curve_spread', 'etf_flows']
SHORT = {'nupl':'NUPL','mvrv_z_score':'MVRV','rhodl_ratio':'RHDL','cvdd_ratio':'CVDD','asopr':'SOPR',
         'cipherb':'CB','mayer_multiple':'MAY','fear_greed':'FG','m2_yoy':'M2',
         'yield_curve_spread':'YC','etf_flows':'ETF'}

def fmt(v):
    return f"{v:3d}" if v is not None else " — "


def run():
    series = load_data()
    results = []
    for date_str, label, price in MILESTONES:
        td = datetime.date.fromisoformat(date_str)
        r = compute_at(td, series)
        results.append((date_str, label, price, r))

    # ── Score table ──────────────────────────────────────────────────────────
    header_metrics = " ".join(f"{SHORT[c]:>4}" for c in SCORE_COLS)
    print(f"\n{'Date':<12} {'Label':<22} {'BTC':>8}  {'OC':>4} {'TC':>4} {'IDX':>4}  {header_metrics}")
    print("─" * 140)

    for date_str, label, price, r in results:
        sc = r['scores']
        metric_vals = " ".join(fmt(sc.get(c)) for c in SCORE_COLS)
        idx_str = f"{r['final']:3d}" if r['final'] is not None else " ✗ "
        oc_str  = fmt(r['oc'])
        tc_str  = fmt(r['tech'])
        print(f"{date_str:<12} {label:<22} ${price:>8,}  {oc_str} {tc_str} {idx_str}  {metric_vals}")

    # ── Coverage matrix ──────────────────────────────────────────────────────
    RAW_COLS = ['nupl', 'mvrv', 'rhodl_ratio', 'cvdd_ratio', 'asopr',
                'cipherb', 'mayer_multiple', 'fear_greed', 'm2_yoy', 'yield_curve_spread', 'etf_flows']
    RAW_SHORT = {'nupl':'NUPL','mvrv':'MVRV','rhodl_ratio':'RHDL','cvdd_ratio':'CVDD','asopr':'SOPR',
                 'cipherb':'CB','mayer_multiple':'MAY','fear_greed':'FG','m2_yoy':'M2',
                 'yield_curve_spread':'YC','etf_flows':'ETF'}

    cov_header = " ".join(f"{RAW_SHORT[c]:>5}" for c in RAW_COLS)
    print(f"\nCoverage matrix  (✓ = present, N = days offset, ✗ = missing)")
    print(f"{'Date':<12} {'Label':<22} {cov_header}  FULL")
    print("─" * 130)

    for date_str, label, price, r in results:
        doff = r['days_off']
        raw  = r['raw']

        def cell(k):
            v = raw.get(k)
            d = doff.get(k)
            if v is None:
                return "   ✗ "
            if d == 0:
                return "   ✓ "
            return f" +{d:2d}d"

        cells = " ".join(cell(k) for k in RAW_COLS)
        full = "✓" if r['full_coverage'] else "✗"
        print(f"{date_str:<12} {label:<22} {cells}  {full}")

    # ── Legend ───────────────────────────────────────────────────────────────
    print(f"""
Notes:
  ✗ (IDX)  composite score not computed — one or more CORE metrics missing
  CORE     = {sorted(CORE_REQUIRED)}
  ETF      included in TECH weight only from 2024-01-11 (spot ETF launch); renormalized before
  adaptive = 50% fixed map + 50% rolling {ADAPTIVE_WIN//365}yr percentile  (nupl, mvrv, cvdd, mayer)
  staleness limit = {MAX_STALENESS} days — nearest datapoint beyond this treated as absent
""")


if __name__ == '__main__':
    run()
