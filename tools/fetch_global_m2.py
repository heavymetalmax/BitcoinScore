"""Fetch Global M2 from BMP chart and compare with BTC milestones."""
import datetime
from playwright.sync_api import sync_playwright

MILESTONES = [
    ("2018-12-15", "Cycle bottom",   3_200),
    ("2020-03-13", "COVID crash",    3_800),
    ("2021-04-14", "Spring ATH",    63_500),
    ("2021-11-10", "Nov ATH",       69_000),
    ("2022-06-18", "Capitulation",  17_600),
    ("2022-11-21", "FTX bottom",    15_500),
    ("2024-03-14", "2024 ATH",      73_500),
    ("2025-01-20", "Jan 2025 top", 109_000),
    ("2025-09-29", "ATH",          129_000),
    ("2026-04-25", "Today",         77_500),
]

url = 'https://www.bitcoinmagazinepro.com/charts/global-liquidity/'
print("Fetching BMP Global Liquidity chart...")
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
        return m2.x.map((x, i) => [x.slice(0,10), m2.y[i]]);
    }""")
    br.close()

if not raw:
    print("ERROR: no data returned")
    raise SystemExit(1)

series = {d: v for d, v in raw if v is not None}
dates_sorted = sorted(series.keys())
print(f"Global M2 series: {len(series)} points  {dates_sorted[0]} → {dates_sorted[-1]}")
print()


def closest_val(target_date_str):
    td = datetime.date.fromisoformat(target_date_str)
    best = min(dates_sorted, key=lambda d: abs((datetime.date.fromisoformat(d) - td).days))
    return series[best], best


print(f"{'Date':<12} {'Label':<16} {'BTC':>9}  {'Global M2':>10}  {'YoY %':>8}  {'MoM %':>7}")
print("-" * 70)
for date_str, label, btc in MILESTONES:
    val, actual_date = closest_val(date_str)

    td = datetime.date.fromisoformat(date_str)

    yoy_date = (td - datetime.timedelta(days=365)).isoformat()
    val_yoy, _ = closest_val(yoy_date)
    yoy_pct = round((val - val_yoy) / val_yoy * 100, 1) if val_yoy else None

    mom_date = (td - datetime.timedelta(days=30)).isoformat()
    val_mom, _ = closest_val(mom_date)
    mom_pct = round((val - val_mom) / val_mom * 100, 2) if val_mom else None

    print(f"{date_str:<12} {label:<16} ${btc:>8,}  {val:>10.1f}  {yoy_pct:>7.1f}%  {mom_pct:>6.2f}%")

print()
print("Expected pattern: YoY % should be HIGH at ATH, LOW/negative at bottoms.")
