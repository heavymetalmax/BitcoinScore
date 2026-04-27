# BTC Signal Score

Project skeleton for automated on-chain metric collection and a static dashboard. Contains:
- `scraper/` — scraper code (Playwright + API wrappers)
- `data/` — generated `data.json` and history snapshots
- `web/` — frontend helpers and mapping logic
- `.github/workflows/update-data.yml` — GitHub Actions workflow skeleton

Next steps: implement MacroMicro Playwright scraping, integrate `data.json` into `index.html`, persist sliders.
