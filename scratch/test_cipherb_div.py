import json
import sys
import os

sys.path.insert(0, '.')
from scraper.cipherb import get_cipherb

def main():
    res = get_cipherb('BTCUSDT')
    series = res['series']
    
    total = len(series)
    valid_count = len([x for x in series if x['wt1'] is not None])
    
    # We can compute divergences manually for each week to see how many were active
    from scraper.cipherb import _bearish_divergence, _bullish_divergence, _fast_divergence
    
    bearish_count = 0
    bullish_count = 0
    fast_bear_count = 0
    fast_bull_count = 0
    
    for i in range(50, len(series) + 1):
        sub_series = series[:i]
        if _bearish_divergence(sub_series):
            bearish_count += 1
        if _bullish_divergence(sub_series):
            bullish_count += 1
        if _fast_divergence(sub_series, kind='bearish'):
            fast_bear_count += 1
        if _fast_divergence(sub_series, kind='bullish'):
            fast_bull_count += 1
            
    print(f"Total weeks in history: {total}")
    print(f"Valid weeks: {valid_count}")
    print(f"Standard Bearish Divergences active weeks: {bearish_count}")
    print(f"Standard Bullish Divergences active weeks: {bullish_count}")
    print(f"Fast Bearish Divergences active weeks: {fast_bear_count}")
    print(f"Fast Bullish Divergences active weeks: {fast_bull_count}")

if __name__ == '__main__':
    main()
