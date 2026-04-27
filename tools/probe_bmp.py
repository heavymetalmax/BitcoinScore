#!/usr/bin/env python3
"""Probe BitcoinMagazinePro chart pages and extract Plotly trace names + last values."""
from playwright.sync_api import sync_playwright

CANDIDATES = {
    'nupl':             'https://www.bitcoinmagazinepro.com/charts/relative-unrealized-profit--loss/',
    'mvrv':             'https://www.bitcoinmagazinepro.com/charts/mvrv-zscore/',
    'addresses_loss':   'https://www.bitcoinmagazinepro.com/charts/percent-addresses-in-loss/',
    'profit_loss_ratio':'https://www.bitcoinmagazinepro.com/charts/profit-loss-ratio/',
    'realized_profit':  'https://www.bitcoinmagazinepro.com/charts/realized-profit-loss/',
    'large_holder':     'https://www.bitcoinmagazinepro.com/charts/addresses-greater-than-1k-btc/',
    'supply_profit':    'https://www.bitcoinmagazinepro.com/charts/percent-supply-in-profit/',
}

JS = """() => {
    const gd = document.querySelector('.js-plotly-plot');
    if(!gd) return {err: 'no plotly'};
    const traces = gd.data;
    if(!traces || !traces.length) return {err: 'no traces'};
    return traces.map(t => ({
        name: t.name,
        lastY: (t.y && t.y.length) ? t.y[t.y.length - 1] : null
    }));
}"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for key, url in CANDIDATES.items():
        try:
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(url, wait_until='networkidle', timeout=60000)
            page.wait_for_timeout(4000)
            res = page.evaluate(JS)
            ctx.close()
            print(f'\n=== {key} ===')
            if isinstance(res, dict) and 'err' in res:
                print('  ERROR:', res['err'])
            else:
                for t in (res or []):
                    print(f"  trace '{t['name']}': {t['lastY']}")
        except Exception as e:
            print(f'\n=== {key} === ERROR: {e}')
    browser.close()
