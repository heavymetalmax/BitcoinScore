"""Scraper for RHODL Ratio metric.

Source: https://www.bitcoinmagazinepro.com/charts/rhodl-ratio/

RHODL (Realized HODL) Ratio = Realized Cap of 1-week bands / Realized Cap of 1-2 year bands.
High values signal overheating (near cycle top); low values signal accumulation zone.

Typical ranges:
  < 1 000   — accumulation / deep bottom
  1 000–5 000 — mid-cycle / neutral
  > 10 000  — overheated / approaching cycle top
"""
import logging
from scraper.mm_utils import get_bmp_trace

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/rhodl-ratio/'


def get_rhodl_ratio():
    """Return RHODL Ratio as float, e.g. 1941.07. Returns None on failure."""
    val = get_bmp_trace(_BMP_URL, 'RHODL Ratio')
    if val is None:
        logger.warning('get_rhodl_ratio: no data from BMP')
    return val


__all__ = ['get_rhodl_ratio']
