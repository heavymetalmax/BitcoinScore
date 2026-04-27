from playwright.sync_api import sync_playwright
import time
p = sync_playwright().start()
b = p.chromium.launch(headless=False)
c = b.new_context(viewport={'width':1280,'height':800})
page = c.new_page()
page.goto("https://en.macromicro.me/charts/55589/global-geopolitical-risk-index", wait_until='domcontentloaded', timeout=60000)
print("Browser opened — you have 120s to inspect the page.")
time.sleep(120)
b.close()
p.stop()
