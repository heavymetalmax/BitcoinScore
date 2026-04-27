import requests
import math
import statistics
from datetime import datetime


def fetch_ohlcv_kraken(symbol='BTCUSDT', interval='1w', limit=500):
    # Kraken public API — no geo-blocking on GitHub Actions
    # interval: '1w' -> 10080 minutes
    kraken_interval = 10080 if interval == '1w' else 60
    url = 'https://api.kraken.com/0/public/OHLC'
    params = {'pair': 'XBTUSD', 'interval': kraken_interval}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get('error'):
        raise RuntimeError(f"Kraken error: {data['error']}")
    # result key is usually 'XXBTZUSD'
    result = data['result']
    pair_key = next(k for k in result if k != 'last')
    rows = result[pair_key]
    rows = rows[:-1]  # drop last unclosed candle
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


def sma(series, length):
    if length <= 0:
        raise ValueError('length>0')
    out = [None]*len(series)
    s = 0.0
    for i, v in enumerate(series):
        if v is None:
            continue
        s += v
        if i >= length:
            if series[i-length] is not None:
                s -= series[i-length]
        if i >= length-1:
            out[i] = s / length
    return out


def ema(series, length):
    out = [None]*len(series)
    if length <= 0:
        raise ValueError('length>0')
    alpha = 2.0/(length+1.0)
    prev = None
    for i, v in enumerate(series):
        if v is None:
            continue
        if prev is None:
            # initialize with SMA of first 'length' values when available
            window = series[max(0, i-length+1):i+1]
            if len([x for x in window if x is not None]) < length:
                # not enough data yet
                prev = None
                continue
            prev = sum(window)/len(window)
            out[i] = prev
            continue
        prev = prev + alpha*(v - prev)
        out[i] = prev
    return out


def rma(series, length):
    # Pine's rma / Wilder MA (first value = sma of first length, then prev*(len-1)+x)/len
    out = [None]*len(series)
    if length <= 0:
        raise ValueError('length>0')
    prev = None
    for i, v in enumerate(series):
        if v is None:
            continue
        if prev is None:
            window = series[max(0, i-length+1):i+1]
            if len([x for x in window if x is not None]) < length:
                continue
            prev = sum(window)/len(window)
            out[i] = prev
            continue
        prev = (prev*(length-1) + v) / length
        out[i] = prev
    return out


def stdev_rolling(series, length):
    out = [None]*len(series)
    if length <= 0:
        raise ValueError('length>0')
    for i in range(len(series)):
        if i < length-1:
            continue
        window = series[i-length+1:i+1]
        if any(x is None for x in window):
            continue
        out[i] = statistics.pstdev(window)
    return out


def compute_cipherb_from_ohlcv(ohlc, channelLength=9, averageLength=12, wtSmaLength=3, oversoldLevel=-60, overboughtLevel=60):
    # ohlc: list of dicts with keys timestamp, open, high, low, close, volume
    n = len(ohlc)
    hlc3 = [None]*n
    for i, c in enumerate(ohlc):
        hlc3[i] = (c['high'] + c['low'] + c['close'])/3.0

    ap = hlc3
    esa = ema(ap, channelLength)
    # d = EMA(abs(ap-esa), channelLength)
    abs_dev = [None]*n
    for i in range(n):
        if ap[i] is None or esa[i] is None:
            abs_dev[i] = None
        else:
            abs_dev[i] = abs(ap[i] - esa[i])
    d = ema(abs_dev, channelLength)

    ci = [None]*n
    for i in range(n):
        if ap[i] is None or esa[i] is None or d[i] is None or d[i] == 0:
            ci[i] = None
        else:
            ci[i] = (ap[i] - esa[i]) / (0.015 * d[i])

    tci = ema(ci, averageLength)
    wt1 = tci
    wt2 = sma([x if x is not None else None for x in wt1], wtSmaLength)

    # prepare output series
    series = []
    for i in range(n):
        item = {
            'timestamp': ohlc[i]['timestamp'],
            'close': ohlc[i]['close'],
            'wt1': wt1[i],
            'wt2': wt2[i],
        }
        # signals
        buy = False
        sell = False
        if i>0 and wt1[i-1] is not None and wt2[i-1] is not None and wt1[i] is not None and wt2[i] is not None:
            if wt1[i-1] < wt2[i-1] and wt1[i] > wt2[i] and wt1[i] < oversoldLevel:
                buy = True
            if wt1[i-1] > wt2[i-1] and wt1[i] < wt2[i] and wt1[i] > overboughtLevel:
                sell = True
        # weekly score normalization
        score = None
        if wt1[i] is not None:
            if wt1[i] <= oversoldLevel:
                score = 0.0
            elif wt1[i] >= overboughtLevel:
                score = 100.0
            else:
                score = (wt1[i] - oversoldLevel) / (overboughtLevel - oversoldLevel) * 100.0
        # green/red dot representation (1/0) and distances
        green_dot = 1 if buy else 0
        red_dot = 1 if sell else 0
        distance_to_buy = score if score is not None else None
        distance_to_sell = (100.0 - score) if score is not None else None
        item.update({
            'buySignal': buy,
            'sellSignal': sell,
            'green_dot': green_dot,
            'red_dot': red_dot,
            'weekly_score': score,
            'distance_to_buy': distance_to_buy,
            'distance_to_sell': distance_to_sell
        })
        series.append(item)

    # return last valid entry
    last = None
    for it in reversed(series):
        if it['wt1'] is not None and it['wt2'] is not None:
            last = it
            break

    return {'series': series, 'last': last, 'params': {'channelLength': channelLength, 'averageLength': averageLength, 'wtSmaLength': wtSmaLength, 'oversoldLevel': oversoldLevel, 'overboughtLevel': overboughtLevel}}


def get_cipherb(symbol='BTCUSDT'):
    ohlc = fetch_ohlcv_kraken(symbol=symbol, interval='1w', limit=500)
    return compute_cipherb_from_ohlcv(ohlc)


if __name__ == '__main__':
    # quick test
    res = get_cipherb('BTCUSDT')
    last = res.get('last')
    print('Last:', last)
