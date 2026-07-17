"""
One-time BMP backfill: fetch full daily history for all on-chain metrics via Playwright.

Updates data/history/{metric}_history.json files with daily data from BMP.
Then merges fetched metrics into scores.json (ONE database) and rebuilds backfill_scores.json.

Run: python tools/backfill_bmp.py
"""
import sys, os, json, datetime
sys.path.insert(0, '.')

from scraper.mm_utils import get_bmp_trace_full
from playwright.sync_api import sync_playwright

HIST_DIR = 'data/history'

# Simple metrics: (output_file, url, trace_name, multiply)
SOURCES = [
    ('nupl_history.json',
     'https://www.bitcoinmagazinepro.com/charts/relative-unrealized-profit--loss/',
     'nupl', 1.0),
    ('mvrv_history.json',
     'https://www.bitcoinmagazinepro.com/charts/mvrv-zscore/',
     'z-score', 1.0),
    ('rhodl_history.json',
     'https://www.bitcoinmagazinepro.com/charts/rhodl-ratio/',
     'rhodl ratio', 1.0),
    ('asopr_history.json',
     'https://www.bitcoinmagazinepro.com/charts/sopr-spent-output-profit-ratio/',
     'adjusted', 1.0),
    ('puell_history.json',
     'https://www.bitcoinmagazinepro.com/charts/puell-multiple/',
     'puell', 1.0),
]


def _fetch_cvdd_ratio_full():
    """Fetch full CVDD ratio history (btc_price / cvdd) from BMP in one session."""
    url = 'https://www.bitcoinmagazinepro.com/charts/bitcoin-price-prediction/'
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context().new_page()
            page.goto(url, wait_until='networkidle', timeout=90000)
            page.wait_for_timeout(4000)
            js = """() => {
                const gd = document.querySelector('.js-plotly-plot');
                if (!gd || !gd.data) return null;
                let price = null, cvdd = null;
                for (const t of gd.data) {
                    const n = (t.name || '').toLowerCase();
                    if (n.includes('btc price') && t.x && t.y && t.y.length > 50) price = t;
                    if (n.includes('cvdd') && t.x && t.y && t.y.length > 50) cvdd = t;
                }
                if (!price || !cvdd) return null;
                return {px: price.x, py: price.y, cx: cvdd.x, cy: cvdd.y};
            }"""
            res = page.evaluate(js)
            browser.close()
            if not res:
                return []
            # align by date
            cvdd_by_date = {str(d)[:10]: float(v) for d, v in zip(res['cx'], res['cy']) if v}
            out = []
            for d, p_val in zip(res['px'], res['py']):
                date = str(d)[:10]
                c_val = cvdd_by_date.get(date)
                if p_val and c_val and c_val > 0:
                    out.append({'date': date, 'btc_price': round(float(p_val), 2),
                                'cvdd_price': round(c_val, 2),
                                'ratio': round(float(p_val) / c_val, 4)})
            return out
    except Exception as e:
        print(f'  FAILED: {e}')
        return []


def _merge_and_save(path, new_pts):
    """Merge new_pts [[date, value], ...] into existing file, de-dup by date."""
    by_date = {}
    if os.path.exists(path):
        raw = json.load(open(path, encoding='utf-8'))
        for row in raw:
            if isinstance(row, list):
                by_date[row[0]] = row[1]
            elif isinstance(row, dict):
                d = row.get('date', '')[:10]
                v = row.get('value')
                if d and v is not None:
                    by_date[d] = v
    for d, v in new_pts:
        by_date[d] = v
    merged = sorted([d, v] for d, v in by_date.items())
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(merged, f)
    return len(merged)


def _merge_and_save_cvdd(path, new_rows):
    """Merge CVDD ratio rows (list of dicts with date/btc_price/cvdd_price/ratio)."""
    by_date = {}
    if os.path.exists(path):
        raw = json.load(open(path, encoding='utf-8'))
        series = raw.get('series', raw) if isinstance(raw, dict) else raw
        for row in series:
            if isinstance(row, dict):
                by_date[row['date'][:10]] = row
    for row in new_rows:
        by_date[row['date']] = row
    merged = sorted(by_date.values(), key=lambda r: r['date'])
    out = {'source': 'BMP CVDD + BTC price', 'n': len(merged),
           'first': merged[0]['date'] if merged else '', 'last': merged[-1]['date'] if merged else '',
           'series': merged}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f)
    return len(merged)


def main():
    print('BMP Full History Backfill')
    print('=' * 50)

    for fname, url, trace, mult in SOURCES:
        path = os.path.join(HIST_DIR, fname)
        metric = fname.replace('_history.json', '')
        print(f'\n{metric}  [{trace}]')
        pts = get_bmp_trace_full(url, trace, multiply=mult)
        if not pts:
            print(f'  FAILED — no data returned')
            continue
        print(f'  Got {len(pts)} pts  ({pts[0][0]} → {pts[-1][0]})')
        total = _merge_and_save(path, pts)
        print(f'  Saved  {total} rows → {path}')

    # CVDD needs both traces simultaneously
    print('\ncvdd_ratio  [btc price + cvdd]')
    cvdd_rows = _fetch_cvdd_ratio_full()
    if cvdd_rows:
        print(f'  Got {len(cvdd_rows)} pts  ({cvdd_rows[0]["date"]} → {cvdd_rows[-1]["date"]})')
        total = _merge_and_save_cvdd(os.path.join(HIST_DIR, 'cvdd_ratio_history.json'), cvdd_rows)
        print(f'  Saved  {total} rows → {HIST_DIR}/cvdd_ratio_history.json')
    else:
        print('  FAILED')

    print('\n' + '=' * 50)
    import subprocess

    # Step 1: rebuild unified_history.json from *_history.json files
    print('Step 1: rebuilding unified_history.json from metric files...')
    r1 = subprocess.run(['python3', 'tools/build_unified_history.py'], capture_output=True, text=True)
    print(r1.stdout[-500:] if r1.stdout else '')
    if r1.returncode != 0:
        print('ERROR:', r1.stderr[-300:])

    # Step 2: merge unified_history.json into scores.json (ONE database)
    print('Step 2: merging into scores.json (ONE database)...')
    r2 = subprocess.run(['python3', 'tools/migrate_one_db.py'], capture_output=True, text=True)
    print(r2.stdout[-500:] if r2.stdout else '')
    if r2.returncode != 0:
        print('ERROR:', r2.stderr[-300:])

    print('\nDone. Commit the updated data/history/ files and re-run CI.')


if __name__ == '__main__':
    main()
