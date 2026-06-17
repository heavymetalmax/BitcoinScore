import sys
import os
import json
import datetime
import numpy as np

sys.path.insert(0, '.')

from tools.backtest import load_data, compute_at

def run_simulation():
    print("Loading historical data...")
    series = load_data()
    
    with open('data/history/scores.json', 'r', encoding='utf-8') as f:
        scores_history = json.load(f)
    scores_history.sort(key=lambda x: x['date'])
    
    dates = [x['date'] for x in scores_history]
    prices = {x['date']: x['btc_price'] for x in scores_history}
    
    daily_metrics = []
    
    print("Extracting daily metrics...")
    for i, date_str in enumerate(dates):
        if i % 500 == 0:
            print(f"  Processed {i}/{len(dates)} days...")
        td = datetime.date.fromisoformat(date_str)
        r = compute_at(td, series)
        sc = r['scores']
        
        nupl_raw = r['raw'].get('nupl')
        cb_raw = r['raw'].get('cipherb')
        cb_val = None
        if isinstance(cb_raw, dict):
            w = cb_raw.get('weekly_score')
            d = cb_raw.get('daily_score')
            if w is not None and d is not None:
                cb_val = 0.8 * w + 0.2 * d
            elif w is not None:
                cb_val = w
        else:
            cb_val = cb_raw
            
        daily_metrics.append({
            'date': date_str,
            'price': prices[date_str],
            'nupl_raw': nupl_raw,
            'cb_raw': cb_val,
            'nupl_norm': sc.get('nupl'),
            'cb_norm': sc.get('cipherb'),
        })

    # Let's run with CUMULATIVE history (all data since start up to target date)
    # This prevents the memory decay of the 3-year rolling window!
    
    initial_cash = 10000.0
    cash = initial_cash
    btc = 0.0
    trades = []
    peak = initial_cash
    max_dd = 0.0
    last_action = 'SELL'
    
    # Warmup window: 365 days
    warmup = 365
    
    for idx in range(warmup, len(daily_metrics)):
        d = daily_metrics[idx]
        price = d['price']
        
        # Calculate portfolio value and max drawdown
        port_val = cash + btc * price
        if port_val > peak:
            peak = port_val
        dd = (peak - port_val) / peak
        if dd > max_dd:
            max_dd = dd
            
        # Cumulative history up to yesterday
        hist_slice = daily_metrics[0:idx]
        
        hist_nupl = [h['nupl_raw'] for h in hist_slice if h['nupl_raw'] is not None]
        hist_cb = [h['cb_raw'] for h in hist_slice if h['cb_raw'] is not None]
        
        if len(hist_nupl) < 100 or len(hist_cb) < 100:
            continue
            
        # Dynamic buy/sell thresholds using entire cumulative history
        dyn_nupl_buy_thr = np.percentile(hist_nupl, 15)
        dyn_nupl_sell_thr = np.percentile(hist_nupl, 85)
        
        dyn_cb_buy_thr = np.percentile(hist_cb, 10)
        dyn_cb_sell_thr = np.percentile(hist_cb, 90)
        
        current_nupl = d['nupl_raw']
        current_cb = d['cb_raw']
        
        if current_nupl is None or current_cb is None:
            continue
            
        buy_cond = (current_nupl <= dyn_nupl_buy_thr) and (current_cb <= dyn_cb_buy_thr)
        sell_cond = (current_nupl >= dyn_nupl_sell_thr) and (current_cb >= dyn_cb_sell_thr)
        
        if last_action == 'SELL' and buy_cond:
            btc = cash / price
            cash = 0.0
            last_action = 'BUY'
            trades.append(('BUY', d['date'], price, round(current_nupl, 3), round(current_cb, 2), round(dyn_nupl_buy_thr, 3), round(dyn_cb_buy_thr, 2)))
        elif last_action == 'BUY' and sell_cond:
            cash = btc * price
            btc = 0.0
            last_action = 'SELL'
            trades.append(('SELL', d['date'], price, round(current_nupl, 3), round(current_cb, 2), round(dyn_nupl_sell_thr, 3), round(dyn_cb_sell_thr, 2)))
            
    final_val = cash + btc * daily_metrics[-1]['price']
    roi = (final_val - initial_cash) / initial_cash * 100
    
    print(f"\nResults for Cumulative History Strategy:")
    print(f"  Final Value: ${final_val:,.2f} (ROI: {roi:.1f}%)")
    print(f"  Max Drawdown: {max_dd*100:.1f}%")
    print(f"  Total Trades: {len(trades)}")
    for t in trades:
        print(f"    {t[0]} on {t[1]} at ${t[2]:,} | NUPL: {t[3]} (thr: {t[5]}) | CB: {t[4]} (thr: {t[6]})")

if __name__ == '__main__':
    run_simulation()
