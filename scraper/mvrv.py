"""Scraper for MVRV Z-Score metric."""
import logging
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/mvrv-zscore/'


def _get_mvrv_from_bitcoinmagazine():
    """Fetch MVRV Z-Score from BitcoinMagazinePro Plotly chart. Returns float or None."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(_BMP_URL, wait_until='networkidle', timeout=60000)
            page.wait_for_timeout(4000)
            js = """() => {
                const gd = document.querySelector('.js-plotly-plot');
                if(!gd) return null;
                const traces = gd.data;
                if(!traces) return null;
                for(const t of traces){
                    if((t.name || '').toLowerCase().includes('z-score')){
                        if(t.y && t.y.length) return t.y[t.y.length - 1];
                    }
                }
                return null;
            }"""
            res = page.evaluate(js)
            try:
                browser.close()
            except Exception:
                pass
            if res is not None:
                return round(float(res), 4)
    except Exception as e:
        logger.debug('bitcoinmagazine mvrv fetch failed: %s', e)
    return None


def get_mvrv():
    """Return MVRV Z-Score as float, e.g. 0.80.

    Source: BitcoinMagazinePro Plotly chart.
    """
    return _get_mvrv_from_bitcoinmagazine()


__all__ = ['get_mvrv']


