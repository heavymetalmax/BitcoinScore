"""Scraper for Addresses In Loss metric."""
import re
import logging
import os
from playwright.sync_api import sync_playwright
from scraper.mm_utils import (
    MM, _new_context, _get_selector_text, _enumerate_stat_vals,
    _find_stat_in_sidebar, _parse_number, _read_cached_stats_for_metric,
    get_bmp_trace,
)

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/percent-addresses-in-loss/'


def get_addresses_in_loss():
    """Return percentage of addresses in loss as float, e.g. 21.27."""
    # 1. BMP (primary — BMP trace is a fraction 0..1; multiply by 100 → percent)
    val = get_bmp_trace(_BMP_URL, 'addresses in loss', multiply=100)
    if val is not None:
        return val

    # 2. Cached MacroMicro snapshot
    loss_url = MM.get('loss_series')
    try:
        cached = _read_cached_stats_for_metric('loss') or _read_cached_stats_for_metric('addresses_in_loss')
        if cached:
            sb = cached.get('sidebar') or []
            if sb and len(sb) > 0 and sb[0].get('val'):
                return _parse_number(re.sub(r'[^0-9\-\.,]', '', sb[0].get('val')))
            stats = cached.get('stats') or []
            if stats and stats[0].get('val'):
                return _parse_number(re.sub(r'[^0-9\-\.,]', '', stats[0].get('val')))
    except Exception:
        pass

    if not loss_url:
        return None

    # 3. MacroMicro headless fallback
    keywords = ['percentage of addresses in loss', 'addresses in loss', 'share of addresses in loss', 'in loss']
    try:
        v, _ = _find_stat_in_sidebar(loss_url, keywords)
        if v:
            parsed = _parse_number(re.sub(r'[^0-9\-\.,]', '', v))
            if parsed is not None:
                return parsed
    except Exception:
        pass

    sel = _get_selector_text(loss_url, 'div.stat-val span.val')
    if sel:
        parsed = _parse_number(re.sub(r'[^0-9\-\.,]', '', sel))
        if parsed is not None:
            return parsed

    items = _enumerate_stat_vals(loss_url)
    if items:
        for it in items:
            if it.get('val'):
                parsed = _parse_number(re.sub(r'[^0-9\-\.,]', '', it.get('val')))
                if parsed is not None:
                    return parsed

    # 4. MacroMicro headful last-resort
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, args=['--disable-blink-features=AutomationControlled'], slow_mo=50)
            try:
                context = _new_context(browser)
            except Exception:
                context = browser.new_context(viewport={'width': 1920, 'height': 1080})
            page = context.new_page()
            try:
                page.goto(loss_url, wait_until='networkidle', timeout=60000)
            except Exception:
                try:
                    page.goto(loss_url, wait_until='domcontentloaded', timeout=60000)
                except Exception:
                    pass
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass
            js = '''() => {
                const container = document.querySelector('.sidebar-sec.chart-stat-lastrows');
                if(container){
                    const items = Array.from(container.querySelectorAll('ul > li'));
                    for(const li of items){
                        const nameEl = li.querySelector('.stat-name a') || li.querySelector('.stat-name');
                        const statName = nameEl? nameEl.innerText.toLowerCase() : '';
                        if(statName.includes('addresses in loss') || statName.includes('in loss')){
                            const valEl = li.querySelector('.stat-val .val');
                            return valEl? valEl.textContent.trim() : null;
                        }
                    }
                }
                const v = document.querySelector('div.stat-val span.val');
                return v? v.textContent.trim() : null;
            }'''
            res = None
            try:
                res = page.evaluate(js)
            except Exception:
                pass
            try:
                os.makedirs('data/debug', exist_ok=True)
                with open('data/debug/loss_page_headful.html', 'w', encoding='utf-8') as hf:
                    hf.write(page.content())
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            if res:
                return _parse_number(re.sub(r'[^0-9\-\.,]', '', res))
    except Exception:
        pass

    return None


__all__ = ['get_addresses_in_loss']

logger = logging.getLogger(__name__)


def get_addresses_in_loss():
    """Return percentage of addresses in loss as float, e.g. 21.99."""
    loss_url = MM.get('loss_series')
    if not loss_url:
        return None

    try:
        cached = _read_cached_stats_for_metric('loss') or _read_cached_stats_for_metric('addresses_in_loss')
        if cached:
            sb = cached.get('sidebar') or []
            if sb and len(sb) > 0 and sb[0].get('val'):
                return _parse_number(re.sub(r'[^0-9\-\.,]', '', sb[0].get('val')))
            stats = cached.get('stats') or []
            if stats and stats[0].get('val'):
                return _parse_number(re.sub(r'[^0-9\-\.,]', '', stats[0].get('val')))
    except Exception:
        pass

    keywords = ['percentage of addresses in loss', 'addresses in loss', 'share of addresses in loss', 'in loss']
    try:
        v, _ = _find_stat_in_sidebar(loss_url, keywords)
        if v:
            parsed = _parse_number(re.sub(r'[^0-9\-\.,]', '', v))
            if parsed is not None:
                return parsed
    except Exception:
        pass

    sel = _get_selector_text(loss_url, 'div.stat-val span.val')
    if sel:
        parsed = _parse_number(re.sub(r'[^0-9\-\.,]', '', sel))
        if parsed is not None:
            return parsed

    items = _enumerate_stat_vals(loss_url)
    if items:
        for it in items:
            if it.get('val'):
                parsed = _parse_number(re.sub(r'[^0-9\-\.,]', '', it.get('val')))
                if parsed is not None:
                    return parsed

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, args=['--disable-blink-features=AutomationControlled'], slow_mo=50)
            try:
                context = _new_context(browser)
            except Exception:
                context = browser.new_context(viewport={'width': 1920, 'height': 1080})
            page = context.new_page()
            try:
                page.goto(loss_url, wait_until='networkidle', timeout=60000)
            except Exception:
                try:
                    page.goto(loss_url, wait_until='domcontentloaded', timeout=60000)
                except Exception:
                    pass
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass
            js = '''() => {
                const container = document.querySelector('.sidebar-sec.chart-stat-lastrows');
                if(container){
                    const items = Array.from(container.querySelectorAll('ul > li'));
                    for(const li of items){
                        const nameEl = li.querySelector('.stat-name a') || li.querySelector('.stat-name');
                        const statName = nameEl? nameEl.innerText.toLowerCase() : '';
                        if(statName.includes('addresses in loss') || statName.includes('in loss') || statName.includes('addresses')){
                            const valEl = li.querySelector('.stat-val .val');
                            return valEl? valEl.textContent.trim() : null;
                        }
                    }
                }
                const v = document.querySelector('div.stat-val span.val');
                return v? v.textContent.trim() : null;
            }'''
            res = None
            try:
                res = page.evaluate(js)
            except Exception:
                pass
            try:
                os.makedirs('data/debug', exist_ok=True)
                with open('data/debug/loss_page_headful.html', 'w', encoding='utf-8') as hf:
                    hf.write(page.content())
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            if res:
                return _parse_number(re.sub(r'[^0-9\-\.,]', '', res))
    except Exception:
        pass

    return None


__all__ = ['get_addresses_in_loss']

