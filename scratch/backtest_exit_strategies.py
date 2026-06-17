import re
import numpy as np

# 1. Read data from ml_history.html
with open("/Users/max/BitcoinScore/web/ml_history.html", "r") as f:
    content = f.read()

# Extract arrays
dates_match = re.search(r'const dates\s*=\s*(\[.*?\]);', content, re.DOTALL)
prices_match = re.search(r'const prices\s*=\s*(\[.*?\]);', content, re.DOTALL)
ml_match = re.search(r'const ml\s*=\s*(\[.*?\]);', content, re.DOTALL)

dates = eval(dates_match.group(1).replace("'", '"'))
prices = eval(prices_match.group(1))
ml = eval(ml_match.group(1))

# Check alignment
n = len(dates)
assert len(prices) == n and len(ml) == n, "Arrays have different lengths!"

print(f"Loaded {n} daily records from {dates[0]} to {dates[-1]}.\n")

# --- BASELINE: Buy <= 25, Sell >= 60 ---
def run_baseline():
    capital = 1000.0
    in_position = False
    btc_balance = 0.0
    trades = []
    buy_price = 0.0
    buy_date = ""

    for i in range(n):
        price = prices[i]
        score = ml[i]
        date = dates[i]

        if not in_position and score <= 25:
            btc_balance = capital / price
            capital = 0.0
            in_position = True
            buy_price = price
            buy_date = date
        elif in_position and score >= 60:
            capital = btc_balance * price
            btc_balance = 0.0
            in_position = False
            ret = (price - buy_price) / buy_price * 100
            trades.append((buy_date, buy_price, date, price, ret, capital))
            
    # If still in position at the end, close it at final price for comparison
    if in_position:
        capital = btc_balance * prices[-1]
        ret = (prices[-1] - buy_price) / buy_price * 100
        trades.append((buy_date, buy_price, dates[-1], prices[-1], ret, capital))

    return capital, trades

# --- TRAILING STOP ON PRICE ---
# Once ML score >= 60, we don't sell. Instead, we initiate a trailing stop of trailing_pct (e.g., 0.10 for 10%).
# The stop price tracks peak_price * (1 - trailing_pct).
def run_price_trailing_stop(trailing_pct):
    capital = 1000.0
    in_position = False
    btc_balance = 0.0
    trades = []
    buy_price = 0.0
    buy_date = ""
    
    # Trailing state
    trailing_active = False
    peak_price = 0.0
    stop_price = 0.0

    for i in range(n):
        price = prices[i]
        score = ml[i]
        date = dates[i]

        if not in_position:
            if score <= 25:
                btc_balance = capital / price
                capital = 0.0
                in_position = True
                buy_price = price
                buy_date = date
                trailing_active = False
        else:
            # We are in position
            if score >= 60 and not trailing_active:
                trailing_active = True
                peak_price = price
                stop_price = peak_price * (1 - trailing_pct)

            if trailing_active:
                # Update peak and stop prices
                if price > peak_price:
                    peak_price = price
                    stop_price = peak_price * (1 - trailing_pct)
                
                # Check if hit stop
                if price <= stop_price:
                    capital = btc_balance * price
                    btc_balance = 0.0
                    in_position = False
                    trailing_active = False
                    ret = (price - buy_price) / buy_price * 100
                    trades.append((buy_date, buy_price, date, price, ret, capital))
            else:
                # In position but score hasn't hit 60 yet, do nothing
                pass

    if in_position:
        capital = btc_balance * prices[-1]
        ret = (prices[-1] - buy_price) / buy_price * 100
        trades.append((buy_date, buy_price, dates[-1], prices[-1], ret, capital))

    return capital, trades

# --- TRAILING STOP ON ML SCORE ---
# Once ML score >= 60, we wait until the ML score peaks and falls by drop_points from its peak.
def run_score_trailing_stop(drop_points):
    capital = 1000.0
    in_position = False
    btc_balance = 0.0
    trades = []
    buy_price = 0.0
    buy_date = ""
    
    # Trailing state
    trailing_active = False
    peak_score = 0.0

    for i in range(n):
        price = prices[i]
        score = ml[i]
        date = dates[i]

        if not in_position:
            if score <= 25:
                btc_balance = capital / price
                capital = 0.0
                in_position = True
                buy_price = price
                buy_date = date
                trailing_active = False
        else:
            if score >= 60 and not trailing_active:
                trailing_active = True
                peak_score = score

            if trailing_active:
                if score > peak_score:
                    peak_score = score
                
                # If score falls by drop_points from peak
                if score <= (peak_score - drop_points):
                    capital = btc_balance * price
                    btc_balance = 0.0
                    in_position = False
                    trailing_active = False
                    ret = (price - buy_price) / buy_price * 100
                    trades.append((buy_date, buy_price, date, price, ret, capital))
            else:
                pass

    if in_position:
        capital = btc_balance * prices[-1]
        ret = (prices[-1] - buy_price) / buy_price * 100
        trades.append((buy_date, buy_price, dates[-1], prices[-1], ret, capital))

    return capital, trades

# --- RUN BACKTESTS ---
baseline_cap, baseline_trades = run_baseline()
print(f"Baseline (Sell immediately at ML >= 60):")
print(f"  Final Capital: ${baseline_cap:,.2f}")
print(f"  Number of Trades: {len(baseline_trades)}")
for t in baseline_trades:
    print(f"    Buy {t[0]} (${t[1]:,}) -> Sell {t[2]} (${t[3]:,}) | {t[4]:+.1f}% | Balance: ${t[5]:,.2f}")
print("-" * 50)

for pct in [0.05, 0.10, 0.15, 0.20, 0.25]:
    cap, trades = run_price_trailing_stop(pct)
    print(f"Price Trailing Stop ({pct*100:.0f}% trailing threshold):")
    print(f"  Final Capital: ${cap:,.2f}")
    print(f"  Number of Trades: {len(trades)}")
    for t in trades:
        print(f"    Buy {t[0]} (${t[1]:,}) -> Sell {t[2]} (${t[3]:,}) | {t[4]:+.1f}% | Balance: ${t[5]:,.2f}")
    print("-" * 50)

for pts in [5, 10, 15, 20]:
    cap, trades = run_score_trailing_stop(pts)
    print(f"Score Trailing Stop (drops by {pts} points from peak):")
    print(f"  Final Capital: ${cap:,.2f}")
    print(f"  Number of Trades: {len(trades)}")
    for t in trades:
        print(f"    Buy {t[0]} (${t[1]:,}) -> Sell {t[2]} (${t[3]:,}) | {t[4]:+.1f}% | Balance: ${t[5]:,.2f}")
    print("-" * 50)
