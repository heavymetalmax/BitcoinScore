"""Grid-search strategy tester: buy/sell ML-score thresholds × min_tiz_days.

TiZ (Time-in-Zone) = how many consecutive days the ML score has been
in the buy zone (≤ buy_threshold). A buy signal at tiz_days=1 means
we just entered the zone — might be noise. At tiz_days≥14 the signal
is confirmed.

Usage: python3 tools/strategy_tester.py
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

ML_HIST  = '/tmp/btcbri_ml_history.json'
TIZ_DATA = os.path.join(os.path.dirname(__file__), '..', 'data', 'ml_training_data.json')

INITIAL_CASH = 10_000.0


def load_rows():
    with open(ML_HIST) as f:
        hist = {r['d']: r for r in json.load(f)}
    with open(TIZ_DATA) as f:
        tiz = {r['date']: r['features'].get('tiz_days', 0)
               for r in json.load(f)['records']}
    rows = []
    for date in sorted(hist):
        h = hist[date]
        if h['p'] and h['ml'] is not None:
            rows.append({
                'date':     date,
                'ml':       h['ml'],
                'price':    h['p'],
                'tiz_days': tiz.get(date, 0),
            })
    return rows


def simulate(rows, buy_thr, sell_thr, min_tiz):
    cash, btc = INITIAL_CASH, 0.0
    trades = []
    days_in_zone = 0

    for r in rows:
        ml    = r['ml']
        price = r['price']

        # track consecutive days in buy zone
        if ml <= buy_thr:
            days_in_zone += 1
        else:
            days_in_zone = 0

        # BUY: in zone long enough + have cash
        if ml <= buy_thr and days_in_zone >= min_tiz and cash > 10:
            btc_bought = cash / price
            btc  += btc_bought
            cash  = 0.0
            trades.append({'t': 'B', 'd': r['date'], 'p': price, 'ml': ml})
            days_in_zone = 0   # reset so we don't rebuy next day

        # SELL: above sell threshold + holding BTC
        elif ml >= sell_thr and btc > 0:
            cash += btc * price
            trades.append({'t': 'S', 'd': r['date'], 'p': price, 'ml': ml,
                           'usd': round(btc * price, 2)})
            btc = 0.0

    last_price = rows[-1]['price']
    final = cash + btc * last_price

    # max drawdown
    peak, max_dd = INITIAL_CASH, 0.0
    cash_run, btc_run = INITIAL_CASH, 0.0
    diz = 0
    for r in rows:
        ml, price = r['ml'], r['price']
        if ml <= buy_thr:
            diz += 1
        else:
            diz = 0
        if ml <= buy_thr and diz >= min_tiz and cash_run > 10:
            btc_run += cash_run / price
            cash_run  = 0.0
            diz = 0
        elif ml >= sell_thr and btc_run > 0:
            cash_run += btc_run * price
            btc_run   = 0.0
        v = cash_run + btc_run * price
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd

    n_buys  = sum(1 for t in trades if t['t'] == 'B')
    n_sells = sum(1 for t in trades if t['t'] == 'S')
    ret = (final - INITIAL_CASH) / INITIAL_CASH * 100

    return {
        'buy_thr':  buy_thr,
        'sell_thr': sell_thr,
        'min_tiz':  min_tiz,
        'ret':      round(ret, 1),
        'max_dd':   round(max_dd * 100, 1),
        'n_buys':   n_buys,
        'n_sells':  n_sells,
        'final':    round(final, 0),
        'trades':   trades,
    }


def main():
    print("Loading data...")
    rows = load_rows()
    print(f"  {len(rows)} days  ({rows[0]['date']} → {rows[-1]['date']})")

    # buy-and-hold benchmark
    bnh = (rows[-1]['price'] / rows[0]['price'] - 1) * 100
    print(f"  Buy-and-hold: {bnh:.0f}%\n")

    results = []
    total = 0
    for buy_thr in range(10, 36, 5):          # 10 15 20 25 30 35
        for sell_thr in range(55, 86, 5):     # 55 60 65 70 75 80 85
            for min_tiz in [1, 3, 7, 14, 21, 30]:
                if buy_thr >= sell_thr:
                    continue
                r = simulate(rows, buy_thr, sell_thr, min_tiz)
                results.append(r)
                total += 1

    print(f"Tested {total} combinations\n")

    # Top 15 by return
    top = sorted(results, key=lambda x: -x['ret'])[:15]
    print(f"{'BUY':>4} {'SELL':>5} {'TIZ':>4} {'RETURN':>9} {'MAX_DD':>7} {'TRADES':>7}")
    print('─' * 50)
    for r in top:
        print(f"≤{r['buy_thr']:2d}  ≥{r['sell_thr']:2d}  min{r['min_tiz']:2d}d  "
              f"{r['ret']:>8.0f}%  {r['max_dd']:>5.1f}%  "
              f"{r['n_buys']}B/{r['n_sells']}S")

    print(f"\nTop 10 by return with max DD < 35%:")
    print(f"{'BUY':>4} {'SELL':>5} {'TIZ':>4} {'RETURN':>9} {'MAX_DD':>7} {'TRADES':>7}")
    print('─' * 50)
    low_dd = [r for r in results if r['max_dd'] < 35]
    low_dd_top = sorted(low_dd, key=lambda x: -x['ret'])[:10]
    for r in low_dd_top:
        print(f"≤{r['buy_thr']:2d}  ≥{r['sell_thr']:2d}  min{r['min_tiz']:2d}d  "
              f"{r['ret']:>8.0f}%  {r['max_dd']:>5.1f}%  "
              f"{r['n_buys']}B/{r['n_sells']}S")

    print(f"\nBest strategy trades:")
    best = low_dd_top[0] if low_dd_top else top[0]
    for t in best['trades']:
        arrow = '▲ BUY ' if t['t'] == 'B' else '▼ SELL'
        print(f"  {arrow}  {t['d']}  ${t['p']:>8,.0f}  ML={t['ml']}")


if __name__ == '__main__':
    main()
