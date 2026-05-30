"""Scraper for aSOPR (Adjusted Spent Output Profit Ratio) metric."""
import logging
from .mm_utils import get_bmp_trace_last_n

logger = logging.getLogger(__name__)

_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/sopr-spent-output-profit-ratio/'

def calculate_7d_sma(history_array):
    if not history_array:
        return None
    if len(history_array) < 7:
        return history_array[-1]
    last_7_days = history_array[-7:]
    return sum(last_7_days) / 7.0

def get_asopr():
    """Return 7-day SMA of aSOPR."""
    # We fetch the trace. Often named 'adjusted sopr' or 'asopr' or just 'sopr'.
    # I'll use 'adjusted sopr' based on typical naming, or 'sopr'.
    # Let's try 'adjusted' first.
    history = get_bmp_trace_last_n(_BMP_URL, 'adjusted', n=7)
    if not history:
        # fallback to 'sopr'
        history = get_bmp_trace_last_n(_BMP_URL, 'sopr', n=7)
    
    if not history:
        return None
        
    # BMP's SOPR trace often returns (SOPR - 1) so it centers at 0.
    # If the average is close to 0 (e.g. between -0.5 and 0.5), we add 1.0.
    avg = sum(h for h in history if h is not None) / len([h for h in history if h is not None])
    if abs(avg) < 0.5:
        history = [(h + 1.0 if h is not None else None) for h in history]
        
    valid_history = [h for h in history if h is not None]
    if not valid_history:
        return None
        
    return calculate_7d_sma(valid_history)

__all__ = ['get_asopr']
