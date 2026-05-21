import json
import os
from functools import wraps
import time

def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def validate_data(data):
    # Minimal validation: required top-level keys
    req = ['timestamp','btc_price','fear_greed','metrics']
    missing = [k for k in req if k not in data]
    return missing

def retry(times=3, delay=5):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            for i in range(times):
                try:
                    return fn(*a, **kw)
                except Exception:
                    if i == times-1:
                        raise
                    time.sleep(delay)
        return wrapper
    return deco


def human_visit(url, wait_seconds=8, storage_state_path='data/debug/storage.json', headful=True, stealth=False, use_real_browser=False):
    """Open a Playwright browser window to `url`, simulate light human actions, wait, then close.
    Returns True on success, False otherwise. Designed to be a lightweight humanizer before scraping.
    """
    try:
        from playwright.sync_api import sync_playwright
        import random
        with sync_playwright() as p:
            launch_args = []
            if stealth:
                launch_args += ["--disable-blink-features=AutomationControlled"]
            # If requested, launch a persistent context using the real Chrome profile (macOS default path)
            ctx = None
            page = None
            if use_real_browser:
                profile_path = os.path.expanduser('~/Library/Application Support/Google/Chrome/Default')
                if not os.path.exists(profile_path):
                    return False
                try:
                    ctx = p.chromium.launch_persistent_context(user_data_dir=profile_path, channel='chrome', headless=False, args=launch_args)
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                except Exception:
                    try:
                        ctx = p.chromium.launch_persistent_context(user_data_dir=profile_path, headless=False, args=launch_args)
                        page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    except Exception:
                        return False
            else:
                browser = p.chromium.launch(headless=not headful, args=launch_args)
                ctx_kwargs = {}
                if storage_state_path and os.path.exists(storage_state_path):
                    ctx_kwargs['storage_state'] = storage_state_path
                # apply some common stealth/profile tweaks via context options
                if stealth:
                    # realistic desktop Chrome UA (kept generic)
                    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
                    ctx_kwargs.update({
                        'user_agent': ua,
                        'locale': 'en-US',
                        'viewport': {'width': 1366, 'height': 768},
                        'timezone_id': 'America/New_York'
                    })
                ctx = browser.new_context(**ctx_kwargs)
                page = ctx.new_page()
                if stealth:
                    # remove webdriver flag and set some navigator properties to mimic real browser
                    page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        window.chrome = { runtime: {} };
                        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
                    """)
            page.goto(url, wait_until='networkidle', timeout=60000)
            # small random wait to mimic thinking/reading
            page.wait_for_timeout(int(1000 * (wait_seconds * (0.5 + random.random()))))
            try:
                # scroll slowly
                page.evaluate("() => {window.scrollBy(0, window.innerHeight/2);} ")
                page.wait_for_timeout(800 + int(400 * random.random()))
                page.evaluate("() => {window.scrollBy(0, -window.innerHeight/3);} ")
                page.wait_for_timeout(800 + int(400 * random.random()))
            except Exception:
                pass
            try:
                ctx.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
        return True
    except Exception:
        return False


def is_valid_metric(name, value):
    """Basic validation for metric values.
    Returns True if value looks sane, False otherwise.
    """
    import math

    if value is None:
        return False
    # numeric
    if isinstance(value, (int, float)):
        if math.isnan(value) or math.isinf(value):
            return False
        thresholds = {
            'nupl': (-50, 100),
            'mvrv': (-10, 20),
            'addresses_in_loss': (0, 100),
            'm2': (None, None),
            'real_yield': (-5, 10),   # FRED DFII10 US 10Y Real Yield (%)
            'geopolitical_risk': (0, 2000),  # GPR Index (Caldara & Iacoviello)
            'rhodl_ratio': (0, 500000),  # RHODL Ratio (BMP)
        }
        if name in thresholds:
            lo, hi = thresholds[name]
            if lo is not None and value < lo:
                return False
            if hi is not None and value > hi:
                return False
        return True

    # dicts: try common numeric fields
    if isinstance(value, dict):
        for k in ('delta', 'value', 'current', 'last'):
            if k in value and isinstance(value[k], (int, float)):
                return is_valid_metric(name, value[k])
        return False

    # tuples/lists: accept if any numeric element seems valid
    if isinstance(value, (list, tuple)):
        for v in value:
            if isinstance(v, (int, float)) and is_valid_metric(name, v):
                return True
        return False

    return False
