#!/usr/bin/env python3
"""Trading simulation engine for ML experiment.

Given an agent config (weights + thresholds + strategy rules),
simulates daily trading on the ml_dataset and returns performance metrics.

Agent config schema:
{
  "agent_name": str,
  "weights": {
    "nupl": float,          # weights can include any subset of metrics
    "mvrv": float,          # they are renormalized over non-null values
    "rhodl": float,
    "puell": float,
    "asopr": float,
    "cvdd_ratio": float,
    "cipherb_weekly": float,
    "cipherb_daily": float,
    "mayer": float,
    "pi_cycle_gap": float,
    "global_m2": float,
  },
  "map_overrides": {        # optional: override default metric→score mapping
    "nupl": [[raw, score], ...]   # piecewise linear breakpoints
  },
  "buy_threshold": float,   # buy when index <= this (0-100)
  "sell_threshold": float,  # sell when index >= this (0-100)
  "min_days_in_zone": int,  # minimum consecutive/cumulative days in buy zone before buying
  "entry_style": str,       # "lump_sum" | "dca"
  "dca_tranches": int,      # number of DCA buys (if entry_style=dca)
  "dca_interval_days": int, # days between DCA tranches
  "exit_style": str,        # "lump_sum" | "gradual"
  "rationale": str
}
"""
import sys, os, json, math, datetime
from typing import Optional

DATASET_PATH = os.path.join(os.path.dirname(__file__), 'ml_dataset.json')

# ── Default metric→score mappings (piecewise linear, returns 0-100) ──────────

def _piecewise(v, breakpoints):
    """Interpolate v against [(raw, score), ...] breakpoints."""
    if v is None:
        return None
    bp = sorted(breakpoints, key=lambda x: x[0])
    if v <= bp[0][0]:
        return float(bp[0][1])
    if v >= bp[-1][0]:
        return float(bp[-1][1])
    for i in range(len(bp) - 1):
        r0, s0 = bp[i]
        r1, s1 = bp[i+1]
        if r0 <= v <= r1:
            t = (v - r0) / (r1 - r0)
            return s0 + t * (s1 - s0)
    return None


DEFAULT_MAPS = {
    # (raw_value, risk_score 0-100)
    # NUPL: stored as % (×100). -50=deepest bear, 0=neutral, 75+=top
    'nupl':          [(-45, 0),  (-20, 10), (0, 25),    (20, 40),  (40, 60),  (60, 80),  (79, 100)],
    # MVRV Z-score: -0.5=bottom, 1=neutral, 5+=top
    'mvrv':          [(-0.5, 0), (0.0, 10), (1.0, 30),  (3.0, 60), (5.0, 80), (7.0, 100)],
    # RHODL ratio: 200=bottom, 1300=neutral, 30000+=top
    'rhodl':         [(200, 0),  (600, 10), (1300, 25), (5000, 50),(20000, 75),(50000, 95),(105000, 100)],
    # Puell Multiple: <0.5=bottom, 1=neutral, 3+=top
    'puell':         [(0.3, 0),  (0.5, 15), (1.0, 40),  (2.0, 65), (4.0, 90), (6.0, 100)],
    # CVDD ratio: btc/cvdd. <1.5=bottom, 2.5=neutral, 6+=top
    'cvdd_ratio':    [(1.0, 0),  (1.5, 15), (2.3, 40),  (3.5, 60), (5.0, 80), (7.0, 95), (11.0, 100)],
    # CipherB 0-100 → 0-100 direct
    'cipherb_weekly':[(0, 0),    (25, 25),  (50, 50),   (75, 75),  (100, 100)],
    'cipherb_daily': [(0, 0),    (25, 25),  (50, 50),   (75, 75),  (100, 100)],
    # Mayer: 0.48=extreme bottom, 1.0=fair value, 2.4=top
    'mayer':         [(0.48, 0), (0.7, 5),  (0.85, 15), (1.0, 30), (1.3, 55), (1.8, 75), (2.4, 100)],
    # Pi Cycle gap%: large positive = safe, near 0 / negative = top
    'pi_cycle_gap':  [(-5, 100), (5, 90),   (20, 70),   (40, 50),  (55, 30),  (65, 15),  (72, 5)],
    # Global M2 (lagged 90d), indexed ~100. Low=bear, high=bull
    'global_m2':     [(63, 0),   (70, 15),  (79, 35),   (87, 55),  (91, 70),  (93, 85),  (95, 100)],
    # aSOPR: data range 0.0–0.107 (non-standard scale — use with caution)
    'asopr':         [(0.0, 20), (0.001, 30),(0.003, 50),(0.01, 70),(0.04, 85),(0.10, 100)],
}


def score_row(row: dict, config: dict) -> Optional[float]:
    """Compute agent's index score (0-100) for a single data row."""
    weights = config.get('weights', {})
    maps    = {**DEFAULT_MAPS, **config.get('map_overrides', {})}

    total_w = 0.0
    total_s = 0.0
    for metric, weight in weights.items():
        raw = row.get(metric)
        if raw is None:
            continue
        bp = maps.get(metric)
        if bp is None:
            continue
        score = _piecewise(raw, bp)
        if score is None:
            continue
        total_w += weight
        total_s += weight * score

    if total_w == 0:
        return None
    return round(total_s / total_w, 2)


# ── Portfolio simulation ──────────────────────────────────────────────────────

def simulate(config: dict, date_from='2017-08-17', date_to='2023-12-31',
             initial_cash=10_000.0):
    """Simulate trading based on agent config. Returns performance dict."""
    with open(DATASET_PATH, encoding='utf-8') as f:
        dataset = json.load(f)

    rows = [r for r in dataset['series']
            if date_from <= r['date'] <= date_to]

    buy_thr   = config.get('buy_threshold', 30)
    sell_thr  = config.get('sell_threshold', 70)
    min_days  = config.get('min_days_in_zone', 1)
    entry     = config.get('entry_style', 'lump_sum')
    tranches  = config.get('dca_tranches', 4)
    interval  = config.get('dca_interval_days', 14)
    exit_sty  = config.get('exit_style', 'lump_sum')

    cash = initial_cash
    btc  = 0.0
    trades = []
    daily  = []

    days_in_buy_zone = 0
    dca_queue = []   # list of (target_date, usd_amount) pending DCA buys

    for row in rows:
        date  = row['date']
        price = row.get('btc_price')
        if price is None or price <= 0:
            continue

        index = score_row(row, config)

        # Process pending DCA queue
        executed = []
        for (target_date, usd_amt) in dca_queue:
            if date >= target_date and cash >= usd_amt:
                btc_bought = usd_amt / price
                btc  += btc_bought
                cash -= usd_amt
                trades.append({'date': date, 'type': 'dca_buy',
                               'price': price, 'usd': usd_amt, 'btc': round(btc_bought, 6)})
                executed.append((target_date, usd_amt))
        for e in executed:
            dca_queue.remove(e)

        # Signals
        if index is not None:
            if index <= buy_thr:
                days_in_buy_zone += 1
            else:
                days_in_buy_zone = 0

            # BUY signal
            if index <= buy_thr and days_in_buy_zone >= min_days and cash > 100:
                if entry == 'lump_sum':
                    usd_to_spend = cash
                    btc_bought   = usd_to_spend / price
                    btc  += btc_bought
                    cash -= usd_to_spend
                    trades.append({'date': date, 'type': 'buy',
                                   'index': index, 'price': price,
                                   'usd': round(usd_to_spend, 2),
                                   'btc': round(btc_bought, 6)})
                    days_in_buy_zone = 0   # reset after buy

                elif entry == 'dca':
                    if not dca_queue and cash > 100:
                        tranche_usd = cash / tranches
                        for i in range(tranches):
                            target = (datetime.date.fromisoformat(date) +
                                      datetime.timedelta(days=i * interval)).isoformat()
                            dca_queue.append((target, tranche_usd))
                        trades.append({'date': date, 'type': 'dca_schedule',
                                       'index': index, 'price': price,
                                       'tranches': tranches, 'interval_days': interval,
                                       'total_usd': round(cash, 2)})
                        days_in_buy_zone = 0

            # SELL signal
            elif index >= sell_thr and btc > 0.0001:
                if exit_sty == 'lump_sum':
                    usd_received = btc * price
                    trades.append({'date': date, 'type': 'sell',
                                   'index': index, 'price': price,
                                   'btc': round(btc, 6), 'usd': round(usd_received, 2)})
                    cash += usd_received
                    btc   = 0.0
                elif exit_sty == 'gradual':
                    # Sell 25% of BTC position each time signal fires
                    sell_btc  = btc * 0.25
                    usd_recv  = sell_btc * price
                    btc  -= sell_btc
                    cash += usd_recv
                    trades.append({'date': date, 'type': 'partial_sell',
                                   'index': index, 'price': price,
                                   'btc': round(sell_btc, 6), 'usd': round(usd_recv, 2)})

        portfolio_value = cash + btc * price
        daily.append({
            'date':   date,
            'index':  index,
            'price':  price,
            'cash':   round(cash, 2),
            'btc':    round(btc, 6),
            'value':  round(portfolio_value, 2),
        })

    # Final portfolio value
    last_price = rows[-1].get('btc_price', 0)
    final_value = cash + btc * last_price

    # Performance metrics
    values = [d['value'] for d in daily]
    peak = initial_cash
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd

    total_return = (final_value - initial_cash) / initial_cash * 100

    # Buy-and-hold benchmark
    first_price = rows[0].get('btc_price', 1)
    bnh_btc   = initial_cash / first_price
    bnh_value = bnh_btc * last_price
    bnh_return = (bnh_value - initial_cash) / initial_cash * 100

    return {
        'agent_name':    config.get('agent_name', 'unknown'),
        'period':        f'{date_from} → {date_to}',
        'initial_cash':  initial_cash,
        'final_value':   round(final_value, 2),
        'total_return':  round(total_return, 2),
        'bnh_return':    round(bnh_return, 2),
        'alpha':         round(total_return - bnh_return, 2),
        'max_drawdown':  round(max_dd * 100, 2),
        'n_trades':      len([t for t in trades if t['type'] in ('buy','sell','dca_buy')]),
        'btc_remaining': round(btc, 6),
        'cash_remaining':round(cash, 2),
        'trades':        trades,
        'daily':         daily,
    }


def print_summary(result):
    print(f"\n{'='*55}")
    print(f"  {result['agent_name']}")
    print(f"{'='*55}")
    print(f"  Period:       {result['period']}")
    print(f"  Final value:  ${result['final_value']:,.2f}  (started $10,000)")
    print(f"  Return:       {result['total_return']:+.1f}%")
    print(f"  B&H return:   {result['bnh_return']:+.1f}%")
    print(f"  Alpha vs B&H: {result['alpha']:+.1f}%")
    print(f"  Max drawdown: {result['max_drawdown']:.1f}%")
    print(f"  Trades:       {result['n_trades']}")
    buy_trades = [t for t in result['trades'] if t['type'] == 'buy']
    if buy_trades:
        avg_buy_idx = sum(t.get('index', 0) for t in buy_trades) / len(buy_trades)
        print(f"  Avg buy idx:  {avg_buy_idx:.1f}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('config', help='Path to agent config JSON')
    parser.add_argument('--from', dest='date_from', default='2017-08-17')
    parser.add_argument('--to',   dest='date_to',   default='2023-12-31')
    args = parser.parse_args()

    with open(args.config, encoding='utf-8') as f:
        cfg = json.load(f)

    result = simulate(cfg, args.date_from, args.date_to)
    print_summary(result)

    out_path = args.config.replace('configs/', 'results/').replace('.json', '_result.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2)
    print(f'\n  Saved → {out_path}')
