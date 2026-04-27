"""SOPR (Spent Output Profit Ratio) from Bitcoin Magazine Pro.

BMP shows an adjusted/smoothed SOPR where the value is near 0:
  - Negative = on-chain coins being sold at a loss  (bullish / accumulation)
  - Zero      = breakeven
  - Positive  = on-chain coins being sold at profit (bearish / distribution)

Typical range: -0.05 (deep capitulation) … +0.10 (strong distribution).
"""
import logging
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

SOPR_URL = 'https://www.bitcoinmagazinepro.com/charts/sopr-spent-output-profit-ratio/'


def get_sopr(timeout=60000):
    """Return the current adjusted SOPR value from BMP (float near 0, or None)."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = ctx.new_page()
            page.goto(SOPR_URL, wait_until='networkidle', timeout=timeout)
            page.wait_for_timeout(4000)
            val = page.evaluate("""() => {
                const gd = document.querySelector('.js-plotly-plot');
                if (!gd || !gd._fullData) return null;
                // BMP has two SOPR traces; the raw one may have null at the end.
                // Return the first trace that has a non-null last value.
                for (const t of gd._fullData) {
                    if (!t.name || !t.name.toLowerCase().includes('sopr')) continue;
                    if (!t.y || !t.y.length) continue;
                    const last = t.y[t.y.length - 1];
                    if (last !== null && last !== undefined) return last;
                }
                return null;
            }""")
            try:
                browser.close()
            except Exception:
                pass
            if val is not None:
                return round(float(val), 6)
    except Exception as e:
        logger.warning('get_sopr failed: %s', e)
    return None


__all__ = ['get_sopr']
