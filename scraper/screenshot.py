import os
import shutil
import threading
import http.server
import socketserver
from playwright.sync_api import sync_playwright

class ThreadedHTTPServer:
    def __init__(self, directory, port=0):
        self.directory = directory
        # Bind to 127.0.0.1 and port 0 (allocates a free random port)
        handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(*args, directory=self.directory, **kwargs)
        # Avoid log output to stdout during scraper run
        handler.log_message = lambda *args: None
        self.server = socketserver.TCPServer(("127.0.0.1", port), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

def generate_stressless_screenshot():
    print("Generating Stressless dashboard screenshot...")
    
    # 1. Copy data.json to web/data.json to ensure the screenshot runs with the newest payload
    src_data = 'data/data.json'
    dst_data = 'web/data.json'
    if os.path.exists(src_data):
        shutil.copy2(src_data, dst_data)
        print(f"Copied {src_data} to {dst_data} for screenshot rendering")
    else:
        print(f"Warning: {src_data} not found. Using existing {dst_data}")

    # 2. Start a temporary HTTP server serving the web directory
    web_dir = os.path.abspath('web')
    server = ThreadedHTTPServer(web_dir)
    server.start()
    
    try:
        url = f"http://127.0.0.1:{server.port}/minimal.html"
        
        # 3. Open Playwright browser and capture the screenshot in dark mode
        with sync_playwright() as p:
            # Use headless chromium
            browser = p.chromium.launch(headless=True)
            
            # Force dark color scheme so the screenshot looks consistent
            context = browser.new_context(
                color_scheme='dark',
                viewport={'width': 750, 'height': 750}
            )
            page = context.new_page()
            
            # Navigate to minimal.html
            print(f"Navigating to {url}...")
            page.goto(url)
            
            # Wait for the main score to load
            page.wait_for_selector("#s-final")
            page.wait_for_function('document.getElementById("s-final").textContent !== "—"')
            
            # Additional small delay for transitions/wallpapers/fonts to settle
            page.wait_for_timeout(1200)
            
            # Hide controls and center the widget in the viewport for a clean screenshot with wallpaper
            page.evaluate("""() => {
                const hideSelectors = ['.status-bar', '.settings-gear', '#commentary-gear', '.disclaimer-trigger', '.no-advice-label', '.wallpaper-floor'];
                hideSelectors.forEach(sel => {
                    const el = document.querySelector(sel);
                    if (el) el.style.setProperty('display', 'none', 'important');
                });
                
                const center = document.getElementById('center-display');
                if (center) {
                    center.style.setProperty('margin-top', '0', 'important');
                    center.style.setProperty('position', 'absolute', 'important');
                    center.style.setProperty('top', '50%', 'important');
                    center.style.setProperty('left', '50%', 'important');
                    center.style.setProperty('transform', 'translate(-50%, -50%)', 'important');
                }
            }""")
            
            # Allow some time for style reflow
            page.wait_for_timeout(300)
            
            # Save screenshot of the entire viewport (with wallpaper background)
            target_img = os.path.join(web_dir, 'stressless_preview.png')
            page.screenshot(path=target_img)
            
            print(f"Successfully generated Stressless preview screenshot at: {target_img}")
            browser.close()
            
    except Exception as e:
        print(f"Error generating screenshot: {e}")
    finally:
        server.stop()
        print("Temporary screenshot HTTP server stopped.")

if __name__ == '__main__':
    # Make sure we run from the repository root
    if not os.path.exists('web'):
        os.chdir('..')
    generate_stressless_screenshot()
