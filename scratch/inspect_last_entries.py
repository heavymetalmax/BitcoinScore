import re
import json

with open("/Users/max/BitcoinScore/web/ml_history.html", "r") as f:
    content = f.read()

# Extract arrays using regex
dates_match = re.search(r'const dates\s*=\s*(\[.*?\]);', content, re.DOTALL)
prices_match = re.search(r'const prices\s*=\s*(\[.*?\]);', content, re.DOTALL)
ml_match = re.search(r'const ml\s*=\s*(\[.*?\]);', content, re.DOTALL)

if dates_match and prices_match and ml_match:
    # Use json.loads to parse if possible, or eval (since it's a JS array representation)
    # Replacing single quotes/double quotes and handling trailing commas
    dates_str = dates_match.group(1).replace("'", '"')
    prices_str = prices_str = prices_match.group(1)
    ml_str = ml_match.group(1)
    
    # Simple parse
    dates = eval(dates_str)
    prices = eval(prices_str)
    ml = eval(ml_str)
    
    print(f"Total entries: {len(dates)}")
    print("Last 10 entries:")
    for i in range(-10, 0):
        print(f"Date: {dates[i]} | Price: ${prices[i]:,} | ML Score: {ml[i]}")
else:
    print("Could not find arrays in the HTML file")
