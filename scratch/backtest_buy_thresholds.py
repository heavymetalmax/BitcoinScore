import re

with open("/Users/max/BitcoinScore/web/ml_history.html", "r") as f:
    content = f.read()

dates = eval(re.search(r'const dates\s*=\s*(\[.*?\]);', content, re.DOTALL).group(1).replace("'", '"'))
prices = eval(re.search(r'const prices\s*=\s*(\[.*?\]);', content, re.DOTALL).group(1))
ml = eval(re.search(r'const ml\s*=\s*(\[.*?\]);', content, re.DOTALL).group(1))

n = len(dates)

def run_backtest_threshold(buy_threshold=25):
    capital = 1000.0
    in_position = False
    btc_balance = 0.0
    trades = []
    
    state = "CASH"  # CASH | HOLD_BTC | TRAILING_EXIT
    buy_price = 0.0
    buy_date = ""
    peak_score = 0

    for i in range(n):
        price = prices[i]
        score = ml[i]
        date = dates[i]

        if state == "CASH":
            if score <= buy_threshold:
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

for th in [25, 20, 15, 10]:
    cap, trades = run_backtest_threshold(th)
    print(f"Buy Threshold <= {th}: Final Capital = ${cap:,.2f} | Num Trades = {len(trades)}")
