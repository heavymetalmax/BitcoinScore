import re

# Read data from ml_history.html
with open("/Users/max/BitcoinScore/web/ml_history.html", "r") as f:
    content = f.read()

dates = eval(re.search(r'const dates\s*=\s*(\[.*?\]);', content, re.DOTALL).group(1).replace("'", '"'))
prices = eval(re.search(r'const prices\s*=\s*(\[.*?\]);', content, re.DOTALL).group(1))
ml = eval(re.search(r'const ml\s*=\s*(\[.*?\]);', content, re.DOTALL).group(1))

n = len(dates)

def run_backtest(use_trailing_buy=False, buy_bounce_pts=5):
    capital = 1000.0
    in_position = False
    btc_balance = 0.0
    trades = []
    
    # State variables
    state = "CASH"  # CASH | TRAILING_BUY | HOLD_BTC | TRAILING_EXIT
    buy_price = 0.0
    buy_date = ""
    
    # Trailing buy state
    min_score = 100
    
    # Trailing exit state
    peak_score = 0

    for i in range(n):
        price = prices[i]
        score = ml[i]
        date = dates[i]

        if state == "CASH":
            if score <= 25:
                if use_trailing_buy:
                    state = "TRAILING_BUY"
                    min_score = score
                else:
                    state = "HOLD_BTC"
                    btc_balance = capital / price
                    capital = 0.0
                    buy_price = price
                    buy_date = date
        
        elif state == "TRAILING_BUY":
            if score < min_score:
                min_score = score
            
            # If score bounces back up by buy_bounce_pts points
            if score >= (min_score + buy_bounce_pts):
                state = "HOLD_BTC"
                btc_balance = capital / price
                capital = 0.0
                buy_price = price
                buy_date = date

        elif state == "HOLD_BTC":
            if score >= 60:
                state = "TRAILING_EXIT"
                peak_score = score
        
        elif state == "TRAILING_EXIT":
            if score > peak_score:
                peak_score = score
            
            # Trailing exit trigger (drop by 5 points)
            if score <= (peak_score - 5):
                capital = btc_balance * price
                btc_balance = 0.0
                state = "CASH"
                ret = (price - buy_price) / buy_price * 100
                trades.append((buy_date, buy_price, date, price, ret, capital))

    if state != "CASH":
        final_val = (btc_balance * prices[-1]) if btc_balance > 0 else capital
        trades.append((buy_date, buy_price, dates[-1], prices[-1], (prices[-1]-buy_price)/buy_price*100, final_val))
        capital = final_val

    return capital, trades

print("=== BASELINE (Exit on 5-pt score drop, Buy immediately at <= 25) ===")
cap_base, trades_base = run_backtest(use_trailing_buy=False)
print(f"Final Capital: ${cap_base:,.2f}")
for t in trades_base:
    print(f"  Buy {t[0]} (${t[1]:,}) -> Sell {t[2]} (${t[3]:,}) | {t[4]:+.1f}% | Balance: ${t[5]:,.2f}")

print("\n=== TRAILING BUY (+5 pt bounce from min score) ===")
cap_trail, trades_trail = run_backtest(use_trailing_buy=True, buy_bounce_pts=5)
print(f"Final Capital: ${cap_trail:,.2f}")
for t in trades_trail:
    print(f"  Buy {t[0]} (${t[1]:,}) -> Sell {t[2]} (${t[3]:,}) | {t[4]:+.1f}% | Balance: ${t[5]:,.2f}")
