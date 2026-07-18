"""Bybit Futures Funding Rate for BTCUSDT (primary), Binance fallback.

Score mapping (direct logic: positive funding = longs paying = overheated = high risk):
  7-day average funding rate (per 8h period).

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

logger = logging.getLogger(__name__)


def _get(url):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={'User-Agent': 'curl/7.88'})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        return json.loads(r.read().decode())


def _from_bybit(symbol='BTCUSDT', periods=21):
    url = f'https://api.bybit.com/v5/market/funding/history?category=linear&symbol={symbol}&limit={periods}'
    data = _get(url)
    rows = data['result']['list']
    if not rows:
        return None
    rate_pct = [float(r['fundingRate']) * 100 for r in rows]
    avg = sum(rate_pct) / len(rate_pct)
    latest = rate_pct[0]
    score = round(max(0, min(100, (avg + 0.05) / 0.15 * 100)))
    return {
        'latest':  round(latest, 5),
        'avg_7d':  round(avg, 5),
        'score':   score,
        'periods': len(rate_pct),
        'source':  'Bybit',
    }


def _from_binance(symbol='BTCUSDT', periods=21):
    url = f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit={periods}'
    data = _get(url)
    if not data:
        return None
    rate_pct = [float(d['fundingRate']) * 100 for d in data]
    avg = sum(rate_pct) / len(rate_pct)
    latest = rate_pct[-1]
    score = round(max(0, min(100, (avg + 0.05) / 0.15 * 100)))
    return {
        'latest':  round(latest, 5),
        'avg_7d':  round(avg, 5),
        'score':   score,
        'periods': len(rate_pct),
        'source':  'Binance',
    }


def _from_kraken_futures(symbol='PF_XBTUSD'):
    """Kraken Futures perpetual funding rate.

    Kraken returns fundingRate as an annualised decimal (e.g. 0.308 = 30.8% p.a.).
    Convert to per-8h % to match the Bybit/Binance score mapping:
        rate_8h_pct = kraken_rate / (365 * 3) * 100
    Only the current rate is available (no 7-day history endpoint), so
    latest == avg_7d as an approximation.
    """
    url = 'https://futures.kraken.com/derivatives/api/v3/tickers'
    data = _get(url)
    ticker = next((t for t in data.get('tickers', []) if t.get('symbol') == symbol), None)
    if ticker is None:
        return None
    raw_rate = ticker.get('fundingRate')
    if raw_rate is None:
        return None
    rate_8h_pct = float(raw_rate) / (365 * 3) * 100
    score = round(max(0, min(100, (rate_8h_pct + 0.05) / 0.15 * 100)))
    return {
        'latest':  round(rate_8h_pct, 5),
        'avg_7d':  round(rate_8h_pct, 5),   # only current rate available
        'score':   score,
        'periods': 1,
        'source':  'Kraken Futures',
    }


def get_funding_rate(symbol='BTCUSDT', periods=21):
    """Return funding rate dict aggregated from available exchanges.

    On CI (GitHub Actions), tries Kraken first — Bybit/Binance are geo-blocked.
    Locally, tries Bybit and Binance first (7-day history = accurate avg_7d),
    then falls back to Kraken (current rate only, periods=1).

    When multiple sources succeed, averages their avg_7d for a market-wide rate.
    """
    import os as _os
    on_ci = _os.environ.get('GITHUB_ACTIONS') == 'true'

    if on_ci:
        # Kraken works on CI; skip Bybit/Binance which are geo-blocked
        try:
            r = _from_kraken_futures()
            if r:
                return r
        except Exception as e:
            logger.warning('get_funding_rate Kraken failed: %s', e)
        return None

    results = []
    for fetch_fn, args, label in [
        (_from_bybit,   (symbol, periods), 'Bybit'),
        (_from_binance, (symbol, periods), 'Binance'),
    ]:
        try:
            r = fetch_fn(*args)
            if r and r.get('avg_7d') is not None:
                results.append(r)
        except Exception as e:
            logger.warning('get_funding_rate %s failed: %s', label, e)

    if results:
        avg_rate = sum(r['avg_7d'] for r in results) / len(results)
        latest = results[0]['latest']
        score = round(max(0, min(100, (avg_rate + 0.05) / 0.15 * 100)))
        return {
            'latest':  round(latest, 5),
            'avg_7d':  round(avg_rate, 5),
            'score':   score,
            'periods': results[0]['periods'],
            'source':  '+'.join(r['source'] for r in results),
        }

    # Kraken fallback for local runs when Bybit/Binance fail
    try:
        return _from_kraken_futures()
    except Exception as e:
        logger.warning('get_funding_rate Kraken fallback failed: %s', e)
    return None


__all__ = ['get_funding_rate']
