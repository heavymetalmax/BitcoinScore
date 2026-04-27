"""Shared Playwright + parsing utilities for MacroMicro scrapers."""
from playwright.sync_api import sync_playwright
import re
import logging
import os
import json
import glob

MM = {
    'nupl': 'https://en.macromicro.me/series/45910/bitcoin-nupl',
    'mvrv': 'https://en.macromicro.me/series/8365/bitcoin-mvrv-zscore',
    'profit': 'https://en.macromicro.me/charts/143605/bitcoin-ratio-of-loss-and-profit-addresses',
    'loss_series': 'https://en.macromicro.me/series/78202/bitcoin-percentage-of-addresses-in-loss',
    'm2': 'https://en.macromicro.me/charts/3439/major-bank-m2-comparsion',
    'large_holder': 'https://en.macromicro.me/charts/29043/bitcoin-addresses-with-balance-more-than-or-equal-to-1k',
    'dxy': 'https://en.macromicro.me/charts/84646/BITCOIN-vs-US-DOLLAR-INDEX-DXY',
    'georisk': 'https://en.macromicro.me/charts/55589/global-geopolitical-risk-index',
}

logger = logging.getLogger(__name__)


def _new_context(browser):
    """Create a Playwright context, reusing saved storage_state if available."""
    storage = 'data/debug/storage.json'
    ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36'
    if os.path.exists(storage):
        try:
            return browser.new_context(storage_state=storage)
        except Exception:
            pass
    lt_cookie = os.environ.get('MACROMICRO_LT_CID')
    cookie_file = 'data/debug/lt_cookie.txt'
    if not lt_cookie and os.path.exists(cookie_file):
        try:
            with open(cookie_file, 'r', encoding='utf-8') as hf:
                lt_cookie = hf.read().strip()
        except Exception:
            lt_cookie = None
    ctx = browser.new_context(viewport={'width': 1920, 'height': 1080}, user_agent=ua)
    if lt_cookie:
        try:
            ck = {
                'name': '__lt__cid',
                'value': lt_cookie,
                'domain': 'en.macromicro.me',
                'path': '/',
                'httpOnly': False,
                'secure': True,
            }
            try:
                ctx.add_cookies([ck])
            except Exception:
                try:
                    p = ctx.new_page()
                    p.context.add_cookies([ck])
                    p.close()
                except Exception:
                    pass
        except Exception:
            pass
    return ctx


def _fetch_page_content(url, timeout=30000):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _new_context(browser)
            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=max(timeout, 60000))
            try:
                page.wait_for_timeout(1000)
            except Exception:
                pass
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        logger.warning('Playwright fetch failed for %s: %s', url, e)
        return None


def _extract_near_keyword(html, keywords, pattern, window=300):
    if not html:
        return None
    low = html.lower()
    for kw in keywords:
        idx = low.find(kw.lower())
        if idx >= 0:
            start = max(0, idx - window)
            end = min(len(html), idx + window)
            snippet = html[start:end]
            m = re.search(pattern, snippet)
            if m:
                return m.group(0)
    m = re.search(pattern, html)
    if m:
        return m.group(0)
    return None


def _get_selector_text(url, selector, timeout=30000):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _new_context(browser)
            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=max(timeout, 60000))
            try:
                try:
                    page.wait_for_selector(selector, timeout=min(max(timeout, 8000), 30000))
                except Exception:
                    page.wait_for_timeout(800)
            except Exception:
                pass
            loc = page.locator(selector)
            count = 0
            try:
                count = loc.count()
            except Exception:
                count = 0
            if count and count > 0:
                try:
                    text = loc.nth(0).text_content()
                    browser.close()
                    return text.strip() if text else None
                except Exception:
                    browser.close()
                    return None
            browser.close()
            return None
    except Exception as e:
        logger.warning('Playwright selector fetch failed for %s (%s): %s', url, selector, e)
        return None


def _enumerate_stat_vals(url, timeout=30000):
    """Return list of {val, unit, context} for each div.stat-val on the page."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _new_context(browser)
            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=max(timeout, 60000))
            try:
                try:
                    page.wait_for_selector('div.stat-val .val', timeout=min(max(timeout, 8000), 30000))
                except Exception:
                    page.wait_for_timeout(800)
            except Exception:
                pass
            js = '''() => {
                const nodes = Array.from(document.querySelectorAll('div.stat-val'));
                return nodes.map(n => {
                    const val = (n.querySelector('.val') && n.querySelector('.val').textContent) || null;
                    const unit = (n.querySelector('.unit') && n.querySelector('.unit').textContent) || null;
                    let ctx = null;
                    let anc = n.closest('section') || n.parentElement || n.closest('div');
                    if (anc) ctx = anc.innerText || null;
                    return {val: val, unit: unit, context: ctx};
                });
            }'''
            results = page.evaluate(js)
            browser.close()
            return results
    except Exception as e:
        logger.warning('enumerate_stat_vals failed for %s: %s', url, e)
        return []


def _find_stat_in_sidebar(url, keywords=None, timeout=30000):
    """Search the sidebar for a stat matching any keyword. Returns (val, unit) or (None, None)."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _new_context(browser)
            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=max(timeout, 60000))
            try:
                page.wait_for_timeout(800)
            except Exception:
                pass
            js = '''(kw) => {
                const container = document.querySelector('.sidebar-sec.chart-stat-lastrows');
                if(!container) return null;
                const items = Array.from(container.querySelectorAll('ul > li'));
                for(const li of items){
                    const nameEl = li.querySelector('.stat-name a');
                    const statName = nameEl? nameEl.innerText.toLowerCase(): (li.querySelector('.stat-name')? li.querySelector('.stat-name').innerText.toLowerCase(): '');
                    for(const k of kw){
                        if(k && statName.includes(k.toLowerCase())){
                            const valEl = li.querySelector('.stat-val .val');
                            const unitEl = li.querySelector('.stat-val .unit');
                            return {val: valEl? valEl.textContent.trim() : null, unit: unitEl? unitEl.textContent.trim() : null};
                        }
                    }
                }
                return null;
            }'''
            res = page.evaluate(js, keywords or [])
            browser.close()
            if res:
                return (res.get('val'), res.get('unit'))
            return (None, None)
    except Exception as e:
        logger.warning('find_stat_in_sidebar failed for %s: %s', url, e)
        return (None, None)


def _find_stat_in_sidebar_with_prev(url, keywords=None, timeout=30000):
    """Search sidebar and return (val, prev_val, unit) for matching stat."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _new_context(browser)
            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=max(timeout, 60000))
            try:
                page.wait_for_timeout(800)
            except Exception:
                pass
            js = '''(kw) => {
                const container = document.querySelector('.sidebar-sec.chart-stat-lastrows');
                if(!container) return null;
                const items = Array.from(container.querySelectorAll('ul > li'));
                for(const li of items){
                    const nameEl = li.querySelector('.stat-name a');
                    const statName = nameEl? nameEl.innerText.toLowerCase(): (li.querySelector('.stat-name')? li.querySelector('.stat-name').innerText.toLowerCase(): '');
                    for(const k of kw){
                        if(k && statName.includes(k.toLowerCase())){
                            const valEl = li.querySelector('.stat-val .val');
                            const unitEl = li.querySelector('.stat-val .unit');
                            const prevEl = li.querySelector('.prev-val .val');
                            return {val: valEl? valEl.textContent.trim() : null, prev: prevEl? prevEl.textContent.trim() : null, unit: unitEl? unitEl.textContent.trim() : null};
                        }
                    }
                }
                return null;
            }'''
            res = page.evaluate(js, keywords or [])
            browser.close()
            if res:
                return (res.get('val'), res.get('prev'), res.get('unit'))
            return (None, None, None)
    except Exception as e:
        logger.warning('find_stat_in_sidebar_with_prev failed for %s: %s', url, e)
        return (None, None, None)


def _parse_number(s):
    if s is None:
        return None
    s = s.replace('\u2212', '-')
    s = s.replace(',', '.')
    s = re.sub(r'[^0-9.\-]+$', '', s.strip())
    try:
        return float(s)
    except Exception:
        return None


def get_bmp_trace(url, trace_name_substr, multiply=1.0, timeout=60000):
    """Fetch a Plotly trace value from a BitcoinMagazinePro chart page.

    Args:
        url: BMP chart URL.
        trace_name_substr: substring to match against trace name (case-insensitive).
        multiply: multiply the raw value before returning (e.g. 100 to convert fraction→%).
    Returns:
        float or None.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(url, wait_until='networkidle', timeout=timeout)
            page.wait_for_timeout(4000)
            js = f"""() => {{
                const gd = document.querySelector('.js-plotly-plot');
                if(!gd) return null;
                const traces = gd.data;
                if(!traces) return null;
                const needle = {json.dumps(trace_name_substr.lower())};
                for(const t of traces){{
                    if((t.name || '').toLowerCase().includes(needle)){{
                        if(t.y && t.y.length) return t.y[t.y.length - 1];
                    }}
                }}
                return null;
            }}"""
            res = page.evaluate(js)
            try:
                browser.close()
            except Exception:
                pass
            if res is not None:
                return round(float(res) * multiply, 4)
    except Exception as e:
        logger.warning('get_bmp_trace failed for %s (%s): %s', url, trace_name_substr, e)
    return None


def _read_cached_stats_for_metric(metric_key):
    """Return parsed JSON from data/debug/ cache for the given metric key, or None."""
    if os.environ.get('FORCE_LIVE') == '1':
        return None
    base = 'data/debug'
    try:
        patterns = [f'{base}/*{metric_key}*stats.json', f'{base}/*{metric_key}*.json']
        for pat in patterns:
            for path in glob.glob(pat):
                try:
                    with open(path, 'r', encoding='utf-8') as hf:
                        return json.load(hf)
                except Exception:
                    continue
    except Exception:
        pass
    return None
