"""Binance Futures Funding Rate for BTCUSDT.

Score mapping (direct logic: positive funding = longs paying = overheated = high risk):
  7-day average funding rate (per 8h period) → annualized for context, scored on daily basis.

  rate_avg in range -0.05% .. +0.10% per period (8h)
  score = clamp((rate_avg + 0.05) / 0.15 * 100, 0, 100)
    rate_avg = -0.05% → score = 0   (max negative, shorts overheated, contrarian buy)
    rate_avg =  0.00% → score = 33  (neutral)
    rate_avg = +0.05% → score = 67  (longs paying, moderate risk)
    rate_avg = +0.10% → score = 100 (longs heavily overheated, sell zone)
"""
import json
import logging
import ssl
import urllib.request
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_BASE = 'https://fapi.binance.com'


def _get(path, params=None):
    url = _BASE + path
    if params:
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        url += '?' + qs
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={'User-Agent': 'curl/7.88'})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        return json.loads(r.read().decode())


def get_funding_rate(symbol='BTCUSDT', periods=21):
    """
    Return funding rate dict or None on failure.
    periods: number of 8h periods to average (21 = ~7 days).
    """
    try:
        # fetch last `periods` funding rate entries
        data = _get('/fapi/v1/fundingRate', {'symbol': symbol, 'limit': periods})
        if not data:
            return None
        rates = [float(d['fundingRate']) for d in data]
        rate_pct = [r * 100 for r in rates]  # convert to percent

        avg = sum(rate_pct) / len(rate_pct)
        latest = rate_pct[-1]

        # next funding time
        next_ts = int(data[-1].get('nextFundingTime', 0)) / 1000
        next_dt = datetime.fromtimestamp(next_ts, tz=timezone.utc).isoformat() if next_ts else None

        # score: range -0.05%..+0.10% → 0..100
        score = round(max(0, min(100, (avg + 0.05) / 0.15 * 100)))

        return {
            'latest':      round(latest, 5),   # last 8h rate, %
            'avg_7d':      round(avg, 5),       # 7-day average, %
            'score':       score,
            'next_funding': next_dt,
            'periods':     len(rates),
        }
    except Exception as e:
        logger.warning('get_funding_rate failed: %s', e)
        return None


__all__ = ['get_funding_rate']
