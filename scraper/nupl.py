"""Scraper for NUPL (Net Unrealized Profit/Loss) metric."""
import logging
import requests
import os
from scraper.mm_utils import get_bmp_trace

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/relative-unrealized-profit--loss/'


def _get_market_cap_coingecko():
    try:
        r = requests.get('https://api.coingecko.com/api/v3/coins/bitcoin', timeout=20)
        if r.status_code == 200:
            mc = r.json().get('market_data', {}).get('market_cap', {}).get('usd')
            if mc:
                return float(mc)
    except Exception as e:
        logger.debug('CoinGecko market cap fetch failed: %s', e)
    return None


def _get_realized_cap_from_glassnode():
    key = os.environ.get('GLASSNODE_API_KEY')
    if not key:
        return None
    try:
        r = requests.get(
            'https://api.glassnode.com/v1/metrics/market/realized_cap',
            params={'api_key': key, 'a': 'BTC', 's': 0},
            timeout=20,
        )
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, list) and j:
                val = j[-1]
                if isinstance(val, list) and len(val) >= 2:
                    return float(val[1])
                if isinstance(val, dict) and 'value' in val:
                    return float(val['value'])
    except Exception as e:
        logger.debug('Glassnode realized_cap fetch failed: %s', e)
    return None


def _get_realized_cap_from_file(path='data/debug/realized_cap.json'):
    try:
        if os.path.exists(path):
            import json
            with open(path, 'r', encoding='utf-8') as hf:
                j = json.load(hf)
                if isinstance(j, dict) and 'realized_cap' in j:
                    return float(j['realized_cap'])
                if isinstance(j, (int, float)):
                    return float(j)
    except Exception:
        pass
    return None


def get_nupl():
    """Return NUPL as a percentage (float), e.g. 30.75."""
    # 1. BMP (primary — reliable, not Cloudflare-blocked)
    # BMP trace is a fraction 0..1; multiply by 100 → percent
    val = get_bmp_trace(_BMP_URL, 'nupl', multiply=100)
    if val is not None:
        return val

    return None


def get_nupl_computed():
    """Compute NUPL = (market_cap - realized_cap) / market_cap as percentage."""
    realized = None
    try:
        rc_env = os.environ.get('REALIZED_CAP_OVERRIDE')
        if rc_env:
            realized = float(rc_env)
    except Exception:
        pass
    if realized is None:
        realized = _get_realized_cap_from_file()
    if realized is None:
        realized = _get_realized_cap_from_glassnode()
    market_cap = _get_market_cap_coingecko()
    if market_cap is None:
        logger.warning('Computed NUPL failed: market cap unavailable')
        return None
    if realized is None:
        logger.warning('Computed NUPL: realized cap unavailable')
        return None
    try:
        return round((market_cap - realized) / market_cap * 100, 2)
    except Exception as e:
        logger.debug('Computed NUPL calculation failed: %s', e)
        return None


__all__ = ['get_nupl', 'get_nupl_computed']


logger = logging.getLogger(__name__)


def _get_market_cap_coingecko():
    try:
        r = requests.get('https://api.coingecko.com/api/v3/coins/bitcoin', timeout=20)
        if r.status_code == 200:
            mc = r.json().get('market_data', {}).get('market_cap', {}).get('usd')
            if mc:
                return float(mc)
    except Exception as e:
        logger.debug('CoinGecko market cap fetch failed: %s', e)
    return None


def _get_realized_cap_from_glassnode():
    key = os.environ.get('GLASSNODE_API_KEY')
    if not key:
        return None
    try:
        r = requests.get(
            'https://api.glassnode.com/v1/metrics/market/realized_cap',
            params={'api_key': key, 'a': 'BTC', 's': 0},
            timeout=20,
        )
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, list) and j:
                val = j[-1]
                if isinstance(val, list) and len(val) >= 2:
                    return float(val[1])
                if isinstance(val, dict) and 'value' in val:
                    return float(val['value'])
    except Exception as e:
        logger.debug('Glassnode realized_cap fetch failed: %s', e)
    return None


def _get_realized_cap_from_file(path='data/debug/realized_cap.json'):
    try:
        if os.path.exists(path):
            import json
            with open(path, 'r', encoding='utf-8') as hf:
                j = json.load(hf)
                if isinstance(j, dict) and 'realized_cap' in j:
                    return float(j['realized_cap'])
                if isinstance(j, (int, float)):
                    return float(j)
    except Exception:
        pass
    return None


def _get_nupl_from_bitcoinmagazine():
    url = 'https://www.bitcoinmagazinepro.com/charts/relative-unrealized-profit--loss/'
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context()
                page = ctx.new_page()
                page.goto(url, wait_until='networkidle', timeout=60000)
                page.wait_for_timeout(4000)
                js = '''() => {
                    const gd = document.querySelector('.js-plotly-plot');
                    if(!gd) return null;
                    try{
                        const traces = gd.data || gd._fullData || (window.Plotly && gd.data);
                        if(!traces) return null;
                        for(const t of traces){
                            const name = (t.name || '').toString().toLowerCase();
                            if(name.includes('nupl') || name.includes('unreal')){
                                if(t.y && t.y.length) return t.y[t.y.length-1];
                            }
                        }
                        for(const t of traces){
                            if(t.y && t.y.length) return t.y[t.y.length-1];
                        }
                    }catch(e){return null}
                    return null;
                }'''
                res = page.evaluate(js)
                try:
                    browser.close()
                except Exception:
                    pass
                if res is None:
                    return None
                val = float(res)
                return round(val * 100, 2) if abs(val) <= 1.5 else round(val, 2)
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as e:
        logger.debug('bitcoinmagazine nupl parse failed: %s', e)
    return None


# (duplicate get_nupl removed — see definition at line 65)


def get_nupl_computed():
    """Compute NUPL = (market_cap - realized_cap) / market_cap as percentage."""
    realized = None
    try:
        rc_env = os.environ.get('REALIZED_CAP_OVERRIDE')
        if rc_env:
            realized = float(rc_env)
    except Exception:
        pass
    if realized is None:
        realized = _get_realized_cap_from_file()
    if realized is None:
        realized = _get_realized_cap_from_glassnode()
    market_cap = _get_market_cap_coingecko()
    if market_cap is None:
        logger.warning('Computed NUPL failed: market cap unavailable')
        return None
    if realized is None:
        logger.warning('Computed NUPL: realized cap unavailable')
        return None
    try:
        return round((market_cap - realized) / market_cap * 100, 2)
    except Exception as e:
        logger.debug('Computed NUPL calculation failed: %s', e)
        return None


__all__ = ['get_nupl', 'get_nupl_computed']

