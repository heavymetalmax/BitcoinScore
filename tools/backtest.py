"""
Retrospective check — compute Bitcoin Buy Risk at key historical BTC price milestones.

Data sources:
  - NUPL, MVRV, CVDD, RHODL, SOPR, Addr-in-loss : BitcoinMagazinePro (Playwright)
  - DXY, M2 MoM                                  : FRED public CSVs
  - Fear & Greed                                  : alternative.me historical API
  - CipherB                                       : data/history/cipherb_btcusdt_1w.json

SMC and Geopolitical Risk are excluded from the backtest (not available historically).
Weights are renormalised over available metrics automatically.
"""

import sys, json, math, datetime, urllib.request, ssl, io, time
sys.path.insert(0, '.')

from scraper.scoring import (
    map_nupl, map_mvrv, map_fear_greed,
    map_m2, map_yield_curve, map_cvdd, map_rhodl, map_asopr, map_etf_flow, OC_WEIGHTS, TECH_WEIGHTS, weighted_score
)
from scraper.smc import fetch_ohlcv_kraken as fetch_ohlcv_binance, compute_smc

# ── Key BTC milestones ───────────────────────────────────────────────────────
MILESTONES = [
    ("2018-12-15", "Cycle bear bottom",    3_200),
    ("2019-06-26", "Local peak",          13_880),
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
    ("2025-09-26", "Close ATH",          123_000),
    ("2025-09-29", "Intraday ATH",       129_000),
    ("2025-11-10", "Post-ATH dump",       94_000),
    ("2026-04-25", "Today",               77_500),
]

# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_date(s):
    return datetime.date.fromisoformat(s)

def closest(series, target_date):
    """Return value from series [(date_str_or_date, value), ...] closest to target_date."""
    best, best_val = None, None
    for d, v in series:
        if isinstance(d, str):
            d = parse_date(d[:10])
        diff = abs((d - target_date).days)
        if best is None or diff < best:
            best, best_val = diff, v
    return best_val

def ssl_ctx():
    ctx = ssl.create_default_context()
    return ctx

# ── 1. Fetch BMP full time series via Playwright ─────────────────────────────

BMP_CHARTS = {
    'nupl':    ('https://www.bitcoinmagazinepro.com/charts/relative-unrealized-profit--loss/',
                'net unrealised', 1.0),        # multiply by 100 (comes as fraction 0-1)
    'mvrv':   ('https://www.bitcoinmagazinepro.com/charts/mvrv-zscore/',
                'mvrv z-score', 1.0),
    'addr':   ('https://www.bitcoinmagazinepro.com/charts/percent-addresses-in-loss/',
                'addresses in loss', 1.0),     # will be inverted later
    'rhodl':  ('https://www.bitcoinmagazinepro.com/charts/rhodl-ratio/',
                'rhodl ratio', 1.0),
    'cvdd':   ('https://www.bitcoinmagazinepro.com/charts/bitcoin-price-prediction/',
                'cvdd', 1.0),                  # BTC/CVDD ratio computed below
}

def fetch_bmp_series(url, trace_keyword, multiply=1.0):
    """Return list of (date_str, value) for a BMP Plotly chart."""
    from playwright.sync_api import sync_playwright

    JS = """(kw) => {
        const el = document.querySelector('.js-plotly-plot');
        if (!el || !el._fullData) return null;
        const t = el._fullData.find(t => t.name && t.name.toLowerCase().includes(kw));
        if (!t) return {names: el._fullData.map(t => t.name)};
        return t.x.map((x, i) => [x, t.y[i]]);
    }"""

    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        ctx = br.new_context(user_agent=(
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ))
        pg = ctx.new_page()
        pg.goto(url, wait_until='networkidle', timeout=90_000)
        pg.wait_for_timeout(5_000)
        raw = pg.evaluate(JS, trace_keyword.lower())
        br.close()

    if not isinstance(raw, list):
        print(f"  WARNING: no trace for '{trace_keyword}' at {url}: {raw}")
        return []
    result = []
    for x, y in raw:
        if y is None:
            continue
        result.append((x[:10], y * multiply))
    return result


def fetch_cvdd_ratio_series():
    """
    CVDD ratio = BTC price / CVDD.
    BMP chart has both 'Bitcoin Price' and 'CVDD' traces.
    Returns [(date, ratio), ...].
    """
    from playwright.sync_api import sync_playwright

    JS = """() => {
        const el = document.querySelector('.js-plotly-plot');
        if (!el || !el._fullData) return null;
        const btc  = el._fullData.find(t => t.name && t.name.toLowerCase().includes('btc price'));
        const cvdd = el._fullData.find(t => t.name && t.name.toLowerCase() === 'cvdd');
        if (!btc || !cvdd) return {names: el._fullData.map(t => t.name)};
        const out = [];
        for (let i = 0; i < btc.x.length; i++) {
            if (btc.y[i] && cvdd.y[i] && cvdd.y[i] !== 0)
                out.push([btc.x[i], btc.y[i] / cvdd.y[i]]);
        }
        return out;
    }"""

    url = 'https://www.bitcoinmagazinepro.com/charts/bitcoin-price-prediction/'
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        ctx = br.new_context(user_agent=(
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ))
        pg = ctx.new_page()
        pg.goto(url, wait_until='networkidle', timeout=90_000)
        pg.wait_for_timeout(5_000)
        raw = pg.evaluate(JS)
        br.close()

    if not isinstance(raw, list):
        print(f"  WARNING cvdd_ratio: {raw}")
        return []
    return [(x[:10], y) for x, y in raw]

# ── 2. FRED ──────────────────────────────────────────────────────────────────

def fetch_fred_series(series_id):
    """Return list of (date_str, float) from FRED CSV."""
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    req = urllib.request.Request(url, headers={'User-Agent': 'curl/7.88'})
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as r:
        lines = r.read().decode().strip().splitlines()
    result = []
    for line in lines:
        if line.startswith('DATE') or not line:
            continue
        parts = line.split(',')
        if len(parts) != 2:
            continue
        try:
            result.append((parts[0], float(parts[1])))
        except ValueError:
            pass
    return result


def load_us_m2_yoy():
    """
    Fetch US M2 (WM2NS) from FRED and compute year-over-year % change for each point.
    Returns [(date_str, yoy_pct), ...] sorted by date.
    Direct logic: high YoY = liquidity flood = high risk score.
    """
    import csv, io, ssl, urllib.request
    url = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=WM2NS'
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={'User-Agent': 'curl/7.88'})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        text = r.read().decode()
    rows = list(csv.reader(io.StringIO(text)))
    series = [(r[0], float(r[1])) for r in rows[1:] if r[1] != '.']
    series.sort(key=lambda x: x[0])
    result = []
    for i in range(52, len(series)):
        d_str, val = series[i]
        past_val = series[i - 52][1]   # ~52 weeks ago
        yoy = round((val - past_val) / past_val * 100, 2)
        result.append((d_str, yoy))
    return result


# legacy alias
def load_us_m2_10w_momentum():
    return load_us_m2_yoy()

# ── 3. Fear & Greed ──────────────────────────────────────────────────────────

def fetch_fg_series():
    """Return list of (date_str, int) from BMP history file (data/history/fear_greed.json)."""
    import os
    path = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'data', 'history', 'fear_greed.json'))
    with open(path) as f:
        data = json.load(f)
    result = [(p['date'], int(p['score'])) for p in data['series'] if p.get('score') is not None]
    result.sort(key=lambda x: x[0])
    return result

# ── 4. CipherB (weekly) ──────────────────────────────────────────────────────

def load_cipherb_series():
    """Return [(date_str, adjusted_score), ...] from existing JSON."""
    with open('data/history/cipherb_btcusdt_1w.json') as f:
        data = json.load(f)
    result = []
    for candle in data.get('series', []):
        score = candle.get('weekly_score')
        if score is None:
            continue
        if candle.get('fast_bearish_div'):
            score = min(100.0, score + 12.0)
        elif candle.get('fast_bullish_div'):
            score = max(0.0, score - 12.0)
        d = datetime.datetime.utcfromtimestamp(candle['timestamp']).date().isoformat()
        result.append((d, score))
    return result


def load_etf_flows_series():
    """Return [(date_str, 14d_flow_sum), ...] from data/history/etf_flows.json."""
    import os
    path = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'data', 'history', 'etf_flows.json'))
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    result = []
    for p in data:
        date_str = p['timestamp'][:10]
        val = p.get('etf_flow_14d')
        result.append((date_str, val))
    result.sort(key=lambda x: x[0])
    return result


def compute_smc_at_date(ohlcv_series, target_date, size=10):
    """
    Compute SMC position using only candles available up to target_date.
    Returns position 0-100, or None if insufficient data.
    """
    cutoff_ts = int(datetime.datetime(
        target_date.year, target_date.month, target_date.day
    ).timestamp()) + 86400  # include target day
    sliced = [c for c in ohlcv_series if c['timestamp'] <= cutoff_ts]
    if len(sliced) < size * 2 + 1:
        return None
    result = compute_smc(sliced, size=size)
    return result.get('position')

# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    print("Fetching historical data...\n")

    print("  [0/7] SMC (Binance weekly OHLCV)...")
    binance_ohlcv = fetch_ohlcv_binance(symbol='BTCUSDT', interval='1w', limit=500)
    print(f"        {len(binance_ohlcv)} weekly candles")


    print("  [1/7] NUPL from BMP...")
    nupl_series = fetch_bmp_series(
        'https://www.bitcoinmagazinepro.com/charts/relative-unrealized-profit--loss/',
        'net unrealised', multiply=100.0)
    print(f"        {len(nupl_series)} points")

    print("  [2/7] MVRV Z-score from BMP...")
    mvrv_series = fetch_bmp_series(
        'https://www.bitcoinmagazinepro.com/charts/mvrv-zscore/',
        'z-score')
    print(f"        {len(mvrv_series)} points")


    asopr_raw = fetch_bmp_series(
        'https://www.bitcoinmagazinepro.com/charts/sopr-spent-output-profit-ratio/',
        'sopr')
    
    # Process aSOPR: add 1.0 if average is near 0
    asopr_avg = sum(y for x, y in asopr_raw) / max(1, len(asopr_raw))
    if abs(asopr_avg) < 0.5:
        asopr_raw = [(x, y + 1.0) for x, y in asopr_raw]
        
    # Calculate 7-day SMA for aSOPR
    asopr_series = []
    for i in range(len(asopr_raw)):
        start_idx = max(0, i - 6)
        window = [val for _, val in asopr_raw[start_idx:i+1]]
        sma = sum(window) / len(window)
        asopr_series.append((asopr_raw[i][0], sma))
    print(f"        asopr: {len(asopr_series)} points")

    rhodl_series = fetch_bmp_series(
        'https://www.bitcoinmagazinepro.com/charts/rhodl-ratio/',
        'rhodl ratio')
    print(f"        rhodl: {len(rhodl_series)} points")

    print("  [5/7] CVDD ratio from BMP...")
    cvdd_series = fetch_cvdd_ratio_series()
    print(f"        {len(cvdd_series)} points")

    print("  [6/7] Real Yield (DFII10) from FRED + US M2 YoY from FRED...")
    ry_series   = fetch_fred_series('DFII10')
    m2_momentum = load_us_m2_10w_momentum()
    print(f"        real_yield: {len(ry_series)}, us m2 yoy: {len(m2_momentum)} points")

    print("  [7/7] Fear & Greed from alternative.me...")
    fg_series = fetch_fg_series()
    print(f"        {len(fg_series)} points")

    cb_series = load_cipherb_series()
    print(f"  CipherB weekly: {len(cb_series)} points\n")

    print("  [8/8] ETF flows from data/history/etf_flows.json...")
    etf_series = load_etf_flows_series()
    print(f"        etf_flows: {len(etf_series)} points\n")

    # ── Compute scores at milestones ─────────────────────────────────────────
    print(f"{'Date':<12} {'Label':<22} {'BTC':>8}  "
          f"{'OC':>4} {'Tech':>4} {'Final':>5}  "
          f"{'NUPL':>4} {'MVRV':>4} {'SOPR':>4} {'RHDL':>4} {'CVDD':>4}  "
          f"{'FG':>3} {'RY':>4} {'M2':>4} {'CB':>4} {'SMC':>4} {'ETF':>4}")
    print("-" * 122)

    for date_str, label, btc_price in MILESTONES:
        td = parse_date(date_str)

        nupl    = closest(nupl_series, td)
        mvrv    = closest(mvrv_series, td)
        asopr   = closest(asopr_series, td)
        rhodl   = closest(rhodl_series, td)
        cvdd    = closest(cvdd_series, td)
        ry      = closest(ry_series, td)
        m2      = closest(m2_momentum, td)
        fg      = closest(fg_series, td)
        cb      = closest(cb_series, td)
        etf_val = closest(etf_series, td)
        s_smc   = compute_smc_at_date(binance_ohlcv, td)
        if s_smc is not None:
            s_smc = round(s_smc)

        s_nupl  = map_nupl(nupl)
        s_mvrv  = map_mvrv(mvrv)
        s_asopr = map_asopr(asopr)
        s_rhodl = map_rhodl(rhodl)
        s_cvdd  = map_cvdd(cvdd)
        s_ry    = map_yield_curve(ry)
        s_m2    = map_m2(m2)
        s_fg    = map_fear_greed(fg)
        s_cb    = round(max(0, min(100, cb))) if cb is not None else None
        s_etf   = map_etf_flow(etf_val)

        oc_map = {
            'nupl': s_nupl, 'mvrv_z_score': s_mvrv,
            'rhodl_ratio': s_rhodl, 'cvdd_ratio': s_cvdd,
            'asopr': s_asopr
        }
        # geopolitical_risk excluded
        tech_map = {
            'cipherb': s_cb, 'm2_yoy': s_m2, 'fear_greed': s_fg, 'yield_curve_spread': s_ry,
            'smc': s_smc, 'etf_flows': s_etf
        }

        oc   = weighted_score(OC_WEIGHTS, oc_map)
        tech = weighted_score(TECH_WEIGHTS, tech_map)

        final = None
        if oc is not None and tech is not None:
            final = round(oc * 0.5 + tech * 0.5)
        elif oc is not None:
            final = oc
        elif tech is not None:
            final = tech

        def fmt(v):
            return f"{v:4d}" if v is not None else "  — "

        print(
            f"{date_str:<12} {label:<22} ${btc_price:>7,}  "
            f"{fmt(oc)} {fmt(tech)} {fmt(final)}  "
            f"{fmt(s_nupl)} {fmt(s_mvrv)} {fmt(s_asopr)} {fmt(s_rhodl)} {fmt(s_cvdd)}  "
            f"{fmt(s_fg)} {fmt(s_ry)} {fmt(s_m2)} {fmt(s_cb)} {fmt(s_smc)} {fmt(s_etf)}"
        )

    print("\nNote: Geopolitical Risk excluded (not available historically).")
    print("SMC computed retroactively from Binance weekly OHLCV (only data up to each milestone date used).")
    print("Binance 1w data starts 2017-08-14; milestones before that will show SMC=—.")
    print("Weights renormalised over available metrics at each date.")

if __name__ == '__main__':
    run()
