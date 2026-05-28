"""Scraper for NUPL (Net Unrealized Profit/Loss) metric."""
import logging
from .mm_utils import get_bmp_trace

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/relative-unrealized-profit--loss/'


def get_nupl():
    """Return NUPL as a percentage (float), e.g. 30.75."""
    # 1. BMP (primary — reliable, not Cloudflare-blocked)
    # BMP trace is a fraction 0..1; multiply by 100 → percent
    val = get_bmp_trace(_BMP_URL, 'nupl', multiply=100)
    if val is not None:
        return val

    return None


__all__ = ['get_nupl']

