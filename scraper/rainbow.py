"""Scraper for Bitcoin Rainbow Chart band metric.

Source: https://www.bitcoinmagazinepro.com/charts/bitcoin-rainbow-chart/

Returns the name and color of the rainbow band the current BTC price falls in.
Bands (bottom → top):
  Below model   — price below log-regression model line
  Fire sale!    — rgba(68, 114, 196, ...)
  BUY!          — rgba(84, 152, 159, ...)
  Accumulate    — rgba(99, 190, 123, ...)
  Still cheap   — rgba(177, 213, 128, ...)
  HODL          — rgba(254, 235, 132, ...)
  Is this a bubble? — rgba(246, 180, 90, ...)
  FOMO intensifies  — rgba(237, 125, 49, ...)
  Sell. Seriously, sell! — rgba(214, 64, 24, ...)
  Maximum bubble territory — rgba(192, 2, 0, ...)
"""
import logging
from typing import Optional
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/bitcoin-rainbow-chart/'

# Band order lowest → highest (upper bound defines top of each band)
_BAND_ORDER = [
    'Fire sale!',
    'BUY!',
    'Accumulate',
    'Still cheap',
    'HODL',
    'Is this a bubble?',
    'FOMO intensifies',
    'Sell. Seriously, sell!',
    'Maximum bubble territory',
]


def get_rainbow_band(timeout: int = 60000) -> Optional[dict]:
    """Return current rainbow band info for BTC price.

    Returns dict with keys:
      band      — band label, e.g. 'Fire sale!' or 'Below model'
      color     — rgba fill color string
      price     — BTC price used
      band_upper — upper bound of the band at current date (None if 'Below model')
      date      — date string of the price data point
    Returns None on failure.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = ctx.new_page()
            page.goto(_BMP_URL, wait_until='networkidle', timeout=timeout)
            page.wait_for_timeout(4000)
            result = page.evaluate("""() => {
                const el = document.querySelector('.js-plotly-plot');
                if (!el || !el._fullData) return null;
                const btc = el._fullData.find(t => t.name === 'BTC Price');
                const model = el._fullData.find(t => t.name === 'model_price');
                if (!btc) return null;
                // find last non-null BTC price index
                let lastIdx = -1;
                for (let i = btc.y.length - 1; i >= 0; i--) {
                    if (btc.y[i] != null) { lastIdx = i; break; }
                }
                if (lastIdx < 0) return null;
                const lastDate = btc.x[lastIdx];
                const lastPrice = btc.y[lastIdx];
                const modelPrice = (model && model.y && model.y.length > lastIdx)
                    ? model.y[lastIdx] : null;
                // collect band upper bounds at lastIdx
                const bands = [];
                for (const t of el._fullData) {
                    if (!['BTC Price','model_price','Halving Dates'].includes(t.name)
                            && t.y && t.y.length > lastIdx) {
                        bands.push({
                            name: t.name,
                            upper: t.y[lastIdx],
                            fillcolor: t.fillcolor || null
                        });
                    }
                }
                // sort bands by upper bound ascending
                bands.sort((a, b) => a.upper - b.upper);
                return {lastDate, lastPrice, modelPrice, bands};
            }""")
            browser.close()

        if not result:
            logger.warning('get_rainbow_band: no result from BMP page')
            return None

        price = result['lastPrice']
        model_price = result['modelPrice']
        bands = result['bands']  # sorted ascending by upper bound

        # determine which band the price is in
        # if price < model_price (or below lowest band), it's "Below model"
        if model_price and price < model_price:
            return {
                'band': 'Below model',
                'color': 'rgba(68, 114, 196, 0.8)',  # same blue as Fire sale
                'price': round(price, 2),
                'band_upper': round(model_price, 2),
                'date': result['lastDate'],
            }

        matched = None
        for b in bands:
            if price < b['upper']:
                matched = b
                break

        if matched is None:
            matched = bands[-1]  # above all bands

        return {
            'band': matched['name'],
            'color': matched['fillcolor'],
            'price': round(price, 2),
            'band_upper': round(matched['upper'], 2),
            'date': result['lastDate'],
        }

    except Exception as e:
        logger.warning('get_rainbow_band failed: %s', e)
        return None


__all__ = ['get_rainbow_band']
