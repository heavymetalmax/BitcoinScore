"""Scraper for MVRV Z-Score metric."""
import json
import logging
import urllib.request
from .mm_utils import get_bmp_trace

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/mvrv-zscore/'
_BGEO_URL = 'https://bitcoin-data.com/v1/mvrv-zscore/last'


def _get_mvrv_bgeometrics():
    """Fallback: fetch latest MVRV Z-Score from BGeometrics public JSON API."""
    try:
        req = urllib.request.Request(_BGEO_URL, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if isinstance(data, list) and data:
            data = data[-1]
        if isinstance(data, dict):
            for key in ('mvrvZscore', 'mvrv_zscore', 'zscore', 'value'):
                if data.get(key) is not None:
                    v = float(data[key])
                    if -5.0 <= v <= 15.0:
                        return round(v, 4)
    except Exception as e:
        logger.debug('BGeometrics MVRV fallback failed: %s', e)
    return None


def get_mvrv():
    """Return MVRV Z-Score as float, e.g. 0.80.

    Primary: BitcoinMagazinePro via shared get_bmp_trace.
    Fallback: BGeometrics REST API.
    """
    val = get_bmp_trace(_BMP_URL, 'z-score')
    if val is not None:
        return round(float(val), 4)

    val = _get_mvrv_bgeometrics()
    if val is not None:
        logger.info('get_mvrv: fetched from BGeometrics fallback: %s', val)
        return val

    return None


__all__ = ['get_mvrv']


