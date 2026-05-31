import requests
import math
import statistics
from datetime import datetime


def fetch_ohlcv_kraken(symbol='BTCUSDT', interval='1w', limit=500):
    # Kraken public API — no geo-blocking on GitHub Actions
    if interval == '1w':
        kraken_interval = 10080
    elif interval == '1d':
        kraken_interval = 1440
    else:
        kraken_interval = 60
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


def _divergence(series, lookback=24, peak_window=4, max_peak_age=6, kind='bearish'):
    """
    Detect divergence between price and wt1 among recent peaks/troughs.
    kind='bearish': higher price high + lower wt1 high  → overbought
    kind='bullish': lower price low  + higher wt1 low   → oversold
    Returns True only while the latest pivot is still fresh (<= max_peak_age candles ago).
    """
    valid = [s for s in series if s.get('wt1') is not None]
    if len(valid) < lookback:
        return False
    recent = valid[-lookback:]
    n = len(recent)
    pivots = []
    for i in range(peak_window, n - peak_window):
        c = recent[i]
        neighbors = recent[max(0, i - peak_window):i] + recent[i + 1:i + peak_window + 1]
        if kind == 'bearish':
            if all(c['close'] >= nb['close'] for nb in neighbors):
                pivots.append((i, c['close'], c['wt1']))
        else:
            if all(c['close'] <= nb['close'] for nb in neighbors):
                pivots.append((i, c['close'], c['wt1']))
    if len(pivots) < 2:
        return False
    _, prev_price, prev_wt1 = pivots[-2]
    last_i, last_price, last_wt1 = pivots[-1]
    if kind == 'bearish':
        detected = last_price > prev_price and last_wt1 < prev_wt1
    else:
        detected = last_price < prev_price and last_wt1 > prev_wt1
    return detected and (n - 1 - last_i) <= max_peak_age


def _bearish_divergence(series, **kw):
    return _divergence(series, kind='bearish', **kw)


def _bullish_divergence(series, **kw):
    return _divergence(series, kind='bullish', **kw)


def _fast_divergence(series, kind='bearish', lookback=20, peak_window=1, max_peak_age=3):
    """
    1-week fast divergence: compare the most recent price peak against the
    peak with the most extreme wt1 in the lookback window.
    Bearish: last price peak higher, but wt1 at that peak lower than the best prior wt1 peak.
    Bullish: last price trough lower, but wt1 higher than the worst prior wt1 trough.
    max_peak_age: how many candles ago the last pivot can be (1 = just confirmed).
    """
    valid = [s for s in series if s.get('wt1') is not None]
    if len(valid) < lookback:
        return False
    recent = valid[-lookback:]
    n = len(recent)
    pivots = []
    for i in range(peak_window, n - peak_window):
        c = recent[i]
        neighbors = recent[max(0, i - peak_window):i] + recent[i + 1:i + peak_window + 1]
        if kind == 'bearish':
            if all(c['close'] >= nb['close'] for nb in neighbors):
                pivots.append((i, c['close'], c['wt1']))
        else:
            if all(c['close'] <= nb['close'] for nb in neighbors):
                pivots.append((i, c['close'], c['wt1']))
    if len(pivots) < 2:
        return False
    last_i, last_price, last_wt1 = pivots[-1]
    # Compare last pivot vs the most extreme wt1 among all previous pivots
    if kind == 'bearish':
        ref = max(pivots[:-1], key=lambda p: p[2])  # highest wt1
        detected = last_price > ref[1] and last_wt1 < ref[2]
    else:
        ref = min(pivots[:-1], key=lambda p: p[2])  # lowest wt1
        detected = last_price < ref[1] and last_wt1 > ref[2]
    return detected and (n - 1 - last_i) <= max_peak_age


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

    # Money Flow Index (MFI) calculation
    mfi_raw = [None]*n
    for i, c in enumerate(ohlc):
        denom = c['high'] - c['low']
        if denom == 0:
            mfi_raw[i] = 0.0
        else:
            mfi_raw[i] = ((c['close'] - c['open']) / denom) * 150.0
    mfi_sma = sma(mfi_raw, 60)
    mfi = [None]*n
    for i in range(n):
        if mfi_sma[i] is not None:
            mfi[i] = mfi_sma[i] - 2.5

    # prepare output series
    series = []
    for i in range(n):
        item = {
            'timestamp': ohlc[i]['timestamp'],
            'close': ohlc[i]['close'],
            'wt1': wt1[i],
            'wt2': wt2[i],
            'mfi': mfi[i],
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

        # Compute divergences active at this index
        bearish_div = False
        bullish_div = False
        fast_bear = False
        fast_bull = False
        if i >= 30:
            # We construct a temporary series up to index i to check divergences
            sub_series = series + [item]
            bearish_div = _bearish_divergence(sub_series)
            bullish_div = _bullish_divergence(sub_series)
            fast_bear   = _fast_divergence(sub_series, kind='bearish')
            fast_bull   = _fast_divergence(sub_series, kind='bullish')

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
            'distance_to_sell': distance_to_sell,
            'bearish_divergence': bearish_div,
            'bullish_divergence': bullish_div,
            'fast_bearish_div': fast_bear,
            'fast_bullish_div': fast_bull
        })
        series.append(item)

    # return last valid entry
    last = None
    for it in reversed(series):
        if it['wt1'] is not None and it['wt2'] is not None:
            last = it.copy()
            break

    return {'series': series, 'last': last, 'params': {'channelLength': channelLength, 'averageLength': averageLength, 'wtSmaLength': wtSmaLength, 'oversoldLevel': oversoldLevel, 'overboughtLevel': overboughtLevel}}


def get_cipherb(symbol='BTCUSDT'):
    ohlc_w = fetch_ohlcv_kraken(symbol=symbol, interval='1w', limit=500)
    ohlc_d = fetch_ohlcv_kraken(symbol=symbol, interval='1d', limit=500)
    
    res_w = compute_cipherb_from_ohlcv(ohlc_w)
    res_d = compute_cipherb_from_ohlcv(ohlc_d)
    
    last_w = res_w.get('last')
    last_d = res_d.get('last')
    
    if not last_w or not last_d:
        raise ValueError("Failed to calculate Cipher B for weekly or daily timeframes.")
        
    last = {
        'timestamp': last_w['timestamp'],
        'close': last_w['close'],
        'wt1': last_w['wt1'],
        'wt2': last_w['wt2'],
        'mfi': last_w['mfi'],
        'weekly_score': last_w['weekly_score'],
        'fast_bearish_div': last_w['fast_bearish_div'],
        'fast_bullish_div': last_w['fast_bullish_div'],
        'daily_score': last_d['weekly_score'],
        'daily_fast_bearish_div': last_d['fast_bearish_div'],
        'daily_fast_bullish_div': last_d['fast_bullish_div'],
    }
    
    return {'series': res_w.get('series'), 'last': last, 'params': res_w.get('params')}


if __name__ == '__main__':
    # quick test
    res = get_cipherb('BTCUSDT')
    last = res.get('last')
    print('Last:', last)
