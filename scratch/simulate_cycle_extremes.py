import sys
import os
import json
import datetime

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
    
    print("Extracting daily raw/normalized metrics for simulation...")
    for i, date_str in enumerate(dates):
        if i % 500 == 0:
            print(f"  Processed {i}/{len(dates)} days...")
        td = datetime.date.fromisoformat(date_str)
        r = compute_at(td, series)
        
        # We want: cipherb (normalized weekly/daily), nupl (normalized or raw)
        # Note: r['scores'] has normalized values
        sc = r['scores']
        
        daily_metrics.append({
            'date': date_str,
            'price': prices[date_str],
            'final_score': r['final'],
            'nupl': sc.get('nupl'),
            'cipherb': sc.get('cipherb'),
            'mvrv': sc.get('mvrv_z_score'),
        })
        
    # Strategy 1: Simple oversold/overbought thresholds on NUPL + CipherB
    # Buy when NUPL <= 20 AND CipherB <= 10
    # Sell when NUPL >= 80 OR CipherB >= 90
    
    # Let's test different thresholds
    configs = [
        {
            'name': 'Extreme Oversold (Buy: NUPL <= 20 & CB <= 10 | Sell: NUPL >= 80 & CB >= 90)',
            'buy_cond': lambda d: d['nupl'] is not None and d['nupl'] <= 20 and d['cipherb'] is not None and d['cipherb'] <= 10,
            'sell_cond': lambda d: d['nupl'] is not None and d['nupl'] >= 80 and d['cipherb'] is not None and d['cipherb'] >= 90,
        },
        {
            'name': 'Oversold Cross-Up (Buy: NUPL <= 25 & CB <= 15 | Sell: NUPL >= 75 & CB >= 85)',
            'buy_cond': lambda d: d['nupl'] is not None and d['nupl'] <= 25 and d['cipherb'] is not None and d['cipherb'] <= 15,
            'sell_cond': lambda d: d['nupl'] is not None and d['nupl'] >= 75 and d['cipherb'] is not None and d['cipherb'] >= 85,
        },
        {
            'name': 'Cyclical Bottoms & Peaks (Buy: NUPL <= 20 & CB <= 5 | Sell: NUPL >= 75 & CB >= 95)',
            'buy_cond': lambda d: d['nupl'] is not None and d['nupl'] <= 20 and d['cipherb'] is not None and d['cipherb'] <= 5,
            'sell_cond': lambda d: d['nupl'] is not None and d['nupl'] >= 75 and d['cipherb'] is not None and d['cipherb'] >= 95,
        }
    ]
    
    initial_cash = 10000.0
    
    for cfg in configs:
        name = cfg['name']
        buy_cond = cfg['buy_cond']
        sell_cond = cfg['sell_cond']
        
        cash = initial_cash
        btc = 0.0
        trades = []
        peak = initial_cash
        max_dd = 0.0
        
        # We need a cooldown or state to avoid buying immediately again on the next day if we are still in the zone
        last_action = 'SELL'
        
        for d in daily_metrics:
            price = d['price']
            port_val = cash + btc * price
            if port_val > peak:
                peak = port_val
            dd = (peak - port_val) / peak
            if dd > max_dd:
                max_dd = dd
                
            if last_action == 'SELL' and buy_cond(d):
                btc = cash / price
                cash = 0.0
                last_action = 'BUY'
                trades.append(('BUY', d['date'], price, d['nupl'], d['cipherb']))
            elif last_action == 'BUY' and sell_cond(d):
                cash = btc * price
                btc = 0.0
                last_action = 'SELL'
                trades.append(('SELL', d['date'], price, d['nupl'], d['cipherb']))
                
        final_val = cash + btc * daily_metrics[-1]['price']
        roi = (final_val - initial_cash) / initial_cash * 100
        
        print(f"\nResults for {name}:")
        print(f"  Final Value: ${final_val:,.2f} (ROI: {roi:.1f}%)")
        print(f"  Max Drawdown: {max_dd*100:.1f}%")
        print(f"  Total Trades: {len(trades)}")
        for t in trades:
            print(f"    {t[0]} on {t[1]} at ${t[2]:,} (NUPL: {t[3]}, CB: {t[4]})")

if __name__ == '__main__':
    run_simulation()
