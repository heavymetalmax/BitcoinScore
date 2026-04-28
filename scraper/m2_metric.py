"""Scraper for US M2 liquidity momentum signal.

Source: FRED WM2NS (US M2 Money Stock, weekly, seasonally adjusted).

Returns 10-week % change (momentum). This is a leading indicator:
  positive momentum → M2 expanding → BTC price expected to rise ~10 weeks ahead
  negative momentum → M2 contracting → BTC price expected to fall ~10 weeks ahead

The map in scoring.py is INVERTED accordingly:
  high momentum → low score (accumulate before the rise)
  low/negative momentum → high score (exit before the fall)
"""
import csv
import io
import logging
import ssl
import urllib.request

logger = logging.getLogger(__name__)

_FRED_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=WM2NS'


def _fetch_wm2ns_series():
    """Fetch full WM2NS weekly series from FRED. Returns list of (date_str, float) sorted by date."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(_FRED_URL, headers={'User-Agent': 'curl/7.88'})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        text = r.read().decode()
    rows = list(csv.reader(io.StringIO(text)))
    series = [(r[0], float(r[1])) for r in rows[1:] if r[1] != '.']
    series.sort(key=lambda x: x[0])
    return series


def get_m2():
    """Return US M2 10-week momentum as float (% change over last 10 weeks).

    Fetches FRED WM2NS weekly data. Returns None on failure.
    """
    try:
        series = _fetch_wm2ns_series()
        if len(series) < 12:
            logger.error('get_m2: insufficient FRED data (%d points)', len(series))
            return None
        current_val = series[-1][1]
        past_val = series[-11][1]   # 10 weeks ago
        momentum = round((current_val - past_val) / past_val * 100, 2)
        logger.info('get_m2: US M2 10w momentum=%.2f%% (%s → %s)',
                    momentum, series[-11][0], series[-1][0])
        return momentum
    except Exception as e:
        logger.error('get_m2: FRED fetch failed: %s', e)
        return None


__all__ = ['get_m2']


