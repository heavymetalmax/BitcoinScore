"""CoinMarketCap Pro API client.

Provides three data groups:
  - Fear & Greed index  (v3/fear-and-greed/latest)
  - BTC price + 24h change  (v2/cryptocurrency/quotes/latest)
  - Global market caps + BTC dominance  (v1/global-metrics/quotes/latest)

API key is read from the CMC_API_KEY environment variable (set via .env).
All functions return None on failure rather than raising, consistent with
the rest of the scraper codebase.
"""
import os
import requests

_BASE = 'https://pro-api.coinmarketcap.com'
_TIMEOUT = 15


def _key():
    k = os.environ.get('CMC_API_KEY', '').strip()
    return k if k else None


def _headers():
    return {'X-CMC_PRO_API_KEY': _key(), 'Accept': 'application/json'}


def available():
    """Return True if an API key is configured."""
    return _key() is not None


# ── Fear & Greed ─────────────────────────────────────────────────────────────

def get_fear_greed():
    """Return dict {'latest': float, 'avg_7d': float, 'label': str} or None on error.

    Fetches last 7 days via /v3/fear-and-greed/historical and returns both
    the latest value and the 7-day average for smoother scoring.
    """
    if not available():
        return None
    try:
        r = requests.get(
            f'{_BASE}/v3/fear-and-greed/historical',
            headers=_headers(),
            params={'limit': 7},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        entries = r.json().get('data', [])
        if not entries:
            return None
        values = [float(e['value']) for e in entries if e.get('value') is not None]
        if not values:
            return None
        latest = values[0]
        avg_7d = round(sum(values) / len(values), 1)
        return {'latest': round(latest, 1), 'avg_7d': avg_7d, 'label': 'CMC'}
    except Exception:
        return None


# ── BTC price ────────────────────────────────────────────────────────────────

def get_btc_price():
    """Return dict with BTC price data, or None on error.

    Keys: price (float USD), change_24h (float %), market_cap (float USD),
          volume_24h (float USD).

    Endpoint: GET /v2/cryptocurrency/quotes/latest?symbol=BTC&convert=USD
    """
    if not available():
        return None
    try:
        r = requests.get(
            f'{_BASE}/v2/cryptocurrency/quotes/latest',
            headers=_headers(),
            params={'symbol': 'BTC', 'convert': 'USD'},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        j = r.json()
        btc_list = j.get('data', {}).get('BTC', [])
        if not btc_list:
            return None
        q = btc_list[0]['quote']['USD']
        return {
            'price':      q.get('price'),
            'change_24h': q.get('percent_change_24h'),
            'market_cap': q.get('market_cap'),
            'volume_24h': q.get('volume_24h'),
        }
    except Exception:
        return None


# ── Global market metrics ────────────────────────────────────────────────────

def get_global_metrics():
    """Return dict with global market data, or None on error.

    Keys: btc_dominance (float %), total_market_cap (float USD),
          total_volume_24h (float USD), active_cryptocurrencies (int).

    Endpoint: GET /v1/global-metrics/quotes/latest
    """
    if not available():
        return None
    try:
        r = requests.get(
            f'{_BASE}/v1/global-metrics/quotes/latest',
            headers=_headers(),
            params={'convert': 'USD'},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        d = r.json().get('data', {})
        q = d.get('quote', {}).get('USD', {})
        return {
            'btc_dominance':          d.get('btc_dominance'),
            'total_market_cap':       q.get('total_market_cap'),
            'total_volume_24h':       q.get('total_volume_24h'),
            'active_cryptocurrencies': d.get('active_cryptocurrencies'),
        }
    except Exception:
        return None
