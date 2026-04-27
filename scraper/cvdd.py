"""Scraper for CVDD (Coindays Destroyed Value) proximity metric.

Source: https://www.bitcoinmagazinepro.com/charts/bitcoin-price-prediction/

Returns price / CVDD ratio:
  ~1.0  = price at CVDD level (historical bottom signal)
  >1.5  = neutral / mid range
  >5.0  = approaching cycle top territory
"""
import logging
from scraper.mm_utils import get_bmp_trace

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/bitcoin-price-prediction/'


def get_cvdd_ratio():
    """Return BTC price / CVDD as float, e.g. 1.64.

    Fetches both BTC Price and CVDD from the same BMP page in two calls.
    Returns None if either value is unavailable or CVDD <= 0.
    """
    price = get_bmp_trace(_BMP_URL, 'btc price')
    cvdd  = get_bmp_trace(_BMP_URL, 'cvdd')
    if price is None or cvdd is None:
        logger.error('get_cvdd_ratio: missing data — price=%s cvdd=%s', price, cvdd)
        return None
    if cvdd <= 0:
        logger.error('get_cvdd_ratio: cvdd=%s is not positive', cvdd)
        return None
    ratio = round(price / cvdd, 4)
    logger.info('get_cvdd_ratio: price=%.0f cvdd=%.0f ratio=%.4f', price, cvdd, ratio)
    return ratio
