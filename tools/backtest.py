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
import sys, os, json, math, datetime, bisect, ssl, urllib.request
sys.path.insert(0, '.')

from scraper.scoring import (
    score_from_raw,
    OC_WEIGHTS, TECH_WEIGHTS, weighted_score,
)
from scraper.scoring_v2 import score_processor_v2, _METRIC_LOOKBACK

MAX_STALENESS = 45           # days — beyond this treat metric as absent
ADAPTIVE_WIN  = 4 * 365      # days for rolling-percentile window (same as scoring.py)
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
    ("2025-07-17", "CB weekly peak",     118_735),
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
        'puell_multiple': _series(rows, 'puell'),
        'dxy':            _series(rows, 'dxy'),
        'funding_rate':   _series(rows, 'funding_rate'),
        'pi_gap_pct':     _series(rows, 'pi_gap_pct'),
    }

    # Weekly CipherB — matches live scoring formula (0.8×weekly + 0.2×daily)
    wk_path = os.path.join('data', 'history', 'cipherb_btcusdt_1w.json')
    cipherb_weekly = []
    cipherb_weekly_fb = []  # fast_bearish_div flag as 1/0 series
    if os.path.exists(wk_path):
        raw_wk = json.load(open(wk_path, encoding='utf-8'))
        wk_rows = raw_wk if isinstance(raw_wk, list) else raw_wk.get('series', raw_wk)
        for r in wk_rows:
            # support both {date: 'YYYY-MM-DD'} and {timestamp: unix} formats
            d = r.get('date')
            if d is None:
                ts = r.get('timestamp')
                if ts is not None:
                    d = datetime.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')
            ws = r.get('weekly_score')
            fb = r.get('fast_bearish_div', False)
            if d is not None and ws is not None:
                cipherb_weekly.append((d, float(ws)))
                cipherb_weekly_fb.append((d, 1.0 if fb else 0.0))
        cipherb_weekly.sort()
        cipherb_weekly_fb.sort()
        print(f"  {'cipherb_weekly':<20s}: {len(cipherb_weekly):5d} pts  ({cipherb_weekly[0][0]} → {cipherb_weekly[-1][0]})")
    series['cipherb_weekly']    = cipherb_weekly
    series['cipherb_weekly_fb'] = cipherb_weekly_fb

    # ETF flows
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

    # LTH Supply — raw BTC count; map_lth_supply handles conversion to risk score
    lth_path = os.path.join('data', 'history', 'lth_supply_history.json')
    lth = []
    if os.path.exists(lth_path):
        raw_lth = json.load(open(lth_path, encoding='utf-8'))
        lth_series = raw_lth.get('series', raw_lth.get('data', []))
        for item in lth_series:
            if isinstance(item, list) and len(item) >= 2:
                lth.append((str(item[0])[:10], float(item[1])))
            elif isinstance(item, dict):
                d = item.get('date', '')[:10]
                v = item.get('value')
                if d and v is not None:
                    lth.append((d, float(v)))
        lth.sort()
    series['lth_supply'] = lth

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


# ── Score computation at a single date ───────────────────────────────────────

def compute_at(target_date, series):
    td = target_date

    raw_vals = {}
    days_off = {}
    for k in series:
        if k in ('cipherb_weekly', 'cipherb_weekly_fb'):
            continue
        v, d = nearest_strict(series[k], td)
        raw_vals[k] = v
        days_off[k] = d

    # Weekly CipherB — separate lookup, then assemble dict for score_from_raw()
    wk_score, _ = nearest_strict(series['cipherb_weekly'], td)
    wk_fb, _    = nearest_strict(series['cipherb_weekly_fb'], td)
    cb_daily = raw_vals.get('cipherb')
    cb_dict = None
    if wk_score is not None:
        cb_dict = {
            'weekly_score':     wk_score,
            'daily_score':      cb_daily,
            'fast_bearish_div': bool(wk_fb),
        }
    elif cb_daily is not None:
        # Weekly file absent — use daily score as fallback for both slots
        cb_dict = {
            'weekly_score':     cb_daily,
            'daily_score':      cb_daily,
            'fast_bearish_div': False,
        }

    raw = {
        'nupl':               raw_vals.get('nupl'),           # tx=×100 applied in _series()
        'mvrv':               raw_vals.get('mvrv'),
        'rhodl_ratio':        raw_vals.get('rhodl_ratio'),
        'cvdd_ratio':         raw_vals.get('cvdd_ratio'),
        'asopr':              raw_vals.get('asopr'),           # tx=+1.0 applied in _series()
        'mayer_multiple':     raw_vals.get('mayer_multiple'),
        'fear_greed':         raw_vals.get('fear_greed'),
        'm2_yoy':             raw_vals.get('m2_yoy'),
        'yield_curve_spread': raw_vals.get('yield_curve_spread'),
        'cipherb':            cb_dict,
        'etf_flows':          raw_vals.get('etf_flows'),
        'puell_multiple':     raw_vals.get('puell_multiple'),
    }

    # Point-in-time adaptive percentiles (only data ≤ target_date)
    adaptive_pcts = {}
    for metric, key in [('nupl', 'nupl'), ('mvrv', 'mvrv'),
                         ('cvdd_ratio', 'cvdd_ratio'), ('mayer', 'mayer_multiple')]:
        pct = pct_rank_at(series[key], td, raw_vals.get(key))
        if pct is not None:
            adaptive_pcts[metric] = pct

    scores = score_from_raw(raw, adaptive_pcts)

    oc_map   = {k: scores.get(k) for k in OC_WEIGHTS}
    tech_map = {k: scores.get(k) for k in TECH_WEIGHTS}

    oc   = weighted_score(OC_WEIGHTS,   oc_map)
    tech = weighted_score(TECH_WEIGHTS, tech_map)

    present = {k for k, v in {**oc_map, **tech_map}.items()
               if k in CORE_REQUIRED and v is not None}
    full_coverage = (present == CORE_REQUIRED)

    if full_coverage and oc is not None and tech is not None:
        final = round(oc * 0.5 + tech * 0.5)
    elif full_coverage and oc is not None:
        final = oc
    elif full_coverage and tech is not None:
        final = tech
    else:
        final = None

    return {
        'raw': raw_vals, 'days_off': days_off,
        'scores': scores,
        'oc': oc, 'tech': tech, 'final': final,
        'present': present, 'full_coverage': full_coverage,
    }


# ── Score Processor v2 helper ─────────────────────────────────────────────────

def _prev_scores(td, series):
    """Build prev_scores dict for score_processor_v2: each metric from its lookback ago."""
    unique_lbs = set(_METRIC_LOOKBACK.values())
    lb_scores = {}
    for lb in unique_lbs:
        prev_td = td - datetime.timedelta(days=lb)
        r = compute_at(prev_td, series)
        lb_scores[lb] = r['scores']
    return {m: lb_scores[lb].get(m) for m, lb in _METRIC_LOOKBACK.items()}


def v2_at(td, series):
    """Return (diag_score, full_score) tuple for a given date."""
    r = compute_at(td, series)
    prev = _prev_scores(td, series)
    sp_v2_diag = score_processor_v2(r['scores'], prev, use_full_mahalanobis=False)
    sp_v2_full = score_processor_v2(r['scores'], prev, use_full_mahalanobis=True)
    return sp_v2_diag, sp_v2_full


def v3_at(td, series):
    """Return V3 dynamic score results dict for a given date."""
    from scraper.scoring_v3 import compute_scores_v3
    from scraper.orchestrator import orchestrate
    r = compute_at(td, series)
    prev = _prev_scores(td, series)

    # Additional series lookups for metrics not in compute_at()'s raw_vals
    dxy_val, _      = nearest_strict(series.get('dxy', []), td)
    fr_val, _       = nearest_strict(series.get('funding_rate', []), td)
    pi_val, _       = nearest_strict(series.get('pi_gap_pct', []), td)

    # Reconstruct raw dict with correct keys for scoring_v3's key_mapping.
    # Build cipherb dict from weekly+daily historical files (same structure as live scraper).
    wk_score, _ = nearest_strict(series.get('cipherb_weekly', []), td)
    wk_fb, _    = nearest_strict(series.get('cipherb_weekly_fb', []), td)
    cb_daily_val, _ = nearest_strict(series.get('cipherb', []), td)
    if wk_score is not None:
        cb_dict = {
            'weekly_score':     wk_score,
            'daily_score':      cb_daily_val,
            'fast_bearish_div': bool(wk_fb),
        }
    elif cb_daily_val is not None:
        cb_dict = {'weekly_score': cb_daily_val, 'daily_score': cb_daily_val, 'fast_bearish_div': False}
    else:
        cb_dict = None

    raw = {
        'nupl':               r['raw'].get('nupl'),
        'mvrv':               r['raw'].get('mvrv'),
        'rhodl_ratio':        r['raw'].get('rhodl_ratio'),
        'cvdd_ratio':         r['raw'].get('cvdd_ratio'),
        'asopr':              r['raw'].get('asopr'),
        'puell_multiple':     r['raw'].get('puell_multiple'),
        'mayer_multiple':     r['raw'].get('mayer_multiple'),
        'fear_greed':         r['raw'].get('fear_greed'),
        'm2_mom':             r['raw'].get('m2_yoy'),
        'yield_curve':        r['raw'].get('yield_curve_spread'),
        'lth_supply_pct':     r['raw'].get('lth_supply'),
        'dxy':                dxy_val,
        'funding_rate':       fr_val,
        'pi_cycle':           pi_val,
        'cipherb':            cb_dict,
        'etf_flows':          r['raw'].get('etf_flows'),
    }
    
    # Load scores_history causally from scores.json if available
    scores_history_dicts = None
    scores_history_tuples = None
    btc_price = None
    try:
        scores_path = 'data/history/scores.json'
        if os.path.exists(scores_path):
            with open(scores_path, encoding='utf-8') as f:
                history_data = json.load(f)
                scores_history_dicts = history_data
                scores_history_tuples = [
                    (x['date'], x.get('final_score'), x.get('phase'), x.get('w_bot'))
                    for x in history_data if x.get('date')
                ]
                # Find current price causally
                td_str = td.isoformat()
                for x in reversed(history_data):
                    if x.get('date') == td_str and x.get('btc_price') is not None:
                        btc_price = float(x['btc_price'])
                        break
    except Exception:
        pass
        
    v3_out = compute_scores_v3(raw, target_date=td, prev_scores=prev, scores_history=scores_history_tuples)
    
    # Run Orchestrator V3
    v3_sig = orchestrate(
        v2_score         = v3_out['final_score'],
        v2_oc_coherence  = v3_out.get('oc_coherence', 1.0),
        wr_score         = None, # No WR in milestone backtest
        wr_coherence     = 0,
        tiz_maturity     = v3_out.get('tiz_maturity'),
        top_signal       = v3_out.get('top_signal'),
        bot_signal       = v3_out.get('bot_signal'),
        is_v3            = True,
        btc_price        = btc_price,
        target_date      = td,
        scores_history   = scores_history_dicts
    )
    
    # Blend orchestrated score into the return output
    v3_out['final_score'] = v3_sig['meta_score']
    v3_out['bear_div'] = v3_sig.get('bear_div', False)
    v3_out['bull_div'] = v3_sig.get('bull_div', False)
    return v3_out


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

    # ── Compute v2 scores ────────────────────────────────────────────────────
    v2_scores = {}
    for date_str, label, price, _ in results:
        td = datetime.date.fromisoformat(date_str)
        v2_scores[date_str] = v2_at(td, series)

    # ── Compute v3 scores ────────────────────────────────────────────────────
    v3_scores = {}
    for date_str, label, price, _ in results:
        td = datetime.date.fromisoformat(date_str)
        try:
            v3_scores[date_str] = v3_at(td, series)
        except Exception as e:
            print(f"Error computing V3 at {date_str}: {e}")
            v3_scores[date_str] = {}

    # ── Score table ──────────────────────────────────────────────────────────
    header_metrics = " ".join(f"{SHORT[c]:>4}" for c in SCORE_COLS)
    print(f"\n{'Date':<12} {'Label':<22} {'BTC':>8}  {'OC':>4} {'TC':>4} {'v1':>4} {'diag':>5} {'full':>5} {'v3':>4}  {header_metrics}")
    print("─" * 162)

    for date_str, label, price, r in results:
        sc = r['scores']
        metric_vals = " ".join(fmt(sc.get(c)) for c in SCORE_COLS)
        idx_str  = f"{r['final']:3d}" if r['final'] is not None else " ✗ "
        oc_str   = fmt(r['oc'])
        tc_str   = fmt(r['tech'])
        v2_pair  = v2_scores.get(date_str, (None, None))
        diag_s, full_s = v2_pair if v2_pair else (None, None)
        diag_str = f"{diag_s:4d}" if diag_s is not None else "  — "
        full_str = f"{full_s:4d}" if full_s is not None else "  — "
        
        v3_res   = v3_scores.get(date_str, {})
        v3_val   = v3_res.get('final_score')
        
        if v3_val is not None:
            if v3_res.get('bear_div'):
                v3_str = f"{v3_val:2d}*"
            elif v3_res.get('bull_div'):
                v3_str = f"{v3_val:2d}#"
            else:
                v3_str = f"{v3_val:3d}"
        else:
            v3_str = "  ✗ "
        
        print(f"{date_str:<12} {label:<22} ${price:>8,}  {oc_str} {tc_str} {idx_str} {diag_str}  {full_str} {v3_str}  {metric_vals}")

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
