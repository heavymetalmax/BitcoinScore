"""Shared Playwright + parsing utilities for BMP (BitcoinMagazinePro) scrapers."""
from playwright.sync_api import sync_playwright
import glob
import json
import logging
import os
import re

logger = logging.getLogger(__name__)


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


def _read_cached_stats_for_metric(metric_key, max_age_seconds=172800):
    """Return parsed JSON from data/debug/ cache for the given metric key, or None.

    Files older than max_age_seconds (default 48 h) are treated as stale and ignored.
    """
    if os.environ.get('FORCE_LIVE') == '1':
        return None
    import time as _time
    base = 'data/debug'
    try:
        patterns = [f'{base}/*{metric_key}*stats.json', f'{base}/*{metric_key}*.json']
        for pat in patterns:
            for path in glob.glob(pat):
                try:
                    mtime = os.path.getmtime(path)
                    if _time.time() - mtime > max_age_seconds:
                        logger.debug('Skipping stale debug cache %s (age %.1fh)', path, (_time.time() - mtime) / 3600)
                        continue
                    with open(path, 'r', encoding='utf-8') as hf:
                        return json.load(hf)
                except Exception:
                    continue
    except Exception:
        pass
    return None
