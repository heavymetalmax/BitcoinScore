"""Scraper for Global M2 liquidity signal.

Primary Source: MacroMicro chart 3439 (World - M2 Money Supply of Major Central Banks, YoY in USD).
Fallback Source: FRED WM2NS (US M2 Money Stock, weekly, seasonally adjusted).
"""
import base64
import csv
import io
import json
import logging
import os
import re
import ssl
import urllib.request
import requests

logger = logging.getLogger(__name__)

_MACROMICRO_URL = 'https://en.macromicro.me/charts/3439/major-bank-m2-comparsion'
_FRED_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=WM2NS'
_ZYTE_API_URL = 'https://api.zyte.com/v1/extract'


def _fetch_wm2ns_series():
    """Fetch full WM2NS weekly series from FRED as fallback. Returns list of (date_str, float) sorted by date."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(_FRED_URL, headers={'User-Agent': 'curl/7.88'})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        text = r.read().decode()
    rows = list(csv.reader(io.StringIO(text)))
    series = [(r[0], float(r[1])) for r in rows[1:] if r[1] != '.']
    series.sort(key=lambda x: x[0])
    return series


def _fetch_fred_fallback():
    """Calculate US M2 YoY % change from FRED as fallback."""
    logger.info('get_m2: Attempting FRED fallback...')
    try:
        series = _fetch_wm2ns_series()
        if len(series) < 54:
            logger.error('get_m2: insufficient FRED data (%d points)', len(series))
            return None
        current_val = series[-1][1]
        past_val    = series[-53][1]   # ~52 weeks ago
        yoy = round((current_val - past_val) / past_val * 100, 2)
        logger.info('get_m2 fallback: US M2 YoY=%.2f%% (%s → %s)',
                    yoy, series[-53][0], series[-1][0])
        return yoy
    except Exception as e:
        logger.error('get_m2 fallback: FRED fetch failed: %s', e)
        return None


def get_m2():
    """Return M2 year-over-year % change.
    
    Tries MacroMicro Global M2 YoY in USD first. If it fails, falls back to US FRED M2.
    """
    api_key = os.getenv('ZYTE_API_KEY')
    if not api_key:
        logger.warning('get_m2: ZYTE_API_KEY not found in environment.')
        return _fetch_fred_fallback()

    try:
        payload = {
            "url": _MACROMICRO_URL,
            "httpResponseBody": True
        }
        logger.info('get_m2: Fetching MacroMicro chart 3439 via Zyte API...')
        response = requests.post(
            _ZYTE_API_URL,
            auth=(api_key, ''),
            json=payload,
            timeout=30
        )
        
        if response.status_code != 200:
            logger.warning('get_m2: Zyte API returned status %d: %s', response.status_code, response.text)
            return _fetch_fred_fallback()
            
        data = response.json()
        html_content = base64.b64decode(data['httpResponseBody']).decode('utf-8')
        
        # Regex to find let/var/const chart = {...};
        match = re.search(r'\b(?:let|var|const)\s+chart\s*=\s*(\{.*?\});', html_content, re.DOTALL)
        if not match:
            logger.warning('get_m2: Could not find chart object in MacroMicro HTML.')
            return _fetch_fred_fallback()
            
        chart_data = json.loads(match.group(1))
        last_rows_str = chart_data.get("series_last_rows")
        if not last_rows_str:
            logger.warning('get_m2: series_last_rows missing in chart object.')
            return _fetch_fred_fallback()
            
        last_rows = json.loads(last_rows_str)
        # Dynamically find the YoY series (it has smaller values, typically < 100,
        # whereas the absolute volume series is in the tens of thousands)
        yoy_series = None
        for series in last_rows:
            if not series:
                continue
            try:
                val = float(series[-1][1])
                if abs(val) < 100:
                    yoy_series = series
                    break
            except (ValueError, TypeError, IndexError):
                continue

        if not yoy_series:
            logger.warning('get_m2: YoY USD series missing or empty in series_last_rows.')
            return _fetch_fred_fallback()
            
        latest_point = yoy_series[-1]  # [date, value]
        latest_date = latest_point[0]
        latest_val = float(latest_point[1])
        
        logger.info('get_m2: Global M2 YoY in USD = %.2f%% (date: %s)', latest_val, latest_date)
        return round(latest_val, 2)
        
    except Exception as e:
        logger.warning('get_m2: MacroMicro fetch failed: %s', e)
        return _fetch_fred_fallback()


__all__ = ['get_m2']
