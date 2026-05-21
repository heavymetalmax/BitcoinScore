import requests
import datetime
import time

def fetch_all_daily_prices():
    # Start from Jan 1, 2018 (or slightly earlier to have 200 days before Dec 15, 2018)
    # 200 days before Dec 15, 2018 is around May 29, 2018.
    # Binance launched BTCUSDT in Aug 2017, so starting from Jan 1, 2018 is perfect.
    start_dt = datetime.datetime(2018, 1, 1)
    start_ts = int(start_dt.timestamp() * 1000)
    
    url = "https://api.binance.com/api/v3/klines"
    prices = []
    
    current_ts = start_ts
    while True:
        params = {
            "symbol": "BTCUSDT",
            "interval": "1d",
            "startTime": current_ts,
            "limit": 1000
        }
        res = requests.get(url, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()
        if not data:
            break
        
        # Parse data
        for row in data:
            open_time = row[0]
            close_price = float(row[4])
            dt = datetime.datetime.fromtimestamp(open_time / 1000).date()
            prices.append((dt, close_price))
        
        # Get next start time (1 ms after the last candle's open time)
        last_ts = data[-1][0]
        if last_ts == current_ts:
            break
        current_ts = last_ts + 86400000  # advance by 1 day
        
        # Stop if we reached close to today
        if current_ts > int(time.time() * 1000):
            break
        time.sleep(0.1)
    
    # Sort and remove duplicates just in case
    prices = sorted(list(set(prices)), key=lambda x: x[0])
    return prices

def calculate_mayer_multiples(prices):
    results = {}
    for i in range(len(prices)):
        dt, price = prices[i]
        if i < 199:
            continue
        # 200DMA is the average of the last 200 days (including the current day)
        window = [p[1] for p in prices[i-199:i+1]]
        dma_200 = sum(window) / 200
        mm = price / dma_200
        
        # map score
        score_val = max(0.5, min(2.1, mm))
        score = round((score_val - 0.5) / 1.6 * 100)
        
        results[dt.strftime("%Y-%m-%d")] = {
            "price": price,
            "dma_200": dma_200,
            "mayer": mm,
            "score": score
        }
    return results

def main():
    print("Fetching daily prices from Binance...")
    prices = fetch_all_daily_prices()
    print(f"Fetched {len(prices)} daily closes from {prices[0][0]} to {prices[-1][0]}")
    
    mayer_data = calculate_mayer_multiples(prices)
    
    milestone_dates = [
        "2018-12-15",
        "2019-06-26",
        "2020-03-13",
        "2020-10-01",
        "2021-04-14",
        "2021-07-20",
        "2021-11-10",
        "2022-06-18",
        "2022-11-21",
        "2023-01-14",
        "2024-03-14",
        "2025-01-20",
        "2025-09-26",
        "2025-09-29",
        "2025-11-10",
        "2026-04-30"
    ]
    
    print("\nMilestone Results:")
    print(f"{'Date':<12} | {'Price':<10} | {'200DMA':<10} | {'Mayer':<8} | {'Score':<5}")
    print("-" * 55)
    for dt in milestone_dates:
        if dt in mayer_data:
            info = mayer_data[dt]
            print(f"{dt:<12} | ${info['price']:<9.1f} | ${info['dma_200']:<9.1f} | {info['mayer']:<8.3f} | {info['score']:<5}")
        else:
            # Fallback check if it's very close or if date is not matched exactly
            print(f"{dt:<12} | No data found")

if __name__ == "__main__":
    main()
