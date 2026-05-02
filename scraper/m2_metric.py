"""Scraper for US M2 liquidity signal.

Source: FRED WM2NS (US M2 Money Stock, weekly, seasonally adjusted).

Returns year-over-year % change. Direct (non-inverted) logic:
  high YoY expansion → system flooded with liquidity → high risk score
  low/negative YoY  → liquidity tightening → low risk score (accumulate)

Range: map_m2 uses -5% to +20% → 0..100.
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
    """Return US M2 year-over-year % change (WM2NS, FRED).

    High value = liquidity expanding = high risk = high score (direct mapping).
    Returns None on failure.
    """
    try:
        series = _fetch_wm2ns_series()
        if len(series) < 54:
            logger.error('get_m2: insufficient FRED data (%d points)', len(series))
            return None
        current_val = series[-1][1]
        past_val    = series[-53][1]   # ~52 weeks ago
        yoy = round((current_val - past_val) / past_val * 100, 2)
        logger.info('get_m2: US M2 YoY=%.2f%% (%s → %s)',
                    yoy, series[-53][0], series[-1][0])
        return yoy
    except Exception as e:
        logger.error('get_m2: FRED fetch failed: %s', e)
        return None


__all__ = ['get_m2']


