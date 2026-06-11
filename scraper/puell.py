"""Puell Multiple — daily miner revenue / 365-day MA of daily miner revenue.

Source: https://www.bitcoinmagazinepro.com/charts/puell-multiple/

Typical ranges:
  < 0.5  — deep bottom / capitulation
  0.5–1  — accumulation zone
  1–4    — mid-cycle / normal
  > 4    — overheated / approaching cycle top
"""
import logging
from .mm_utils import get_bmp_trace

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/puell-multiple/'


def get_puell_multiple():
    """Return Puell Multiple as float, e.g. 0.72. Returns None on failure."""
    val = get_bmp_trace(_BMP_URL, 'puell')
    if val is None:
        logger.warning('get_puell_multiple: no data from BMP')
    return val


__all__ = ['get_puell_multiple']
