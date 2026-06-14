# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**BitcoinScore** is a daily-updated Bitcoin buy risk index (0–100 scale). It collects 10+ on-chain and macro metrics from external APIs and computes a composite score. The output is `data/data.json`, which is served via static HTML dashboards on GitHub Pages.

## Development Commands

```bash
# Install dependencies
pip install -r scraper/requirements.txt

# Run the scraper (primary entry point)
python -m scraper.scraper

# Alternative: run via the root-level helper
python run_scraper.py

# Retry any metrics that came back None
python tools/retry_stale_metrics.py

# Backtest scoring logic against 16 historical key dates (2018–2026)
python tools/backtest.py

# Quick sanity-check backtest (5 key dates)
python tools/backtest_fast.py
```

**Required environment variables** (`.env` file, not committed):
```
ZYTE_API_KEY=...   # MacroMicro scraping via Zyte proxy
CMC_API_KEY=...    # CoinMarketCap Pro API
```

Set `FORCE_LIVE=1` to bypass the 24-hour metric cache when testing live fetches.

## Architecture

### Data flow

```
python -m scraper.scraper
  └── build_payload()              # collect all metrics
        ├── CMC / CoinGecko        → btc_price, fear_greed, btc_dominance
        ├── BMP (via Playwright)   → nupl, mvrv, rhodl_ratio, cvdd_ratio, asopr
        ├── Farside (HTML parse)   → etf_flows
        ├── FRED REST              → yield_curve
        ├── MacroMicro / FRED      → m2_mom (YoY%)
        ├── Kraken OHLCV           → cipherb, mayer_multiple, smc, funding_rate
        └── alternative.me         → fear_greed (fallback)
  └── compute_scores(metrics)      # scoring.py → onchain_score, tech_score, final_score
  └── compute_zone_forecast(...)   # zone_forecast.py → buy/sell price levels
  └── write_json('data/data.json') # final output
  └── append to data/history/      # daily_vector.json + per-metric history files
```

### Scoring engine (`scraper/scoring.py`)

Two independent group averages are computed, then blended:

| Group | Final weight | Metrics & weights |
|---|---|---|
| **On-Chain** | 50% | NUPL ×30%, RHODL ×20%, MVRV Z-score ×20%, CVDD ×15%, aSOPR ×15% |
| **Tech/Macro** | 50% | CipherB ×40%, Mayer Multiple ×20%, ETF Flows ×10%, Fear & Greed ×10%, Yield Curve ×10%, M2 YoY ×10% |

Each raw metric value is mapped to a 0–100 risk score via a dedicated `map_*()` function (e.g. `map_nupl`, `map_mvrv`, `map_yield_curve`). Three metrics (`nupl`, `mvrv`, `mayer`) also receive **adaptive calibration**: their fixed map score is blended 50/50 with a rolling 4-year percentile rank from historical data. This compensates for Bitcoin's maturing market reducing the amplitude of these indicators over cycles.

CipherB additionally applies a ±12 penalty/bonus when a fast divergence is active (bearish divergence adds 12 to the risk score; bullish divergence subtracts 12).

Two published sub-scores exist alongside `final_score`:
- `onchain_score` = 80% OC + 20% Tech
- `tech_score` = 20% OC + 80% Tech

### Caching behaviour

`scraper.py` reads `data/data.json` on startup. If the file is <24 hours old and contains a non-null NUPL, it reuses all cached metric values and skips live fetches. Override with `FORCE_LIVE=1`.

On live-fetch failure for any metric, the scraper falls back to the last cached value from `data.json` or `data/history/daily_vector.json` rather than writing `None` — a missing metric silently reduces the weighting denominator and skews the score.

### Playwright scraping (`scraper/mm_utils.py`)

`get_bmp_trace(chart_url, trace_name)` opens a headless browser, waits for BMP's Plotly chart to render, and extracts the last data point. Most on-chain metrics (NUPL, MVRV, RHODL, CVDD, aSOPR) use this pattern. The full historical series can be fetched via `get_bmp_trace_full()` for backfill jobs.

### History files (`data/history/`)

- `daily_vector.json` — append-only log of every daily run: raw metric scalars + mapped 0-100 scores + group scores. De-duped by date. Used by the adaptive calibration to compute rolling percentiles.
- `scores.json` — lightweight score-only history for the dashboard sparkline.
- `{metric}_history.json` — backfilled seed series for adaptive calibration (NUPL, MVRV, MVRV Z-score, RHODL, Mayer).

### Frontend (`web/`)

Static HTML5 + vanilla JS. No build step; files are served directly from GitHub Pages. `web/data.json` is the live file (copied from `data/data.json` by CI). Multiple dashboard variants exist (`index.html`, `minimal.html`, `classic.html`, `compare.html`).

## CI/CD

**`update-data.yml`** runs daily at 08:00 UTC inside `mcr.microsoft.com/playwright/python:v1.51.0-noble`:
1. Runs scraper → retries stale metrics → generates ICS feed
2. Copies `data/data.json` → `web/data.json`
3. Commits with `[skip ci]` + rebases with `--strategy-option=theirs` on conflict
4. Deploys `web/` to GitHub Pages

**`deploy.yml`** redeploys Pages on any push to `main` that touches `web/`.

## Key Conventions (from `instructions.md`)

- **Surgical changes only** — modify exactly what the task requires; do not reformat unrelated code, change indentation, or rewrite author comments.
- **Ask before building** — if the task is ambiguous or has multiple architectural paths, describe the plan in 1–2 sentences and wait for confirmation before coding.
- **Minimum viable implementation** — avoid new classes or abstractions where a plain function suffices.
- **Verify after changes** — run the scraper or backtest to confirm nothing is broken before reporting done.

## Adding or Modifying a Metric

1. Create `scraper/<metric_name>.py` with a `get_<metric>()` function returning a scalar or dict.
2. Add a `map_<metric>(v)` function in `scraper/scoring.py` that maps the raw value to 0–100.
3. Add the metric to `OC_WEIGHTS` or `TECH_WEIGHTS` in `scoring.py` (weights in each group must sum to 1.0).
4. Wire the fetch into `build_payload()` in `scraper/scraper.py` (follow the existing `metric_specs` list pattern for cacheable metrics).
5. If the metric benefits from adaptive calibration, add it to `ADAPTIVE_METRICS` and provide a seed history file in `data/history/`.
6. Run `python tools/backtest.py` to verify the score impact on historical key dates.

Обовязково використовувати звернення до архітектури і планування проектування при внесені змін, аби зміни були узгоджені з рештою логіки коду і алгоритмів у цілому!
