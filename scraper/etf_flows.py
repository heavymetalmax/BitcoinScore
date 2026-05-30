"""Scraper for US Spot Bitcoin ETF net flows.

Fetches data from Farside Investors (https://farside.co.uk/bitcoin-etf-flow-all-data/).
"""
import logging
import io
import pandas as pd
from curl_cffi import requests

logger = logging.getLogger(__name__)

_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"

def parse_financial_value(val):
    if pd.isna(val):
        return 0.0
    val_str = str(val).strip()
    if not val_str or val_str in ('-', '—', '.'):
        return 0.0
    
    is_negative = False
    if val_str.startswith('(') and val_str.endswith(')'):
        is_negative = True
        val_str = val_str[1:-1]
        
    val_str = val_str.replace(',', '').replace(' ', '')
    try:
        num = float(val_str)
        return -num if is_negative else num
    except ValueError:
        return 0.0

def get_etf_flows():
    """Fetch daily ETF flows and return the latest metrics.
    
    Returns:
        dict: {
            'value': float (14d rolling sum of net flows in $M),
            'daily_flow': float (latest daily total flow in $M),
            'total_cumulative': float (total cumulative flows in $M),
            'date': str (latest data date YYYY-MM-DD)
        } or None
    """
    logger.info("Fetching ETF flows from Farside...")
    try:
        response = requests.get(_URL, impersonate="chrome", timeout=20)
        if response.status_code != 200:
            logger.error(f"Failed to fetch Farside ETF data. Status: {response.status_code}")
            return None
            
        dfs = pd.read_html(io.StringIO(response.text))
        if len(dfs) < 2:
            logger.error("Could not find ETF flows table in Farside HTML.")
            return None
            
        df = dfs[1]
        
        # Clean rows
        if df.iloc[0].isna().all() or df.iloc[0, 0] == 'Date':
            df = df.iloc[1:].reset_index(drop=True)
            
        # Clean columns
        expected_cols = ['Date', 'IBIT', 'FBTC', 'BITB', 'ARKB', 'BTCO', 'EZBC', 'BRRR', 'HODL', 'BTCW', 'MSBT', 'GBTC', 'BTC', 'Total']
        if len(df.columns) >= len(expected_cols):
            df = df.iloc[:, :len(expected_cols)]
            df.columns = expected_cols
        else:
            logger.warning(f"Farside columns do not match expected format: {list(df.columns)}")
            return None
            
        # Filter Date column
        df = df[df['Date'].notna()]
        df['Date'] = df['Date'].astype(str).str.strip()
        df = df[~df['Date'].str.lower().isin(['total', 'average', 'maximum', 'minimum'])]
        
        # Convert numeric values
        for col in expected_cols[1:]:
            df[col] = df[col].apply(parse_financial_value)
            
        # Parse Dates
        df['Datetime'] = pd.to_datetime(df['Date'], format='%d %b %Y', errors='coerce')
        df = df[df['Datetime'].notna()]
        df = df.sort_values('Datetime').reset_index(drop=True)
        
        if df.empty:
            logger.error("No valid rows after cleaning ETF flows.")
            return None
            
        # Compute 14-day rolling sum (based on actual trading calendar rows, or calendar days?)
        # For risk mapping, we calibrated on trading-day rows.
        # But wait, 14-day rolling sum on calendar days or trading days?
        # In analyze_etf_correlations.py, we merged with calendar days (filling non-trading days with 0).
        # To align with that calibration, we should merge with calendar daily price dates or just
        # reconstruct the calendar index and roll sum over 14 calendar days.
        # Let's do calendar daily reconstruction to match:
        df_cal = df.set_index('Datetime').resample('D').asfreq()
        # Fill non-trading days with 0
        df_cal['Total'] = df_cal['Total'].fillna(0.0)
        df_cal['Total_Cumulative'] = df_cal['Total'].cumsum()
        
        # Calculate 14-day rolling sum
        df_cal['Flow_14d_Sum'] = df_cal['Total'].rolling(window=14).sum()
        
        latest_row = df_cal[df_cal['Date'].notna()].iloc[-1]
        
        result = {
            'value': float(latest_row['Flow_14d_Sum']),
            'daily_flow': float(latest_row['Total']),
            'total_cumulative': float(latest_row['Total_Cumulative']),
            'date': latest_row.name.strftime('%Y-%m-%d')
        }
        
        logger.info(f"Latest ETF flow data for {result['date']}: 14d={result['value']:.1f}M  daily={result['daily_flow']:.1f}M")
        return result
        
    except Exception as e:
        logger.error(f"Error scraping ETF flows: {e}")
        return None

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print(get_etf_flows())
