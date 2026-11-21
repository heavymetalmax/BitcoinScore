"""Scraper for CVDD (Coindays Destroyed Value) proximity metric.

Source: https://www.bitcoinmagazinepro.com/charts/bitcoin-price-prediction/

Returns price / CVDD ratio:
  ~1.0  = price at CVDD level (historical bottom signal)
  >1.5  = neutral / mid range
  >5.0  = approaching cycle top territory
"""
import logging
from .mm_utils import get_bmp_traces

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/bitcoin-price-prediction/'
_BMP_CVDD_URL = 'https://www.bitcoinmagazinepro.com/charts/cvdd/'


def get_cvdd_ratio(btc_price=None):
    """Return BTC price / CVDD as float, e.g. 1.64.

    Fetches both BTC Price and CVDD from BMP in a single call, with fallback
    to /charts/cvdd/ and optional spot btc_price if the price trace is missing.
    Returns None if CVDD is unavailable/non-positive or no price is available.
    """
    price = None
    cvdd = None
    for url in (_BMP_URL, _BMP_CVDD_URL):
        traces = get_bmp_traces(url, ['btc price', 'bitcoin price', 'cvdd'])
        cvdd = traces.get('cvdd')
        price = traces.get('btc price') or traces.get('bitcoin price')
        if cvdd is not None and cvdd > 0:
            break

    if price is None and btc_price is not None:
        try:
            price = float(btc_price)
        except (TypeError, ValueError):
            price = None

    if price is None or cvdd is None:
        logger.error('get_cvdd_ratio: missing data — price=%s cvdd=%s', price, cvdd)
        return None
    if cvdd <= 0:
        logger.error('get_cvdd_ratio: cvdd=%s is not positive', cvdd)
        return None
    ratio = round(price / cvdd, 4)
    logger.info('get_cvdd_ratio: price=%.0f cvdd=%.0f ratio=%.4f', price, cvdd, ratio)
    return ratio
