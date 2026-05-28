"""Scraper for Addresses In Loss metric."""
import logging
from .mm_utils import get_bmp_trace

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/percent-addresses-in-loss/'


def get_addresses_in_loss():
    """Return percentage of addresses in loss as float, e.g. 21.27.

    Source: BitcoinMagazinePro Plotly trace (fraction 0..1 -> multiplied by 100).
    """
    val = get_bmp_trace(_BMP_URL, 'addresses in loss', multiply=100)
    if val is not None:
        return val
    return None


__all__ = ['get_addresses_in_loss']
