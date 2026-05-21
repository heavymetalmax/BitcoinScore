import json
import sys
import os
import datetime
from datetime import timedelta

sys.path.insert(0, '.')
from scraper.cipherb import fetch_ohlcv_kraken, compute_cipherb_from_ohlcv, _bearish_divergence, _bullish_divergence, _fast_divergence
from tools.backtest_fast import MILESTONES

def main():
    # Fetch a large number of weekly candles from Kraken to get historical coverage
    print("Fetching Kraken weekly candles...")
    ohlcv = fetch_ohlcv_kraken(symbol='BTCUSDT', interval='1w', limit=720)
    print(f"Fetched {len(ohlcv)} candles.")
    
    # Recompute cipherb series
    res = compute_cipherb_from_ohlcv(ohlcv)
    series = res['series']
    
    # We will map timestamps to dates
    series_by_date = {}
    for item in series:
        d = datetime.datetime.utcfromtimestamp(item['timestamp']).date()
        series_by_date[d] = item

    # Helper to get the closest item
    def get_closest_item(target_date):
        best_diff = None
        best_item = None
        for d, item in series_by_date.items():
            diff = abs((d - target_date).days)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_item = item
        return best_item

    print("\nComparing Cipher B scoring versions at historical milestones:")
    print("-" * 140)
    print(f"{'Milestone Date':<12} {'Label':<20} {'BTC Price':>10} | {'Base Score':>10} | {'Std Div':>8} {'Std Score':>10} | {'Fast Div':>8} {'Fast Score':>10}")
    print("-" * 140)
    
    for date_str, label, btc_price, _, _ in MILESTONES:
        td = datetime.date.fromisoformat(date_str)
        item = get_closest_item(td)
        if item is None:
            continue
            
        # Reconstruct the sub-series up to this item's timestamp to simulate real-time detection
        idx = next(i for i, x in enumerate(series) if x['timestamp'] == item['timestamp'])
        sub_series = series[:idx+1]
        
        # Calculate divergences at this historical point
        std_bear = _bearish_divergence(sub_series)
        std_bull = _bullish_divergence(sub_series)
        fast_bear = _fast_divergence(sub_series, kind='bearish')
        fast_bull = _fast_divergence(sub_series, kind='bullish')
        
        base_score = item['weekly_score']
        if base_score is None:
            continue
            
        # Std Score
        std_score = base_score
        std_div_str = "None"
        if std_bear:
            std_score = min(100, std_score + 12)
            std_div_str = "Bear"
        elif std_bull:
            std_score = max(0, std_score - 12)
            std_div_str = "Bull"
            
        # Fast Score
        fast_score = base_score
        fast_div_str = "None"
        if fast_bear:
            fast_score = min(100, fast_score + 12)
            fast_div_str = "Bear"
        elif fast_bull:
            fast_score = max(0, fast_score - 12)
            fast_div_str = "Bull"
            
        print(f"{date_str:<12} {label:<20} ${btc_price:>9,} | {base_score:10.1f} | {std_div_str:>8} {std_score:10.1f} | {fast_div_str:>8} {fast_score:10.1f}")
        
if __name__ == '__main__':
    main()
