from playwright.sync_api import sync_playwright
import os

os.makedirs('data/debug', exist_ok=True)

def _open_and_wait_then_save():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        ctx = b.new_context(viewport={'width':1280,'height':800})
        # Open NUPL series page by default. GeoRisk can be opened by setting env var OPEN_GEORISK=1
        page1 = ctx.new_page()
        page1.goto('https://en.macromicro.me/series/45910/bitcoin-nupl', wait_until='domcontentloaded', timeout=120000)
        print('Browser opened with NUPL series tab.')
        if os.environ.get('OPEN_GEORISK') in ('1', 'true', 'True'):
            page2 = ctx.new_page()
            page2.goto('https://en.macromicro.me/charts/55589/global-geopolitical-risk-index', wait_until='domcontentloaded', timeout=120000)
            print('Also opened Geopolitical Risk chart because OPEN_GEORISK was set.')
        print('If you see a blank page, try Refresh there. Solve Cloudflare when prompted in the NUPL tab.')
        input('After the NUPL page shows the stats/values, press Enter here to save storage_state...')
        ctx.storage_state(path='data/debug/storage.json')
        print('Saved initial data/debug/storage.json')
        b.close()


def _verify_reload():
    # Re-open a fresh browser context using saved storage_state to validate it shows values
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        try:
            try:
                ctx = b.new_context(storage_state='data/debug/storage.json', viewport={'width':1280,'height':800})
            except Exception:
                ctx = b.new_context(viewport={'width':1280,'height':800})
            p1 = ctx.new_page()
            p1.goto('https://en.macromicro.me/series/45910/bitcoin-nupl', wait_until='domcontentloaded', timeout=120000)
            if os.environ.get('OPEN_GEORISK') in ('1', 'true', 'True'):
                p2 = ctx.new_page()
                p2.goto('https://en.macromicro.me/charts/55589/global-geopolitical-risk-index', wait_until='domcontentloaded', timeout=120000)
                print('Reopened with saved storage_state in NUPL and GeoRisk tabs.')
                input('If both pages show values correctly, press Enter to finalize saving (will overwrite storage_state)...')
            else:
                print('Reopened with saved storage_state in NUPL tab. Please verify NUPL shows metrics.')
                input('If NUPL shows values correctly, press Enter to finalize saving (will overwrite storage_state)...')
            ctx.storage_state(path='data/debug/storage.json')
            print('Final storage_state saved to data/debug/storage.json')
        finally:
            try:
                b.close()
            except Exception:
                pass
        p.stop()


if __name__ == '__main__':
    _open_and_wait_then_save()
    # Allow skipping the verification step to avoid re-opening pages
    # which can trigger Cloudflare loops. Set env `SKIP_VERIFY=1` to skip.
    if os.environ.get('SKIP_VERIFY') in ('1', 'true', 'True'):
        print('SKIP_VERIFY is set — skipping verification step.')
    else:
        try:
            _verify_reload()
        except Exception as e:
            print('Warning: verification step failed:', e)
