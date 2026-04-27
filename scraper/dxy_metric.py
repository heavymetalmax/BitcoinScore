"""Scraper for US Dollar Index (DXY) metric via FRED (DTWEXBGS)."""
import logging
import ssl
import urllib.request

logger = logging.getLogger(__name__)


# FRED series: DTWEXBGS — Nominal Broad US Dollar Index (daily, Mon–Fri)
_FRED_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTWEXBGS'


def get_dxy_change():
    """Return (current, prev_30d, delta) for US Dollar Index from FRED DTWEXBGS."""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(_FRED_URL, headers={'User-Agent': 'curl/7.88'})
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            lines = resp.read().decode().strip().splitlines()
        # parse valid rows (skip header / non-numeric)
        rows = []
        for line in lines:
            if not line:
                continue
            parts = line.split(',')
            if len(parts) < 2:
                continue
            val_str = parts[1].strip()
            try:
                rows.append(float(val_str))
            except ValueError:
                continue  # header or missing ('.')
        if len(rows) < 2:
            return (None, None, None)
        current = round(rows[-1], 4)
        # ~30 trading days ago (≈ 1 month)
        prev_idx = max(0, len(rows) - 22)
        prev = round(rows[prev_idx], 4)
        delta = round(current - prev, 4)
        return (current, prev, delta)
    except Exception as e:
        logger.warning('get_dxy_change failed: %s', e)
        return (None, None, None)


__all__ = ['get_dxy_change']

