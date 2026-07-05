"""ETF flows scraper using CoinGlass (fresher than Farside, used as primary).

Scrapes coinglass.com homepage ETF table via Playwright (USD mode).
Returns the same dict format as etf_flows.get_etf_flows():
  {value, daily_flow, total_cumulative, date}
where value = 14-day rolling sum in $M, total_cumulative in $M.

CoinGlass typically lags 1-2 days behind real time vs Farside's 9+ day lag.
"""
import logging
import re
import datetime
import pandas as pd

logger = logging.getLogger(__name__)

_URL = 'https://coinglass.com'
_TIMEOUT_MS = 30_000


def _parse_usd_m(s: str) -> float:
    """Parse '+166.00M', '-27.08B', '0', '−444.50M' → float in $M."""
    s = s.strip().replace(',', '').replace('−', '-')
    if not s or s in ('0',):
        return 0.0
    mult = 1.0
    if s.endswith('B'):
        mult = 1000.0
        s = s[:-1]
    elif s.endswith('M'):
        s = s[:-1]
    elif s.endswith('K'):
        mult = 0.001
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return 0.0


def _parse_section(body_text: str):
    """Parse ETF section from page body text.

    Returns (rows, total_cumulative_m) where rows = [{date, total}].
    """
    etf_idx = body_text.find('Total Bitcoin Spot ETF')
    if etf_idx < 0:
        return [], None

    section = body_text[etf_idx:]
    show_all_idx = section.find('Show All')
    if show_all_idx > 0:
        section = section[:show_all_idx]

    # Normalize: replace tabs with newlines, split into lines
    lines = [l.strip() for l in section.replace('\t', '\n').split('\n') if l.strip()]

    date_re = re.compile(r'^\d{4}-\d{2}-\d{2}')
    num_re = re.compile(r'^[+\-]?[\d,.]+[BMK]?$')

    rows = []
    current_date = None
    current_nums = []
    in_total_row = False
    total_nums = []

    for line in lines:
        if line == 'Total':
            # Flush current date row first
            if current_date and current_nums:
                rows.append({'date': current_date, 'total': _parse_usd_m(current_nums[-1])})
                current_date = None
                current_nums = []
            in_total_row = True
            continue

        if in_total_row:
            if num_re.match(line):
                total_nums.append(line)
            elif date_re.match(line):
                in_total_row = False
                # Fall through to date handling
            else:
                continue

        if in_total_row:
            continue

        if date_re.match(line):
            if current_date and current_nums:
                rows.append({'date': current_date, 'total': _parse_usd_m(current_nums[-1])})
            current_date = line[:10]
            current_nums = []
        elif current_date and num_re.match(line):
            current_nums.append(line)

    # Flush last row
    if current_date and current_nums:
        rows.append({'date': current_date, 'total': _parse_usd_m(current_nums[-1])})

    # Total cumulative: last value of total_nums row
    total_cumulative = _parse_usd_m(total_nums[-1]) if total_nums else None

    return rows, total_cumulative


def get_etf_flows_coinglass():
    """Scrape ETF flows from CoinGlass (USD mode, Playwright).

    Returns dict {value, daily_flow, total_cumulative, date} or None on failure.
    """
    logger.info('CoinGlass: fetching ETF flows via Playwright...')
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        logger.warning('CoinGlass: playwright not available')
        return None

    body_text = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_extra_http_headers({'accept-language': 'en-US,en;q=0.9'})

            page.goto(_URL, wait_until='domcontentloaded', timeout=_TIMEOUT_MS)

            # Wait for ETF section to render
            try:
                page.wait_for_selector('text=Total Bitcoin Spot ETF', timeout=20_000)
            except Exception:
                logger.warning('CoinGlass: ETF section not found within timeout')
                browser.close()
                return None

            # Click USD tab for USD-denominated values
            try:
                # Find all elements with text "USD" near the ETF section
                usd_els = page.locator('text=USD').all()
                for el in usd_els:
                    try:
                        if el.is_visible():
                            el.click(timeout=3000)
                            break
                    except Exception:
                        continue
                page.wait_for_timeout(1500)
            except Exception:
                logger.debug('CoinGlass: USD tab click failed, using default')

            body_text = page.inner_text('body')
            browser.close()

    except Exception as e:
        logger.error('CoinGlass: Playwright error: %s', e)
        return None

    if not body_text:
        return None

    rows, total_cum = _parse_section(body_text)

    if not rows:
        logger.error('CoinGlass: no date rows parsed from ETF section')
        return None

    # Build calendar-day series and compute 14-day rolling sum
    df = pd.DataFrame(rows)
    df['dt'] = pd.to_datetime(df['date'])
    df = df.sort_values('dt').drop_duplicates('date').reset_index(drop=True)

    df_cal = df.set_index('dt')['total'].resample('D').sum().fillna(0.0)

    latest = df.iloc[-1]
    latest_date = str(latest['date'])
    daily_flow = float(latest['total'])

    rolling = df_cal.rolling(window=14).sum()
    value_14d = float(rolling.iloc[-1]) if not rolling.empty else 0.0

    if total_cum is None:
        total_cum = float(df_cal.cumsum().iloc[-1])

    result = {
        'value': round(value_14d, 2),
        'daily_flow': round(daily_flow, 2),
        'total_cumulative': round(total_cum, 2),
        'date': latest_date,
    }

    logger.info(
        'CoinGlass ETF: date=%s  14d=%.1fM  daily=%.1fM  cum=%.1fM',
        latest_date, value_14d, daily_flow, total_cum,
    )
    return result


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    import json
    print(json.dumps(get_etf_flows_coinglass(), indent=2))
