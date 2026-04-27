import requests
import math
from datetime import datetime
import statistics


def fetch_ohlcv_binance(symbol='BTCUSDT', interval='1w', limit=500):
    # Kraken public API — no geo-blocking on GitHub Actions
    kraken_interval = 10080 if interval == '1w' else 60
    url = 'https://api.kraken.com/0/public/OHLC'
    params = {'pair': 'XBTUSD', 'interval': kraken_interval}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get('error'):
        raise RuntimeError(f"Kraken error: {data['error']}")
    result = data['result']
    pair_key = next(k for k in result if k != 'last')
    rows = result[pair_key]
    rows = rows[-limit:]
    out = []
    for k in rows:
        out.append({
            'timestamp': int(k[0]),
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': float(k[6])
        })
    return out


BEARISH_LEG = 0
BULLISH_LEG = 1


def find_swings(ohlc, size=10):
    """
    LuxAlgo SMC leg(size) logic: find confirmed swing highs and lows.

    At bar i, the pivot at bar (i - size) is confirmed when:
      - Swing HIGH: ohlc[i-size]['high'] > max(ohlc[i-size+1 .. i]['high'])
      - Swing LOW : ohlc[i-size]['low']  < min(ohlc[i-size+1 .. i]['low'])

    Returns list of all confirmed pivots: {'type': 'high'|'low', 'price', 'ts', 'idx'}
    """
    n = len(ohlc)
    pivots = []
    leg = BEARISH_LEG  # var leg = 0 in PineScript

    for i in range(size, n):
        pivot_idx = i - size
        # ta.highest(size) / ta.lowest(size) = max/min of bars [pivot_idx+1 .. i]
        recent_max_high = max(ohlc[j]['high'] for j in range(pivot_idx + 1, i + 1))
        recent_min_low  = min(ohlc[j]['low']  for j in range(pivot_idx + 1, i + 1))

        new_leg_high = ohlc[pivot_idx]['high'] > recent_max_high
        new_leg_low  = ohlc[pivot_idx]['low']  < recent_min_low

        prev_leg = leg
        if new_leg_high:
            leg = BEARISH_LEG
        elif new_leg_low:
            leg = BULLISH_LEG

        if leg != prev_leg:
            if leg == BEARISH_LEG:  # 1→0: swing HIGH confirmed at pivot_idx
                pivots.append({
                    'type': 'high',
                    'price': ohlc[pivot_idx]['high'],
                    'ts': ohlc[pivot_idx]['timestamp'],
                    'idx': pivot_idx,
                })
            else:                   # 0→1: swing LOW confirmed at pivot_idx
                pivots.append({
                    'type': 'low',
                    'price': ohlc[pivot_idx]['low'],
                    'ts': ohlc[pivot_idx]['timestamp'],
                    'idx': pivot_idx,
                })

    # Most recent confirmed swing high and low
    swing_high = next((p for p in reversed(pivots) if p['type'] == 'high'), None)
    swing_low  = next((p for p in reversed(pivots) if p['type'] == 'low'),  None)
    return swing_high, swing_low, pivots


def compute_smc(ohlc, size=10):
    """
    Compute SMC position of current price between confirmed swing low (support)
    and swing high (resistance) on weekly bars.

    position: 0 = at support (discount), 100 = at resistance (premium).
    """
    swing_high, swing_low, pivots = find_swings(ohlc, size=size)

    price = ohlc[-1]['close']

    sh_price = swing_high['price'] if swing_high else None
    sl_price = swing_low['price']  if swing_low  else None

    if sh_price and sl_price and sh_price > sl_price:
        position = (price - sl_price) / (sh_price - sl_price) * 100.0
        position = round(max(0.0, min(100.0, position)), 2)
    else:
        position = None

    equilibrium = round((sh_price + sl_price) / 2, 2) if sh_price and sl_price else None

    return {
        'swing_high': round(sh_price, 2) if sh_price else None,
        'swing_high_ts': swing_high['ts'] if swing_high else None,
        'swing_low': round(sl_price, 2) if sl_price else None,
        'swing_low_ts': swing_low['ts'] if swing_low else None,
        'equilibrium': equilibrium,
        'current_price': round(price, 2),
        'position': position,   # 0–100: 0=discount/support, 100=premium/resistance
        'size': size,
    }


def get_smc(symbol='BTCUSDT', timeframe='1w', size=10):
    ohlc = fetch_ohlcv_binance(symbol=symbol, interval=timeframe, limit=500)
    res = compute_smc(ohlc, size=size)
    return {'series': ohlc, 'last': res}


if __name__ == '__main__':
    res = get_smc('BTCUSDT', timeframe='1w')
    last = res['last']
    print(f"Swing HIGH (resistance): {last['swing_high']}")
    print(f"Swing LOW  (support):    {last['swing_low']}")
    print(f"Equilibrium:             {last['equilibrium']}")
    print(f"Current price:           {last['current_price']}")
    print(f"Position (0=support, 100=resistance): {last['position']}")
