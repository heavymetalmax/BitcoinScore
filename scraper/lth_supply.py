"""Long-Term Holder (LTH) Supply — % of BTC supply held by long-term holders.

Source: https://www.bitcoinmagazinepro.com/charts/long-term-holder-supply/

Long-term holders = addresses that have not moved coins for 155+ days.
Typical ranges:
  < 60%  — LTH distributing heavily (peak euphoria)
  60–70% — mid-cycle
  > 75%  — LTH accumulating (bottom zone)
"""
import logging
from .mm_utils import get_bmp_trace

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/long-term-holder-supply/'


def get_lth_supply_pct():
    """Return LTH Supply as % of total supply, e.g. 76.3. Returns None on failure."""
    val = get_bmp_trace(_BMP_URL, 'long')
    if val is None:
        logger.warning('get_lth_supply_pct: no data from BMP')
    return val


__all__ = ['get_lth_supply_pct']
