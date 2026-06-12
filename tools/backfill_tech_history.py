#!/usr/bin/env python3
"""Compute and save daily tech metric history from Kraken OHLCV (~720 days).

Writes:
  data/history/cipherb_btcusdt_1d.json   — daily CipherB series
  data/history/mayer_daily.json          — daily Mayer Multiple (price/200DMA)
  data/history/pi_cycle_daily.json       — daily Pi Cycle gap%

Also fetches historical BTC price from CoinGecko (full history since 2010) and
computes CVDD ratio using the existing cvdd_history.json:
  data/history/btc_price_history.json    — daily BTC/USD closes
  data/history/cvdd_ratio_history.json   — daily price/CVDD ratio

Usage:
    python tools/backfill_tech_history.py
"""
import sys, os, json, datetime, time
sys.path.insert(0, '.')

import requests

OUT_DIR = 'data/history'


# ── Shared OHLCV fetch ────────────────────────────────────────────────────────
def fetch_kraken_daily(limit=720):
    url = 'https://api.kraken.com/0/public/OHLC'
    r = requests.get(url, params={'pair': 'XBTUSD', 'interval': 1440}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get('error'):
        raise RuntimeError(f"Kraken: {data['error']}")
    result = data['result']
    key = next(k for k in result if k != 'last')
    rows = result[key][:-1][-limit:]
    ohlc = [{'timestamp': int(r[0]), 'open': float(r[1]), 'high': float(r[2]),
              'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[6])}
            for r in rows]
    base = datetime.date.today() - datetime.timedelta(days=len(ohlc)-1)
    for i, c in enumerate(ohlc):
        c['date'] = (base + datetime.timedelta(days=i)).isoformat()
    return ohlc


# ── CipherB daily ────────────────────────────────────────────────────────────
def save_cipherb_daily(ohlc):
    from scraper.cipherb import compute_cipherb_from_ohlcv
    result = compute_cipherb_from_ohlcv(ohlc)
    series = result['series']

    base = datetime.date.today() - datetime.timedelta(days=len(ohlc)-1)
    out = []
    for i, c in enumerate(series):
        if c.get('weekly_score') is None:
            continue
        out.append({
            'date':           (base + datetime.timedelta(days=i)).isoformat(),
            'close':          c['close'],
            'wt1':            round(c['wt1'], 4) if c.get('wt1') is not None else None,
            'daily_score':    round(c['weekly_score'], 4),
            'fast_bear_div':  c.get('fast_bearish_div', False),
            'fast_bull_div':  c.get('fast_bullish_div', False),
        })

    path = f'{OUT_DIR}/cipherb_btcusdt_1d.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'source': 'Kraken 1d', 'n': len(out),
                   'first': out[0]['date'] if out else None,
                   'last':  out[-1]['date'] if out else None,
                   'series': out}, f)
    print(f'  ✓ CipherB 1d: {len(out)} pts  {out[0]["date"]} → {out[-1]["date"]}  →  {path}')


# ── Mayer Multiple daily ─────────────────────────────────────────────────────
def save_mayer_daily(ohlc):
    closes = [c['close'] for c in ohlc]
    out = []
    for i, c in enumerate(ohlc):
        if i < 199:
            continue
        dma200 = sum(closes[i-199:i+1]) / 200
        mm = round(closes[i] / dma200, 4)
        out.append({'date': c['date'], 'close': closes[i],
                    'dma200': round(dma200, 2), 'mayer_multiple': mm})

    path = f'{OUT_DIR}/mayer_daily.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'source': 'Kraken 1d', 'n': len(out),
                   'first': out[0]['date'] if out else None,
                   'last':  out[-1]['date'] if out else None,
                   'series': out}, f)
    print(f'  ✓ Mayer daily: {len(out)} pts  {out[0]["date"]} → {out[-1]["date"]}  →  {path}')


# ── Pi Cycle daily ───────────────────────────────────────────────────────────
def save_pi_cycle_daily(ohlc):
    closes = [c['close'] for c in ohlc]
    out = []
    for i, c in enumerate(ohlc):
        if i < 349:
            continue
        ma111   = sum(closes[i-110:i+1]) / 111
        ma350x2 = sum(closes[i-349:i+1]) / 350 * 2
        gap_pct = round((ma350x2 - ma111) / ma350x2 * 100, 2)
        score   = max(0, min(100, round(100 - gap_pct)))
        out.append({'date': c['date'], 'close': closes[i],
                    'ma111': round(ma111, 2), 'ma350x2': round(ma350x2, 2),
                    'gap_pct': gap_pct, 'score': score, 'cross': gap_pct <= 0})

    path = f'{OUT_DIR}/pi_cycle_daily.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'source': 'Kraken 1d', 'n': len(out),
                   'first': out[0]['date'] if out else None,
                   'last':  out[-1]['date'] if out else None,
                   'series': out}, f)
    print(f'  ✓ Pi Cycle daily: {len(out)} pts  {out[0]["date"]} → {out[-1]["date"]}  →  {path}')


# ── BTC price full history (CoinGecko) ───────────────────────────────────────
def save_btc_price_history():
    print('  Fetching BTC price history from CoinGecko…')
    # CoinGecko free API: market_chart with max days
    url = 'https://api.coingecko.com/api/v3/coins/bitcoin/market_chart'
    params = {'vs_currency': 'usd', 'days': 'max', 'interval': 'daily'}
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    raw = r.json()
    prices = raw.get('prices', [])  # [[timestamp_ms, price], ...]

    out = []
    for ts_ms, price in prices:
        date = datetime.date.fromtimestamp(ts_ms / 1000).isoformat()
        out.append({'date': date, 'close': round(price, 2)})

    # de-dup by date (keep last)
    seen = {}
    for row in out:
        seen[row['date']] = row['close']
    out = [{'date': d, 'close': v} for d, v in sorted(seen.items())]

    path = f'{OUT_DIR}/btc_price_history.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'source': 'CoinGecko', 'n': len(out),
                   'first': out[0]['date'] if out else None,
                   'last':  out[-1]['date'] if out else None,
                   'series': out}, f)
    print(f'  ✓ BTC price: {len(out)} pts  {out[0]["date"]} → {out[-1]["date"]}  →  {path}')
    return {r['date']: r['close'] for r in out}


# ── CVDD ratio = BTC price / CVDD price ─────────────────────────────────────
def save_cvdd_ratio(price_map):
    cvdd_path = f'{OUT_DIR}/cvdd_history.json'
    if not os.path.exists(cvdd_path):
        print('  ! CVDD history not found — skipping ratio')
        return

    with open(cvdd_path, encoding='utf-8') as f:
        cvdd_data = json.load(f)
    cvdd_map = {r[0][:10]: r[1] for r in cvdd_data['series'] if r[1] is not None and r[1] > 0}

    out = []
    for date, cvdd_price in sorted(cvdd_map.items()):
        btc = price_map.get(date)
        if btc is None:
            continue
        ratio = round(btc / cvdd_price, 4)
        if 0.5 <= ratio <= 20:  # sanity filter
            out.append({'date': date, 'btc_price': btc, 'cvdd_price': round(cvdd_price, 2), 'ratio': ratio})

    path = f'{OUT_DIR}/cvdd_ratio_history.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'source': 'BMP + CoinGecko', 'n': len(out),
                   'first': out[0]['date'] if out else None,
                   'last':  out[-1]['date'] if out else None,
                   'series': out}, f)
    print(f'  ✓ CVDD ratio: {len(out)} pts  {out[0]["date"]} → {out[-1]["date"]}  →  {path}')


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print('Fetching Kraken daily OHLCV (720 days)…')
    ohlc = fetch_kraken_daily(720)
    print(f'  Got {len(ohlc)} candles  {ohlc[0]["date"]} → {ohlc[-1]["date"]}')

    print('\nComputing tech metrics from Kraken…')
    save_cipherb_daily(ohlc)
    save_mayer_daily(ohlc)
    save_pi_cycle_daily(ohlc)

    print('\nFetching BTC price history (CoinGecko)…')
    price_map = save_btc_price_history()

    print('\nComputing CVDD ratio…')
    save_cvdd_ratio(price_map)

    print('\nDone.')


if __name__ == '__main__':
    main()
