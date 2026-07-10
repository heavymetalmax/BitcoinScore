"""ETF flows scraper using CoinGlass /etf/bitcoin page.

Primary: Zyte API browserHtml (renders JS, bypasses bot detection).
Fallback: direct Playwright scrape.

Returns the same dict format as etf_flows.get_etf_flows():
  {value, daily_flow, total_cumulative, date}
where value = 7-day rolling sum in $M, total_cumulative in $M.
"""
import base64
import datetime
import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

_URL = 'https://www.coinglass.com/etf/bitcoin'
_ZYTE_API_URL = 'https://api.zyte.com/v1/extract'
_TIMEOUT_MS = 35_000


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_usd_m(s: str) -> float:
    """Parse '+$51.63B', '-$95.30M', '+266.00M' → float in $M."""
    s = s.strip().replace(',', '').replace('−', '-').replace('​', '')
    sign = -1.0 if s.startswith('-') else 1.0
    s = s.lstrip('+-').lstrip('$')
    mult = 1.0
    if s.upper().endswith('B'):
        mult = 1000.0
        s = s[:-1]
    elif s.upper().endswith('M'):
        s = s[:-1]
    elif s.upper().endswith('K'):
        mult = 0.001
        s = s[:-1]
    try:
        return sign * float(s) * mult
    except ValueError:
        return 0.0


def _parse_date(s: str) -> str | None:
    """Parse 'Jul 10, 6:45 AM' / 'Jul 10, 2026 - 6:45 AM' → 'YYYY-MM-DD'."""
    s = s.strip()
    for fmt in ('%b %d, %Y %I:%M %p', '%b %d, %Y - %I:%M %p',
                '%b %d, %I:%M %p', '%b %d'):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            year = dt.year if dt.year != 1900 else datetime.date.today().year
            return f'{year}-{dt.month:02d}-{dt.day:02d}'
        except ValueError:
            continue
    m = re.search(r'([A-Za-z]{3})\s+(\d{1,2})', s)
    if m:
        try:
            dt = datetime.datetime.strptime(f'{m.group(1)} {m.group(2)}', '%b %d')
            return f'{datetime.date.today().year}-{dt.month:02d}-{dt.day:02d}'
        except ValueError:
            pass
    return None


def _parse_html(html: str) -> dict | None:
    """Extract ETF summary values from rendered CoinGlass HTML."""
    from html.parser import HTMLParser

    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.texts = []
        def handle_data(self, data):
            t = data.strip()
            if t:
                self.texts.append(t)

    p = TextExtractor()
    p.feed(html)
    lines = p.texts
    return _parse_lines(lines)


def _parse_lines(lines: list) -> dict | None:
    """Parse a flat list of text fragments into ETF metrics."""
    daily_flow = None
    total_cumulative = None
    last_update_date = None

    usd_re = re.compile(r'^[+\-]?\$?[\d,.]+[BMKbmk]?$')

    for i, line in enumerate(lines):
        # Daily Total Net Inflow card
        if 'Daily Total Net Inflow' in line and daily_flow is None:
            for j in range(i + 1, min(i + 8, len(lines))):
                t = lines[j].replace(',', '')
                if re.search(r'[+\-]?\$[\d.]+[BMK]', t):
                    daily_flow = _parse_usd_m(t)
                    break

        # Total Net Inflow card
        if line.strip() == 'Total Net Inflow' and total_cumulative is None:
            for j in range(i + 1, min(i + 8, len(lines))):
                t = lines[j].replace(',', '')
                if re.search(r'[+\-]?\$[\d.]+[BMK]', t):
                    total_cumulative = _parse_usd_m(t)
                    break

        # Last update: Jul 10, 6:45 AM
        if 'Last update' in line and last_update_date is None:
            m = re.search(r'Last update\s*[:\-]?\s*(.+)', line)
            txt = m.group(1) if m else ''
            if not txt and i + 1 < len(lines):
                txt = lines[i + 1]
            last_update_date = _parse_date(txt)

        # Update Time: Jul 10, 2026 - 6:45 AM
        if 'Update Time' in line and last_update_date is None:
            m = re.search(r'Update Time\s*[:\-]\s*(.+)', line)
            txt = m.group(1) if m else (lines[i + 1] if i + 1 < len(lines) else '')
            last_update_date = _parse_date(txt)

    if daily_flow is None and total_cumulative is None:
        logger.error('CoinGlass: no values parsed. Lines sample: %s', lines[:30])
        return None

    # 7-day rolling sum from table rows
    value_7d = _parse_7d_total(lines)

    if last_update_date is None:
        last_update_date = datetime.date.today().isoformat()

    result = {
        'value': round(value_7d if value_7d is not None else (daily_flow or 0.0), 2),
        'daily_flow': round(daily_flow or 0.0, 2),
        'total_cumulative': round(total_cumulative or 0.0, 2),
        'date': last_update_date,
    }
    logger.info(
        'CoinGlass ETF: date=%s  daily=%.1fM  7d=%.1fM  cum=%.1fM',
        result['date'], result['daily_flow'], result['value'], result['total_cumulative'],
    )
    return result


def _parse_7d_total(lines: list) -> float | None:
    """Sum the Total column for the last 7 dated rows in the table."""
    date_re = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    usd_re = re.compile(r'^[+\-]?\$[\d,.]+[BMK]?$')

    rows = []
    i = 0
    while i < len(lines):
        if date_re.match(lines[i]):
            date_str = lines[i]
            nums = []
            j = i + 1
            while j < len(lines) and not date_re.match(lines[j]) and j < i + 25:
                if usd_re.match(lines[j].replace(',', '')):
                    nums.append(lines[j])
                j += 1
            if nums:
                rows.append((date_str, _parse_usd_m(nums[-1])))
            i = j
        else:
            i += 1

    if not rows:
        return None
    rows.sort(key=lambda r: r[0])
    return round(sum(v for _, v in rows[-7:]), 2)


# ---------------------------------------------------------------------------
# Fetch via Zyte API (primary — bypasses bot detection)
# ---------------------------------------------------------------------------

def _fetch_via_zyte(api_key: str) -> dict | None:
    """Use Zyte API browserHtml to render the CoinGlass ETF page."""
    logger.info('CoinGlass: fetching via Zyte browserHtml...')
    try:
        resp = requests.post(
            _ZYTE_API_URL,
            auth=(api_key, ''),
            json={'url': _URL, 'browserHtml': True},
            timeout=60,
        )
        if resp.status_code != 200:
            logger.warning('CoinGlass/Zyte: status %d: %s', resp.status_code, resp.text[:200])
            return None
        html = resp.json().get('browserHtml', '')
        if not html:
            logger.warning('CoinGlass/Zyte: empty browserHtml')
            return None
        return _parse_html(html)
    except Exception as e:
        logger.error('CoinGlass/Zyte: %s', e)
        return None


# ---------------------------------------------------------------------------
# Fetch via Playwright (fallback — direct, no proxy)
# ---------------------------------------------------------------------------

def _fetch_via_playwright() -> dict | None:
    """Direct Playwright scrape as fallback when Zyte is unavailable."""
    logger.info('CoinGlass: fetching via Playwright...')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning('CoinGlass: playwright not available')
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                locale='en-US',
                extra_http_headers={'accept-language': 'en-US,en;q=0.9'},
            )
            page = ctx.new_page()
            page.goto(_URL, wait_until='domcontentloaded', timeout=_TIMEOUT_MS)
            try:
                page.wait_for_selector('text=Daily Total Net Inflow', timeout=25_000)
            except Exception:
                logger.warning('CoinGlass/Playwright: summary cards not found')
                browser.close()
                return None
            page.wait_for_timeout(2000)
            html = page.content()
            browser.close()
        return _parse_html(html)
    except Exception as e:
        logger.error('CoinGlass/Playwright: %s', e)
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_etf_flows_coinglass() -> dict | None:
    """Return ETF flows dict or None. Tries Zyte first, then Playwright."""
    api_key = os.getenv('ZYTE_API_KEY')
    if api_key:
        result = _fetch_via_zyte(api_key)
        if result:
            return result
        logger.warning('CoinGlass: Zyte failed, trying Playwright...')

    return _fetch_via_playwright()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    import json
    print(json.dumps(get_etf_flows_coinglass(), indent=2))
