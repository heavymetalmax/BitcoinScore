"""Save Global M2 series from BMP Global Liquidity chart to data/history/global_m2.json."""
import json
import os
from playwright.sync_api import sync_playwright

OUT_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'history', 'global_m2.json')

url = 'https://www.bitcoinmagazinepro.com/charts/global-liquidity/'
print("Fetching BMP Global Liquidity chart (this may take ~30s)...")

with sync_playwright() as p:
    br = p.chromium.launch(headless=True)
    ctx = br.new_context(user_agent=(
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ))
    pg = ctx.new_page()
    pg.goto(url, wait_until='networkidle', timeout=60000)
    pg.wait_for_timeout(5000)
    raw = pg.evaluate("""() => {
        const el = document.querySelector('.js-plotly-plot');
        if (!el || !el._fullData) return null;
        const m2 = el._fullData.find(t => t.name === 'Global M2');
        if (!m2) return null;
        return m2.x.map((x, i) => ({date: x.slice(0,10), value: m2.y[i]}));
    }""")
    br.close()

if not raw:
    print("ERROR: no data")
    raise SystemExit(1)

series = [p for p in raw if p['value'] is not None]
series.sort(key=lambda x: x['date'])

result = {
    "source": "BitcoinMagazinePro Global Liquidity chart — Global M2 trace",
    "description": "Global M2 index (normalized, ~2013=53.5, ~2025=104). "
                   "Combines Fed + ECB + PBoC + BoJ + other central banks M2.",
    "series": series,
}

out_path = os.path.normpath(OUT_FILE)
with open(out_path, 'w') as f:
    json.dump(result, f, separators=(',', ':'))

first = series[0]
last = series[-1]
print(f"Saved {len(series)} points: {first['date']} ({first['value']:.2f}) → {last['date']} ({last['value']:.2f})")
print(f"Output: {out_path}")
