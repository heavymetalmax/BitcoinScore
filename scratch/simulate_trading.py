import sys
import os
import json
import datetime

sys.path.insert(0, '.')

from tools.backtest import load_data, compute_at, _prev_scores, v3_at

def run_simulation():
    print("Loading historical data...")
    series = load_data()
    
    # Load all dates from scores.json to have prices and order
    with open('data/history/scores.json', 'r', encoding='utf-8') as f:
        scores_history = json.load(f)
    
    # Sort chronologically
    scores_history.sort(key=lambda x: x['date'])
    
    dates = [x['date'] for x in scores_history]
    prices = {x['date']: x['btc_price'] for x in scores_history}
    
    print(f"Total days to evaluate: {len(dates)}")
    
    # Pre-calculate V1 and V3.3 scores daily to avoid recalculating inside the strategy loop
    print("Evaluating daily scores (V1 and V3.3)...")
    daily_data = []
    
    # To speed up, we can do it daily
    for i, date_str in enumerate(dates):
        if i % 200 == 0:
            print(f"  Processed {i}/{len(dates)} days...")
        td = datetime.date.fromisoformat(date_str)
        price = prices[date_str]
        
        # V1 score
        r = compute_at(td, series)
        v1_score = r['final']
        
        # V3.3 score
        try:
            v3_res = v3_at(td, series)
            v3_score = v3_res.get('final_score')
        except Exception:
            v3_score = None
            
        daily_data.append({
            'date': date_str,
            'price': price,
            'v1': v1_score,
            'v3': v3_score
        })
        
    print("Recalculation complete. Running trading simulations...")
    
    # Save cache so we can rerun fast if needed
    with open('scratch/daily_scores_cache.json', 'w') as f:
        json.dump(daily_data, f, indent=2)
        
    run_strategies(daily_data)

def run_strategies(daily_data):
    # Strategy 1: Buy <= 30, Sell >= 70
    # Strategy 2: Buy <= 35, Sell >= 65 (better for V3.3 which has lower peaks)
    # Strategy 3: Buy <= 25, Sell >= 75 (better for V1)
    
    configs = [
        {'name': 'V1 (Buy <= 30, Sell >= 70)', 'score_key': 'v1', 'buy_thr': 30, 'sell_thr': 70},
        {'name': 'V1 (Buy <= 25, Sell >= 75)', 'score_key': 'v1', 'buy_thr': 25, 'sell_thr': 75},
        {'name': 'V3.3 (Buy <= 30, Sell >= 70)', 'score_key': 'v3', 'buy_thr': 30, 'sell_thr': 70},
        {'name': 'V3.3 (Buy <= 35, Sell >= 65)', 'score_key': 'v3', 'buy_thr': 35, 'sell_thr': 65},
        {'name': 'V3.3 (Buy <= 30, Sell >= 65)', 'score_key': 'v3', 'buy_thr': 30, 'sell_thr': 65},
    ]
    
    initial_cash = 10000.0
    
    for cfg in configs:
        name = cfg['name']
        key = cfg['score_key']
        buy_thr = cfg['buy_thr']
        sell_thr = cfg['sell_thr']
        
        cash = initial_cash
        btc = 0.0
        trades = []
        
        # To calculate max drawdown of portfolio value
        peak = initial_cash
        max_dd = 0.0
        
        in_buy_zone = False
        
        for day in daily_data:
            score = day[key]
            price = day['price']
            if score is None:
                continue
                
            # Current portfolio value
            port_val = cash + btc * price
            if port_val > peak:
                peak = port_val
            dd = (peak - port_val) / peak
            if dd > max_dd:
                max_dd = dd
                
            # Buy logic: we buy if score <= buy_thr and we have cash
            if score <= buy_thr and cash > 10:
                btc_bought = cash / price
                btc += btc_bought
                cash = 0.0
                trades.append(('BUY', day['date'], price, score))
                
            # Sell logic: we sell if score >= sell_thr and we have BTC
            elif score >= sell_thr and btc > 0:
                cash_received = btc * price
                cash += cash_received
                btc = 0.0
                trades.append(('SELL', day['date'], price, score))
                
        final_val = cash + btc * daily_data[-1]['price']
        roi = (final_val - initial_cash) / initial_cash * 100
        
        print(f"\nResults for {name}:")
        print(f"  Final Value: ${final_val:,.2f} (ROI: {roi:.1f}%)")
        print(f"  Max Drawdown: {max_dd*100:.1f}%")
        print(f"  Total Trades: {len(trades)}")
        for t in trades[:10]:
            print(f"    {t[0]} on {t[1]} at ${t[2]:,} (score: {t[3]})")
        if len(trades) > 10:
            print(f"    ... and {len(trades)-10} more trades ...")
            # print the last trade
            last_t = trades[-1]
            print(f"    {last_t[0]} on {last_t[1]} at ${last_t[2]:,} (score: {last_t[3]})")

if __name__ == '__main__':
    if os.path.exists('scratch/daily_scores_cache.json'):
        print("Loading cached daily scores...")
        with open('scratch/daily_scores_cache.json', 'r') as f:
            daily_data = json.load(f)
        run_strategies(daily_data)
    else:
        run_simulation()
