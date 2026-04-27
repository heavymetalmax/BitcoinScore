"""Scraper for MVRV Z-Score metric."""
import re
import logging
from playwright.sync_api import sync_playwright
from scraper.mm_utils import (
    MM, _get_selector_text, _fetch_page_content,
    _extract_near_keyword, _parse_number, _read_cached_stats_for_metric,
)

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
    """Return MVRV Z-Score as float, e.g. 0.80."""
    try:
        cached = _read_cached_stats_for_metric('mvrv')
        if cached:
            stats = cached.get('stats') or []
            if stats and stats[0].get('val'):
                return _parse_number(re.sub(r'[^0-9\-\.,]', '', stats[0].get('val')))
    except Exception:
        pass

    # Primary: BitcoinMagazinePro (reliable, not Cloudflare-blocked)
    val = _get_mvrv_from_bitcoinmagazine()
    if val is not None:
        return val

    # Fallback: MacroMicro (may be blocked by Cloudflare)
    url = MM['mvrv']
    sel_text = _get_selector_text(url, 'div.stat-val span.val')
    if sel_text:
        parsed = _parse_number(re.sub(r'[^0-9\-\.,]', '', sel_text))
        if parsed is not None and -10 <= parsed <= 20:
            return parsed

    html = _fetch_page_content(url)
    found = _extract_near_keyword(html, ['mvrv', 'z-score', 'mvrv z-score'], r'-?\d{1,2}[\.,]?\d{1,2}')
    if found:
        val = _parse_number(found)
        if val is not None and -10 <= val <= 20:
            return val
    return None


__all__ = ['get_mvrv']


