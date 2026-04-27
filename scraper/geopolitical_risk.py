"""Scraper for Global Geopolitical Risk Index metric (Caldara & Iacoviello, Fed)."""
import io
import logging
import ssl
import urllib.request

import xlrd

logger = logging.getLogger(__name__)

# Public XLS published by Caldara & Iacoviello (updated monthly)
_GPR_URL = 'https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls'


def get_geopolitical_risk_change():
    """Return (current, prev, delta) for Global Geopolitical Risk Index (monthly)."""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(_GPR_URL, headers={'User-Agent': 'curl/7.88'})
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            data = resp.read()
        wb = xlrd.open_workbook(file_contents=data)
        ws = wb.sheets()[0]
        # Row 0 = headers; col 0 = month (Excel serial), col 1 = GPR
        values = []
        for i in range(1, ws.nrows):
            row = ws.row_values(i)
            try:
                val = float(row[1])
                if val > 0:
                    values.append(val)
            except (TypeError, ValueError):
                continue
        if len(values) < 2:
            return (None, None, None)
        current = round(values[-1], 4)
        prev = round(values[-2], 4)
        delta = round(current - prev, 4)
        return (current, prev, delta)
    except Exception as e:
        logger.warning('get_geopolitical_risk_change failed: %s', e)
        return (None, None, None)


__all__ = ['get_geopolitical_risk_change']

