"""Scraper for US Spot Bitcoin ETF net flows.

Primary: CoinGlass (Playwright, 1-2 day lag).
Fallback: Farside Investors (HTML table, 9+ day lag).
"""
import logging
import io
import pandas as pd
from curl_cffi import requests

logger = logging.getLogger(__name__)

_FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
# Legacy alias kept for scraper.py staleness check
_URL = _FARSIDE_URL

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
    """Fetch daily ETF flows — CoinGlass primary, Farside fallback.

    Returns:
        dict: {
            'value': float (7d rolling sum of net flows in $M),
            'daily_flow': float (latest daily total flow in $M),
            'total_cumulative': float (total cumulative flows in $M),
            'date': str (latest data date YYYY-MM-DD)
        } or None
    """
    # Primary: CoinGlass (1-2 day lag, Playwright)
    try:
        from .etf_flows_coinglass import get_etf_flows_coinglass
        result = get_etf_flows_coinglass()
        if result:
            return result
        logger.warning('CoinGlass ETF returned None — falling back to Farside')
    except Exception as e:
        logger.warning('CoinGlass ETF failed (%s) — falling back to Farside', e)

    # Fallback: Farside (9+ day lag, HTML scrape)
    logger.info("Fetching ETF flows from Farside...")
    try:
        response = requests.get(_FARSIDE_URL, impersonate="chrome", timeout=20)
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
            
        df_cal = df.set_index('Datetime').resample('D').asfreq()
        df_cal['Total'] = df_cal['Total'].fillna(0.0)
        df_cal['Total_Cumulative'] = df_cal['Total'].cumsum()
        df_cal['Flow_7d_Sum'] = df_cal['Total'].rolling(window=7).sum()
        
        # Prefer last row with actual reported flows; fall back to last Farside row if all zero
        _reported = df_cal[(df_cal['Date'].notna()) & (df_cal['Total'] != 0.0)]
        latest_row = _reported.iloc[-1] if not _reported.empty else df_cal[df_cal['Date'].notna()].iloc[-1]
        
        result = {
            'value': float(latest_row['Flow_7d_Sum']),
            'daily_flow': float(latest_row['Total']),
            'total_cumulative': float(latest_row['Total_Cumulative']),
            'date': latest_row.name.strftime('%Y-%m-%d')
        }
        
        logger.info(f"Latest ETF flow data for {result['date']}: 7d={result['value']:.1f}M  daily={result['daily_flow']:.1f}M")
        return result
        
    except Exception as e:
        logger.error(f"Error scraping ETF flows: {e}")
        return None

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print(get_etf_flows())
