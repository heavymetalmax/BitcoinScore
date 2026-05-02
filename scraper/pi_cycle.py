"""Pi Cycle Top Indicator — 111DMA vs 2×350DMA on daily BTC/USD (Kraken).

Score mapping:
  gap_pct = (2×350DMA - 111DMA) / (2×350DMA) × 100
  score   = clamp((50 - gap_pct) / 50 × 100, 0, 100)

  gap_pct ≥ 50% → score = 0   (far from top, low risk)
  gap_pct = 25% → score = 50  (moderate risk)
  gap_pct = 0%  → score = 100 (cross confirmed, sell zone)
"""
import logging
import requests

logger = logging.getLogger(__name__)


def fetch_daily_closes():
    """Fetch daily BTC/USD closing prices from Kraken (up to ~720 bars)."""
    url = 'https://api.kraken.com/0/public/OHLC'
    params = {'pair': 'XBTUSD', 'interval': 1440}  # 1440 min = 1 day
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get('error'):
        raise RuntimeError(f"Kraken error: {data['error']}")
    result = data['result']
    pair_key = next(k for k in result if k != 'last')
    rows = result[pair_key]
    rows = rows[:-1]  # drop unclosed candle
    return [float(row[4]) for row in rows]


def compute_pi_cycle(closes):
    """Compute Pi Cycle Top values from a list of closing prices."""
    if len(closes) < 350:
        return None
    ma111 = sum(closes[-111:]) / 111
    ma350 = sum(closes[-350:]) / 350
    ma350x2 = ma350 * 2
    gap_pct = (ma350x2 - ma111) / ma350x2 * 100
    score = round(max(0, min(100, (50 - gap_pct) / 50 * 100)))
    return {
        'ma111':   round(ma111, 0),
        'ma350x2': round(ma350x2, 0),
        'gap_pct': round(gap_pct, 2),
        'score':   score,
        'cross':   gap_pct <= 0,
    }


def get_pi_cycle():
    """Return Pi Cycle dict or None on failure."""
    try:
        closes = fetch_daily_closes()
        return compute_pi_cycle(closes)
    except Exception as e:
        logger.warning('get_pi_cycle failed: %s', e)
        return None


__all__ = ['get_pi_cycle']
