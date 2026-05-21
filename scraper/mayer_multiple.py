"""Mayer Multiple — Price / 200DMA.

Slightly smoothed by using daily close from Kraken.
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


def get_mayer_multiple():
    """Return dict with Mayer Multiple value, 200DMA, and score, or None on failure."""
    try:
        closes = fetch_daily_closes()
        if len(closes) < 200:
            logger.warning(f"get_mayer_multiple: Not enough data points ({len(closes)}/200)")
            return None
        current_price = closes[-1]
        dma_200 = sum(closes[-200:]) / 200
        mayer_multiple = current_price / dma_200
        
        # Scale score: MM <= 0.5 -> 0, MM >= 2.1 -> 100.
        score_val = max(0.5, min(2.1, mayer_multiple))
        score = round((score_val - 0.5) / 1.6 * 100)
        
        logger.info(f"get_mayer_multiple: MM={mayer_multiple:.2f} (200DMA={dma_200:.1f}, Price={current_price:.1f}) -> score={score}")
        return {
            'value': round(mayer_multiple, 3),
            'dma_200': round(dma_200, 1),
            'current_price': round(current_price, 1),
            'score': score
        }
    except Exception as e:
        logger.warning('get_mayer_multiple failed: %s', e)
        return None


__all__ = ['get_mayer_multiple']
