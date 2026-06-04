"""
Zone-price inversion: at what BTC price would the index reach the Buy (<=20)
or Sell (>=80) zone?

This is NOT a forecast. The index is a (near-)monotone function of price, so we
invert it: we re-run the EXACT scoring pipeline (scoring.compute_scores) at
hypothetical prices and binary-search for the price at which the final score
crosses a zone boundary.

The price-linked metrics are rescaled cleanly, holding the slow on-chain
baselines (realized cap, supply, 200-day MA) at today's values:

    NUPL    = 1 - RC/MC          ->  NUPL' = 1 - (1-NUPL)/k
    MVRV-Z  linear in market cap ->  Z'    = (k - (1-NUPL)) / (NUPL / Z)
    Mayer   = price / 200dMA     ->  Mayer'= price' / dma200
    CVDD    = price / slow floor ->  CVDD' = CVDD * k

A real move also drags momentum (Cipher B) and sentiment (Fear & Greed, aSOPR)
along with it, so we let those track the move: down into capitulation for the
Buy case, up into euphoria for the Sell case. RHODL and the macro inputs
(yield curve, M2) are held flat. The result answers "if price moved there soon,
with the usual co-movement of momentum and sentiment, where does the index sit".
"""
import copy
from . import scoring as S

BUY_THRESHOLD = 20
SELL_THRESHOLD = 80


def _rescale(metrics, k, price0, direction):
    """metrics for hypothetical price = price0 * k.
    direction: 'down' (capitulation co-move) or 'up' (euphoria co-move)."""
    m = copy.deepcopy(metrics)

    def gv(key, src=metrics):
        o = src.get(key)
        return o['value'] if isinstance(o, dict) and 'value' in o else o

    def sv(key, val):
        if isinstance(m.get(key), dict) and 'value' in m[key]:
            m[key]['value'] = val
        else:
            m[key] = val

    nupl0 = gv('nupl')
    z0 = gv('mvrv')
    cvdd0 = gv('cvdd_ratio')
    nf0 = (nupl0 or 0) / 100.0

    # NUPL: realized cap + supply held constant
    sv('nupl', round((1.0 - (1.0 - nf0) / k) * 100.0, 4))
    # MVRV-Z: sigma pinned from today's (NUPL, Z)
    if z0 and nf0:
        sv('mvrv', round((k - (1.0 - nf0)) / (nf0 / z0), 4))
    # CVDD ratio scales with price
    if cvdd0 is not None:
        sv('cvdd_ratio', round(cvdd0 * k, 4))
    # Mayer: price / 200dMA (held), update ratio + precomputed score
    mm = m.get('mayer_multiple')
    mm = mm.get('value') if isinstance(mm, dict) and 'value' in mm else mm
    if isinstance(mm, dict) and mm.get('dma_200'):
        new_price = price0 * k
        ratio = new_price / mm['dma_200']
        mm['value'] = round(ratio, 4)
        rr = max(0.5, min(2.1, ratio))
        mm['score'] = round((rr - 0.5) / 1.6 * 100)
        mm['current_price'] = round(new_price, 2)

    # momentum + sentiment track the move
    move = abs(k - 1.0)
    cb = m.get('cipherb')
    cb = cb.get('value') if isinstance(cb, dict) and 'value' in cb else cb
    fg0 = 50.0
    fgsrc = metrics.get('fear_greed')
    if isinstance(fgsrc, dict):
        inner = fgsrc.get('value', fgsrc)
        if isinstance(inner, dict):
            fg0 = inner.get('avg_7d') or inner.get('latest') or fg0
        elif isinstance(inner, (int, float)):
            fg0 = inner
    asopr0 = gv('asopr') or 1.0

    if direction == 'down':
        f = max(0.0, 1.0 - move / 0.35)           # WaveTrend oversold by ~-35%
        if isinstance(cb, dict):
            cb['weekly_score'] = (cb.get('weekly_score') or 0) * f
            cb['daily_score'] = (cb.get('daily_score') or 0) * f
            cb['fast_bearish_div'] = cb['fast_bullish_div'] = False
        fg_new = max(8.0, fg0 - move * 110.0)
        sv('asopr', max(0.85, asopr0 - move * 0.35))
    else:  # up / euphoria
        f = min(1.0, move / 0.6)
        if isinstance(cb, dict):
            cb['weekly_score'] = (cb.get('weekly_score') or 0) + (100 - (cb.get('weekly_score') or 0)) * f
            cb['daily_score'] = (cb.get('daily_score') or 0) + (100 - (cb.get('daily_score') or 0)) * f
            cb['fast_bearish_div'] = cb['fast_bullish_div'] = False
        fg_new = min(92.0, fg0 + move * 110.0)
        sv('asopr', min(1.15, asopr0 + move * 0.35))

    fgm = m.get('fear_greed')
    if isinstance(fgm, dict) and 'value' in fgm:
        fgm['value'] = {'avg_7d': fg_new, 'latest': fg_new}
    else:
        m['fear_greed'] = fg_new
    return m


def _score(metrics, k, price0, direction):
    S._HIST_CACHE.clear()
    return S.compute_scores(_rescale(metrics, k, price0, direction))['final_score']


def _solve(metrics, price0, threshold, direction):
    """Binary-search the price multiplier where final_score crosses threshold.
    score is monotone increasing in k. Returns price or None if unreachable."""
    if direction == 'down':
        lo, hi = 0.02, 1.0
        if _score(metrics, hi, price0, direction) <= threshold:
            return price0            # already in/below zone at current price
        if _score(metrics, lo, price0, direction) > threshold:
            return None              # cannot reach even near price 0
    else:
        lo, hi = 1.0, 25.0
        if _score(metrics, lo, price0, direction) >= threshold:
            return price0
        if _score(metrics, hi, price0, direction) < threshold:
            return None
    for _ in range(64):
        mid = (lo + hi) / 2
        s = _score(metrics, mid, price0, direction)
        if direction == 'down':
            if s > threshold:
                hi = mid
            else:
                lo = mid
        else:
            if s < threshold:
                lo = mid
            else:
                hi = mid
    return price0 * (lo + hi) / 2


def compute_zone_forecast(metrics, btc_price):
    """Return the inverted Buy/Sell zone prices for the current data state."""
    if not btc_price or not metrics:
        return None
    try:
        nupl0 = metrics.get('nupl', {})
        nupl0 = nupl0.get('value') if isinstance(nupl0, dict) else nupl0
        realized = btc_price * (1.0 - (nupl0 or 0) / 100.0)
        buy = _solve(metrics, btc_price, BUY_THRESHOLD, 'down')
        sell = _solve(metrics, btc_price, SELL_THRESHOLD, 'up')

        def pack(price):
            if price is None:
                return {'price': None, 'pct': None}
            return {'price': round(price), 'pct': round((price / btc_price - 1) * 100, 1)}

        return {
            'buy': pack(buy),
            'sell': pack(sell),
            'buy_threshold': BUY_THRESHOLD,
            'sell_threshold': SELL_THRESHOLD,
            'realized_price': round(realized),
            'ref_price': round(btc_price),
        }
    except Exception:
        return None
