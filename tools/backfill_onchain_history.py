"""
One-off backfill of FULL multi-cycle on-chain history from BitcoinMagazinePro.

The BMP Plotly charts already contain the entire daily series (2010→today) —
the live scraper only reads the last point. This pulls the whole trace for each
on-chain metric and saves it to data/history/<metric>_history.json as
[["YYYY-MM-DD", value], ...]. That history is what adaptive normalisation
(rolling-percentile / decaying-envelope) needs — see tools/adaptive_norm_probe.py.

Requires Playwright (runs in the same env as the scraper, e.g. the GitHub
Actions playwright container). Run once; re-run to refresh.

    python tools/backfill_onchain_history.py            # all metrics
    python tools/backfill_onchain_history.py nupl mvrv   # subset
"""
import sys, os, json
sys.path.insert(0, '.')
from scraper.mm_utils import get_bmp_trace_full

# metric -> (chart URL, trace-name substring, multiply)  [matches the live scraper]
SOURCES = {
    'nupl':          ('https://www.bitcoinmagazinepro.com/charts/relative-unrealized-profit--loss/', 'nupl',              100.0),
    'mvrv':          ('https://www.bitcoinmagazinepro.com/charts/mvrv-zscore/',                       'z-score',           1.0),
    'rhodl':         ('https://www.bitcoinmagazinepro.com/charts/rhodl-ratio/',                       'rhodl ratio',       1.0),
    'cvdd':          ('https://www.bitcoinmagazinepro.com/charts/bitcoin-price-prediction/',          'cvdd',              1.0),
    'asopr':         ('https://www.bitcoinmagazinepro.com/charts/sopr-spent-output-profit-ratio/',    'adjusted',          1.0),
    'mayer':         ('https://www.bitcoinmagazinepro.com/charts/mayer-multiple/',                    'mayer',             1.0),
    'puell':         ('https://www.bitcoinmagazinepro.com/charts/puell-multiple/',                    'puell',             1.0),
    'lth_supply':    ('https://www.bitcoinmagazinepro.com/charts/long-term-holder-supply/',           'long',              1.0),
    'pi_cycle':      ('https://www.bitcoinmagazinepro.com/charts/pi-cycle-top-indicator/',            '111',               1.0),
}

def main(metrics):
    os.makedirs('data/history', exist_ok=True)
    for m in metrics:
        if m not in SOURCES:
            print(f"  ? unknown metric '{m}' (known: {', '.join(SOURCES)})")
            continue
        url, needle, mul = SOURCES[m]
        print(f"… {m}: fetching full trace from BMP")
        series = get_bmp_trace_full(url, needle, multiply=mul)
        if not series:
            # asopr trace is sometimes named 'sopr' instead of 'adjusted'
            if m == 'asopr':
                series = get_bmp_trace_full(url, 'sopr', multiply=mul)
        if not series:
            print(f"  ! {m}: no data (trace name? page render?) — skipped")
            continue
        out = f'data/history/{m}_history.json'
        with open(out, 'w', encoding='utf-8') as f:
            json.dump({'metric': m, 'source': 'BitcoinMagazinePro',
                       'first': series[0][0], 'last': series[-1][0],
                       'n': len(series), 'series': series}, f)
        print(f"  ✓ {m}: {len(series)} points  {series[0][0]} → {series[-1][0]}  →  {out}")

if __name__ == '__main__':
    metrics = sys.argv[1:] or list(SOURCES.keys())
    main(metrics)
