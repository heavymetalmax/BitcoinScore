"""Scraper for NUPL (Net Unrealized Profit/Loss) metric."""
import json
import logging
import urllib.request
from .mm_utils import get_bmp_trace

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/relative-unrealized-profit--loss/'
_BGEO_URL = 'https://bitcoin-data.com/v1/nupl/last'


def _get_nupl_bgeometrics():
    """Fallback: fetch latest NUPL from BGeometrics public JSON API."""
    try:
        req = urllib.request.Request(_BGEO_URL, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if isinstance(data, list) and data:
            data = data[-1]
        if isinstance(data, dict):
            raw = data.get('nupl') if data.get('nupl') is not None else data.get('value')
            if raw is not None:
                v = float(raw)
                # Convert fraction (-1..1) to percentage (-100..100)
                if abs(v) <= 1.5:
                    v *= 100.0
                if -60.0 <= v <= 100.0:
                    return round(v, 4)
    except Exception as e:
        logger.debug('BGeometrics NUPL fallback failed: %s', e)
    return None


def get_nupl():
    """Return NUPL as a percentage (float), e.g. 30.75."""
    # 1. BMP (primary — Plotly trace is a fraction 0..1; multiply by 100 → percent)
    val = get_bmp_trace(_BMP_URL, 'nupl', multiply=100)
    if val is not None:
        return val

    # 2. BGeometrics public REST API fallback
    val = _get_nupl_bgeometrics()
    if val is not None:
        logger.info('get_nupl: fetched from BGeometrics fallback: %s', val)
        return val

    return None


__all__ = ['get_nupl']

