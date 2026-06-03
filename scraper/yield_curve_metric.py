"""US Yield Curve Spread (10Y-2Y) from FRED."""
import csv
import io
import logging
import ssl
import urllib.request

logger = logging.getLogger(__name__)

_FRED_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10Y2Y'


def get_yield_curve():
    """Return current US Yield Curve Spread % (T10Y2Y, FRED). Returns None on failure."""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(_FRED_URL, headers={'User-Agent': 'curl/7.88'})
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            text = r.read().decode()
        rows = list(csv.reader(io.StringIO(text)))
        vals = [float(row[1]) for row in rows[1:] if row[1] not in ('.', '')]
        if not vals:
            raise ValueError("No values found in CSV")
        return round(vals[-1], 4)
    except Exception as e:
        logger.warning('get_yield_curve failed: %s. Trying history fallback...', e)
        try:
            import json
            import os
            hist_path = 'data/history/yield_curve.json'
            if os.path.exists(hist_path):
                with open(hist_path, 'r', encoding='utf-8') as hf:
                    hist = json.load(hf)
                for entry in reversed(hist):
                    if entry.get('yield_curve') is not None:
                        logger.info('get_yield_curve: recovered last value from history: %s', entry['yield_curve'])
                        return entry['yield_curve']
        except Exception as he:
            logger.warning('get_yield_curve history fallback failed: %s', he)
        return None


__all__ = ['get_yield_curve']
