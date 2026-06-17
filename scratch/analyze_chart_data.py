import numpy as np

# Data from ml_history.html
buy_dates = ["2018-12-05", "2020-03-12", "2021-08-05", "2022-10-26", "2023-08-31", "2024-07-04"]
buy_prices = [3770, 4800, 40862, 20772, 25941, 57050]

sell_dates = ["2019-06-23", "2021-01-11", "2021-09-05", "2023-06-21", "2024-03-11", "2024-11-18"]
sell_prices = [10906, 35404, 51757, 29994, 72078, 90464]

print("--- STRATEGY TRADES ANALYSIS ---")
capital = 1000.0  # start with $1000
btc_balance = 0.0

for i in range(len(buy_prices)):
    b_date, b_price = buy_dates[i], buy_prices[i]
    s_date, s_price = sell_dates[i], sell_prices[i]
    
    # Buy all-in
    btc_bought = capital / b_price
    capital = 0.0
    
    # Sell all-in
    capital = btc_bought * s_price
    trade_return = (s_price - b_price) / b_price * 100
    print(f"Trade {i+1}: Buy {b_date} @ ${b_price:,} -> Sell {s_date} @ ${s_price:,} | Return: {trade_return:+.1f}% | Capital: ${capital:,.2f}")

print("\n--- PERFORMANCE COMPARISON ---")
# Buy & Hold from the first buy to last sell
bh_start_price = buy_prices[0]
bh_end_price = sell_prices[-1]
bh_return = (bh_end_price - bh_start_price) / bh_start_price * 100
strategy_return = (capital - 1000.0) / 1000.0 * 100

print(f"Start capital: $1,000")
print(f"End capital: ${capital:,.2f}")
print(f"Total strategy return: {strategy_return:+.1f}% (x{capital/1000:.1f})")
print(f"Buy & Hold return (from {buy_dates[0]} to {sell_dates[-1]}): {bh_return:+.1f}% (x{bh_end_price/bh_start_price:.1f})")
print(f"Outperformance: {strategy_return - bh_return:+.1f}%")
