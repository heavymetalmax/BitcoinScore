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
    """Return dict with Mayer Multiple value, 200DMA (with 30d slope), 200WMA, regime, and score."""
    try:
        closes = fetch_daily_closes()
        if len(closes) < 200:
            logger.warning(f"get_mayer_multiple: Not enough data points ({len(closes)}/200)")
            return None
        current_price = closes[-1]
        dma_200 = sum(closes[-200:]) / 200
        mayer_multiple = current_price / dma_200

        dma_200_slope_30d = None
        if len(closes) >= 230:
            dma_200_prev30 = sum(closes[-230:-30]) / 200
            if dma_200_prev30 > 0:
                dma_200_slope_30d = round((dma_200 / dma_200_prev30 - 1.0) * 100.0, 2)

        wma_200 = None
        ma200w_ratio = None
        ma_regime = 'BULL_EXPANSION' if mayer_multiple >= 1.0 else 'BEAR_CONTRACTION'
        try:
            import datetime
            from scraper.cycle_normalizer import compute_ma_context
            ctx = compute_ma_context(
                datetime.date.today(),
                current_price=current_price,
                mayer_raw={
                    'value': mayer_multiple,
                    'dma_200': dma_200,
                    'dma_200_slope_30d': dma_200_slope_30d,
                },
            )
            wma_200 = ctx.get('wma_200')
            ma200w_ratio = ctx.get('ma200w_ratio')
            ma_regime = ctx.get('ma_regime', ma_regime)
            if dma_200_slope_30d is None:
                dma_200_slope_30d = ctx.get('dma_200_slope_30d')
        except Exception:
            pass

        # Scale score: MM <= 0.5 -> 0, MM >= 2.1 -> 100.
        score_val = max(0.5, min(2.1, mayer_multiple))
        score = round((score_val - 0.5) / 1.6 * 100)

        logger.info(
            f"get_mayer_multiple: MM={mayer_multiple:.2f} "
            f"(200DMA={dma_200:.1f}, slope30d={dma_200_slope_30d}%, "
            f"200WMA={wma_200}, regime={ma_regime}) -> score={score}"
        )
        return {
            'value': round(mayer_multiple, 3),
            'dma_200': round(dma_200, 1),
            'dma_200_slope_30d': dma_200_slope_30d,
            'above_dma_200': bool(mayer_multiple >= 1.0),
            'wma_200': wma_200,
            'ma200w_ratio': ma200w_ratio,
            'ma_regime': ma_regime,
            'current_price': round(current_price, 1),
            'score': score,
        }
    except Exception as e:
        logger.warning('get_mayer_multiple failed: %s', e)
        return None


__all__ = ['get_mayer_multiple']
