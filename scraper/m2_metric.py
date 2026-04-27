"""Scraper for Global M2 liquidity signal.

Primary source: data/history/global_m2.json — BMP Global Liquidity index
(global aggregate: Fed + ECB + PBoC + BoJ etc.), stored locally.

Returns YoY % change (current vs ~365 days ago).
Positive and high (>10%) = expanding global liquidity (bullish for BTC).
Negative = contracting global liquidity (bearish for BTC).

For recent months beyond the stored BMP data, falls back to US WM2NS YoY%
from FRED as a directional proxy.
"""
import csv
import datetime
import io
import json
import logging
import os
import ssl
import urllib.request

logger = logging.getLogger(__name__)

_FRED_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=WM2NS'
_HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'history', 'global_m2.json')

# How many days old the BMP data can be before we switch to US proxy
_MAX_STALENESS_DAYS = 45


def _load_global_m2_history():
    """Load saved Global M2 BMP series. Returns list of (date_str, value) sorted by date."""
    path = os.path.normpath(_HISTORY_FILE)
    if not os.path.exists(path):
        logger.warning('get_m2: history file not found: %s', path)
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        series = [(p['date'], float(p['value'])) for p in data['series'] if p.get('value') is not None]
        series.sort(key=lambda x: x[0])
        return series
    except Exception as e:
        logger.error('get_m2: failed to load history: %s', e)
        return []


def _yoy_from_series(series):
    """Compute YoY % change using the last available point vs ~365 days prior.
    Returns (yoy_pct, last_date_str) or (None, None) on failure.
    """
    if len(series) < 50:
        return None, None
    last_date_str, last_val = series[-1]
    last_date = datetime.date.fromisoformat(last_date_str)
    target_yoy = last_date - datetime.timedelta(days=365)
    # find closest point
    best_date, best_val = min(series, key=lambda x: abs((datetime.date.fromisoformat(x[0]) - target_yoy).days))
    yoy = round((last_val - best_val) / best_val * 100, 2)
    return yoy, last_date_str


def _get_us_wm2ns_yoy():
    """Fetch US WM2NS from FRED and return YoY % change. Used as fallback proxy."""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(_FRED_URL, headers={'User-Agent': 'curl/7.88'})
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            text = r.read().decode()
        rows = list(csv.reader(io.StringIO(text)))
        series = [(r[0], float(r[1])) for r in rows[1:] if r[1] != '.']
        if len(series) < 55:
            return None
        last_val = series[-1][1]
        yoy_val = series[-53][1]  # ~52 weeks ago
        return round((last_val - yoy_val) / yoy_val * 100, 2)
    except Exception as e:
        logger.error('get_m2: FRED fallback failed: %s', e)
        return None


def get_m2():
    """Return Global M2 YoY % change as float, e.g. 6.4 means +6.4%.

    Uses saved BMP Global M2 history (global aggregate index).
    If data is stale (> 45 days old), blends with US WM2NS YoY% delta as proxy.
    Returns None on failure.
    """
    series = _load_global_m2_history()
    yoy, last_date_str = _yoy_from_series(series)

    if yoy is None:
        # No BMP history — fall back entirely to US WM2NS YoY
        logger.warning('get_m2: no BMP history, using US WM2NS YoY as fallback')
        return _get_us_wm2ns_yoy()

    last_date = datetime.date.fromisoformat(last_date_str)
    staleness = (datetime.date.today() - last_date).days
    logger.info('get_m2: BMP Global M2 YoY=%.2f%% (data as of %s, %d days old)', yoy, last_date_str, staleness)

    if staleness > _MAX_STALENESS_DAYS:
        # Data is stale — use US WM2NS YoY delta to adjust
        us_yoy_now = _get_us_wm2ns_yoy()
        if us_yoy_now is not None:
            # Compute US YoY at the BMP cutoff date for delta
            try:
                ctx = ssl.create_default_context()
                req = urllib.request.Request(_FRED_URL, headers={'User-Agent': 'curl/7.88'})
                with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                    text = r.read().decode()
                rows = list(csv.reader(io.StringIO(text)))
                us_series = [(r[0], float(r[1])) for r in rows[1:] if r[1] != '.']
                us_series.sort(key=lambda x: x[0])
                # YoY at BMP cutoff
                cutoff_date_closest, cutoff_val = min(us_series, key=lambda x: abs(
                    (datetime.date.fromisoformat(x[0]) - last_date).days
                ))
                cutoff_yoy_val = [v for d, v in us_series if
                                  abs((datetime.date.fromisoformat(d) - (last_date - datetime.timedelta(days=365))).days) < 14]
                if cutoff_yoy_val:
                    us_yoy_at_cutoff = round((cutoff_val - cutoff_yoy_val[0]) / cutoff_yoy_val[0] * 100, 2)
                    delta = us_yoy_now - us_yoy_at_cutoff
                    # Apply 50% of US delta to Global M2 YoY (partial adjustment)
                    yoy = round(yoy + delta * 0.5, 2)
                    logger.info('get_m2: applied US delta %.2f%% × 0.5 → adjusted Global M2 YoY=%.2f%%',
                                delta, yoy)
            except Exception as e:
                logger.warning('get_m2: stale adjustment failed: %s', e)

    return yoy


__all__ = ['get_m2']


