"""Puell Multiple — daily miner revenue / 365-day MA of daily miner revenue.

Sources:
  1. CoinMetrics Community REST API (IssTotUSD / 365d MA) — fast, browserless
  2. BitcoinMagazinePro Plotly trace
  3. BGeometrics REST API fallback
"""
import datetime
import json
import logging
import urllib.request
from .mm_utils import get_bmp_trace

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/puell-multiple/'
_BGEO_URL = 'https://bitcoin-data.com/v1/puell-multiple/last'


def _get_puell_coinmetrics():
    """Compute Puell Multiple directly from CoinMetrics Community API (IssTotUSD / 365d MA)."""
    try:
        start_dt = (datetime.date.today() - datetime.timedelta(days=380)).isoformat()
        url = (
            'https://community-api.coinmetrics.io/v4/timeseries/asset-metrics'
            f'?assets=btc&metrics=IssTotUSD&frequency=1d&start_time={start_dt}&page_size=400'
        )
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        rows = payload.get('data') or []
        vals = [float(r['IssTotUSD']) for r in rows if r.get('IssTotUSD') is not None]
        if len(vals) >= 365:
            ma_365 = sum(vals[-365:]) / 365.0
            if ma_365 > 0:
                puell = round(vals[-1] / ma_365, 4)
                if 0.1 <= puell <= 12.0:
                    return puell
    except Exception as e:
        logger.debug('CoinMetrics Puell calculation failed: %s', e)
    return None


def _get_puell_bgeometrics():
    """Fallback: fetch latest Puell Multiple from BGeometrics public JSON API."""
    try:
        req = urllib.request.Request(_BGEO_URL, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if isinstance(data, list) and data:
            data = data[-1]
        if isinstance(data, dict):
            for key in ('puellMultiple', 'puell_multiple', 'puell', 'value'):
                if data.get(key) is not None:
                    v = float(data[key])
                    if 0.1 <= v <= 12.0:
                        return round(v, 4)
    except Exception as e:
        logger.debug('BGeometrics Puell fallback failed: %s', e)
    return None


def get_puell_multiple():
    """Return Puell Multiple as float, e.g. 0.72. Returns None on failure."""
    # 1. Fast, exact CoinMetrics REST API calculation
    val = _get_puell_coinmetrics()
    if val is not None:
        logger.info('get_puell_multiple: computed from CoinMetrics API: %s', val)
        return val

    # 2. BMP Playwright chart
    val = get_bmp_trace(_BMP_URL, 'puell')
    if val is not None:
        return val

    # 3. BGeometrics REST API
    val = _get_puell_bgeometrics()
    if val is not None:
        logger.info('get_puell_multiple: fetched from BGeometrics fallback: %s', val)
        return val

    logger.warning('get_puell_multiple: all sources failed')
    return None


__all__ = ['get_puell_multiple']
