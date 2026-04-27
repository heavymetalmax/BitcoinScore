"""Save Fear & Greed history from BMP chart to data/history/fear_greed.json.

F&G data is in customdata[2] (score) and customdata[3] (label) of the
'Fear & Greed Score' trace. The y values are BTC price (secondary axis).
"""
import json
import os
from playwright.sync_api import sync_playwright

OUT_FILE = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'data', 'history', 'fear_greed.json'))

url = 'https://www.bitcoinmagazinepro.com/charts/bitcoin-fear-and-greed-index/'
print("Fetching BMP Fear & Greed chart...")

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
        const fg = el._fullData.find(t => t.name && t.name.toLowerCase().includes('fear'));
        if (!fg || !fg.customdata) return null;
        return fg.customdata.map(cd => ({
            date:  cd[0] ? cd[0].slice(0, 10) : null,
            score: cd[2],
            label: cd[3],
        }));
    }""")
    br.close()

if not raw:
    print("ERROR: no data")
    raise SystemExit(1)

series = [p for p in raw if p['date'] and p['score'] is not None]
series.sort(key=lambda x: x['date'])

result = {
    "source": "BitcoinMagazinePro — Fear & Greed Index chart (customdata)",
    "description": "Daily BTC Fear & Greed score (0-100) with label. 2018-02-01 onward.",
    "series": series,
}

os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
with open(OUT_FILE, 'w') as f:
    json.dump(result, f, separators=(',', ':'))

first, last = series[0], series[-1]
print(f"Saved {len(series)} points: {first['date']} (score={first['score']}) → {last['date']} (score={last['score']})")
print(f"Output: {OUT_FILE}")
