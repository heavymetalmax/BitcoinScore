import json
import sys
import os

sys.path.insert(0, '.')
from scraper.cipherb import fetch_ohlcv_kraken, compute_cipherb_from_ohlcv

def main():
    print("Fetching Kraken weekly candles...")
    # Fetch 720 candles to get historical coverage back to 2012/2013 if available
    ohlcv = fetch_ohlcv_kraken(symbol='BTCUSDT', interval='1w', limit=720)
    print(f"Fetched {len(ohlcv)} candles. Computing Cipher B...")
    res = compute_cipherb_from_ohlcv(ohlcv)
    
    path = 'data/history/cipherb_btcusdt_1w.json'
    print(f"Writing to {path}...")
    with open(path, 'w') as f:
        json.dump(res, f, indent=2)
    print("Regeneration completed successfully.")

if __name__ == '__main__':
    main()
