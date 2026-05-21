import json
import sys
import os
import datetime

sys.path.insert(0, '.')
from scraper.cipherb import fetch_ohlcv_kraken, sma
from tools.backtest_fast import MILESTONES

def main():
    print("Fetching Kraken weekly candles...")
    ohlcv = fetch_ohlcv_kraken(symbol='BTCUSDT', interval='1w', limit=720)
    
    # Calculate MFI
    mfi_raw = []
    for c in ohlcv:
        denom = c['high'] - c['low']
        if denom == 0:
            mfi_raw.append(0.0)
        else:
            mfi_raw.append(((c['close'] - c['open']) / denom) * 150.0)
            
    mfi = sma(mfi_raw, 60)
    
    # Pair with timestamps
    mfi_by_date = {}
    for i, c in enumerate(ohlcv):
        if mfi[i] is not None:
            d = datetime.datetime.utcfromtimestamp(c['timestamp']).date()
            mfi_by_date[d] = mfi[i] - 2.5  # subtract offset of 2.5

    def get_closest_mfi(target_date):
        best_diff = None
        best_val = None
        for d, val in mfi_by_date.items():
            diff = abs((d - target_date).days)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_val = val
        return best_val

    print("\nMFI Values at historical milestones:")
    print("-" * 60)
    print(f"{'Date':<12} {'Label':<20} {'Price':>10} | {'MFI':>8}")
    print("-" * 60)
    for date_str, label, price, _, _ in MILESTONES:
        td = datetime.date.fromisoformat(date_str)
        val = get_closest_mfi(td)
        val_str = f"{val:+.1f}" if val is not None else "N/A"
        print(f"{date_str:<12} {label:<20} ${price:>9,} | {val_str:>8}")

if __name__ == '__main__':
    main()
