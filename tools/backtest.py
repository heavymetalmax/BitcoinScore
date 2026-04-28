"""
Retrospective check — compute Bitcoin Score at key historical BTC price milestones.

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
    map_nupl, map_mvrv, map_sopr, map_addr_profit, map_fear_greed,
    map_m2, map_dxy, map_cvdd, map_rhodl, OC_WEIGHTS, TECH_WEIGHTS, weighted_score
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
    'sopr':   ('https://www.bitcoinmagazinepro.com/charts/sopr-spent-output-profit-ratio/',
                'sopr', 1.0),
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


def load_global_m2_yoy():
    """
    Load Global M2 index from data/history/global_m2.json and compute
    year-over-year % change for each date.
    Returns [(date_str, yoy_pct), ...] sorted by date.
    """
    import os
    path = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'data', 'history', 'global_m2.json'))
    with open(path) as f:
        data = json.load(f)
    series = [(p['date'], float(p['value'])) for p in data['series'] if p.get('value') is not None]
    series.sort(key=lambda x: x[0])
    dates = [datetime.date.fromisoformat(d) for d, _ in series]
    result = []
    for i, (d_str, val) in enumerate(series):
        td = dates[i]
        target = td - datetime.timedelta(days=365)
        j = min(range(len(dates)), key=lambda k: abs((dates[k] - target).days))
        yoy = (val - series[j][1]) / series[j][1] * 100
        result.append((d_str, round(yoy, 2)))
    return result

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
    """Return [(date_str, weekly_score), ...] from existing JSON."""
    with open('data/history/cipherb_btcusdt_1w.json') as f:
        data = json.load(f)
    result = []
    for candle in data.get('series', []):
        if candle.get('weekly_score') is None:
            continue
        d = datetime.datetime.utcfromtimestamp(candle['timestamp']).date().isoformat()
        result.append((d, candle['weekly_score']))
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

    print("  [3/7] SOPR from BMP...")
    sopr_raw = []
    # BMP has two complementary SOPR traces that alternate non-null values — merge them
    from playwright.sync_api import sync_playwright as _pw
    with _pw() as p:
        br = p.chromium.launch(headless=True)
        ctx = br.new_context(user_agent=(
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ))
        pg = ctx.new_page()
        pg.goto('https://www.bitcoinmagazinepro.com/charts/sopr-spent-output-profit-ratio/',
                wait_until='networkidle', timeout=90_000)
        pg.wait_for_timeout(5_000)
        sopr_raw = pg.evaluate("""() => {
            const el = document.querySelector('.js-plotly-plot');
            if (!el || !el._fullData) return [];
            const merged = {};
            for (const t of el._fullData) {
                if (!t.name || !t.name.toLowerCase().includes('sopr')) continue;
                if (!t.x || !t.y) continue;
                for (let i = 0; i < t.x.length; i++) {
                    const v = t.y[i];
                    if (v !== null && v !== undefined) merged[t.x[i].slice(0,10)] = v;
                }
            }
            return Object.entries(merged).sort((a,b) => a[0] < b[0] ? -1 : 1);
        }""")
        br.close()
    # Values are already delta-format (near 0), no - 1.0 needed
    sopr_series = [(d, v) for d, v in sopr_raw]
    print(f"        {len(sopr_series)} points")

    print("  [4/7] Addresses in loss + RHODL + CVDD from BMP...")
    addr_series = fetch_bmp_series(
        'https://www.bitcoinmagazinepro.com/charts/percent-addresses-in-loss/',
        'addresses in loss')
    # addr_series = fraction 0-1 → profit% = (1 - frac) * 100
    addr_profit_series = [(d, (1.0 - v) * 100.0) for d, v in addr_series]
    print(f"        addr: {len(addr_series)} points")

    rhodl_series = fetch_bmp_series(
        'https://www.bitcoinmagazinepro.com/charts/rhodl-ratio/',
        'rhodl ratio')
    print(f"        rhodl: {len(rhodl_series)} points")

    print("  [5/7] CVDD ratio from BMP...")
    cvdd_series = fetch_cvdd_ratio_series()
    print(f"        {len(cvdd_series)} points")

    print("  [6/7] DXY from FRED + Global M2 YoY from history...")
    dxy_series  = fetch_fred_series('DTWEXBGS')
    m2_yoy      = load_global_m2_yoy()
    print(f"        dxy: {len(dxy_series)}, global m2 yoy: {len(m2_yoy)} points")

    print("  [7/7] Fear & Greed from alternative.me...")
    fg_series = fetch_fg_series()
    print(f"        {len(fg_series)} points")

    cb_series = load_cipherb_series()
    print(f"  CipherB weekly: {len(cb_series)} points\n")

    # ── Compute scores at milestones ─────────────────────────────────────────
    print(f"{'Date':<12} {'Label':<22} {'BTC':>8}  "
          f"{'OC':>4} {'Tech':>4} {'Final':>5}  "
          f"{'NUPL':>4} {'MVRV':>4} {'SOPR':>4} {'Addr':>4} {'RHODL':>5} {'CVDD':>4}  "
          f"{'FG':>3} {'DXY':>4} {'M2':>4} {'CB':>4} {'SMC':>4}")
    print("-" * 121)

    for date_str, label, btc_price in MILESTONES:
        td = parse_date(date_str)

        nupl    = closest(nupl_series, td)
        mvrv    = closest(mvrv_series, td)
        sopr    = closest(sopr_series, td)
        addr    = closest(addr_profit_series, td)
        rhodl   = closest(rhodl_series, td)
        cvdd    = closest(cvdd_series, td)
        dxy     = closest(dxy_series, td)
        m2      = closest(m2_yoy, td)
        fg      = closest(fg_series, td)
        cb      = closest(cb_series, td)
        s_smc   = compute_smc_at_date(binance_ohlcv, td)
        if s_smc is not None:
            s_smc = round(s_smc)

        s_nupl  = map_nupl(nupl)
        s_mvrv  = map_mvrv(mvrv)
        s_sopr  = map_sopr(sopr)
        s_addr  = map_addr_profit(addr)
        s_rhodl = map_rhodl(rhodl)
        s_cvdd  = map_cvdd(cvdd)
        s_dxy   = map_dxy(dxy)
        s_m2    = map_m2(m2)
        s_fg    = map_fear_greed(fg)
        s_cb    = round(max(0, min(100, cb))) if cb is not None else None

        oc_map = {
            'nupl': s_nupl, 'mvrv_z_score': s_mvrv, 'sopr': s_sopr,
            'addresses_in_profit': s_addr, 'rhodl_ratio': s_rhodl, 'cvdd_ratio': s_cvdd,
        }
        # geopolitical_risk excluded
        tech_map = {
            'cipherb': s_cb, 'm2_mom': s_m2, 'fear_greed': s_fg, 'dxy': s_dxy,
            'smc': s_smc,
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
            f"{fmt(s_nupl)} {fmt(s_mvrv)} {fmt(s_sopr)} {fmt(s_addr)} {fmt(s_rhodl)} {fmt(s_cvdd)}  "
            f"{fmt(s_fg)} {fmt(s_dxy)} {fmt(s_m2)} {fmt(s_cb)} {fmt(s_smc)}"
        )

    print("\nNote: Geopolitical Risk excluded (not available historically).")
    print("SMC computed retroactively from Binance weekly OHLCV (only data up to each milestone date used).")
    print("Binance 1w data starts 2017-08-14; milestones before that will show SMC=—.")
    print("Weights renormalised over available metrics at each date.")

if __name__ == '__main__':
    run()
