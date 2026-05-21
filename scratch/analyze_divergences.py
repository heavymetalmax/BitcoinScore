import json
import sys
import os
import datetime

sys.path.insert(0, '.')
from scraper.cipherb import get_cipherb, _bearish_divergence, _bullish_divergence, _fast_divergence

def main():
    res = get_cipherb('BTCUSDT')
    series = res['series']
    
    print("Analyzing Cipher B Divergences on Weekly Chart...")
    print("-" * 80)
    
    std_bear = []
    std_bull = []
    fast_bear = []
    fast_bull = []
    
    for i in range(50, len(series) + 1):
        sub_series = series[:i]
        candle = sub_series[-1]
        dt = datetime.datetime.utcfromtimestamp(candle['timestamp']).date()
        close = candle['close']
        
        # Check standard
        if _bearish_divergence(sub_series):
            std_bear.append((dt, close))
        if _bullish_divergence(sub_series):
            std_bull.append((dt, close))
            
        # Check fast
        if _fast_divergence(sub_series, kind='bearish'):
            fast_bear.append((dt, close))
        if _fast_divergence(sub_series, kind='bullish'):
            fast_bull.append((dt, close))
            
    print("\n[STANDARD BEARISH DIVERGENCES] (peak_window=4, max_peak_age=6)")
    for dt, close in std_bear:
        print(f"  {dt} | Price: ${close:,.0f}")
        
    print("\n[STANDARD BULLISH DIVERGENCES] (peak_window=4, max_peak_age=6)")
    for dt, close in std_bull:
        print(f"  {dt} | Price: ${close:,.0f}")
        
    print("\n[FAST BEARISH DIVERGENCES] (peak_window=1, max_peak_age=3)")
    for dt, close in fast_bear:
        print(f"  {dt} | Price: ${close:,.0f}")
        
    print("\n[FAST BULLISH DIVERGENCES] (peak_window=1, max_peak_age=3)")
    for dt, close in fast_bull:
        print(f"  {dt} | Price: ${close:,.0f}")

if __name__ == '__main__':
    main()
