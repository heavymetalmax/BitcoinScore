"""Scraper for Balanced Price proximity metric.

Source: https://www.bitcoinmagazinepro.com/charts/balanced-price/

Balanced Price = Realized Price - Transferred Price.
Historically Bitcoin bottoms near or at this level.

Returns price / balanced_price ratio:
  ~1.0  = price at Balanced Price level (historical bottom signal)
  ~2.0  = mid-cycle accumulation
  ~5.0+ = approaching cycle top territory
"""
import logging
from .mm_utils import get_bmp_traces

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/balanced-price/'


def get_balanced_price_ratio():
    """Return BTC price / Balanced Price as float, e.g. 1.85.

    Returns None if either value is unavailable or Balanced Price <= 0.
    """
    traces = get_bmp_traces(_BMP_URL, ['btc price', 'balanced price'])
    price = traces.get('btc price')
    bp = traces.get('balanced price')

    if price is None or bp is None:
        logger.error('get_balanced_price_ratio: missing data — price=%s balanced_price=%s', price, bp)
        return None
    if bp <= 0:
        logger.error('get_balanced_price_ratio: balanced_price=%s is not positive', bp)
        return None
    ratio = round(price / bp, 4)
    logger.info('get_balanced_price_ratio: price=%.0f bp=%.0f ratio=%.4f', price, bp, ratio)
    return ratio
