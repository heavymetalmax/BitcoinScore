# CLAUDE.md

This file provides guidance to Claude when working with code in this repository.

## What This Project Is

**BitcoinScore** is a daily-updated Bitcoin buy risk index (0–100 scale). It collects 10+ on-chain and macro metrics from external APIs and computes a composite score. The output is `data/data.json`, which is served via static HTML dashboards on GitHub Pages.

User-facing naming: **BRI Score** (`final_score`/`bri_score`), **Market Context**
(legacy `v3_*`), **Forward Risk** (`forward_risk`, legacy `v5b_score`), and
The retired Legacy Model is not executed and must not be added back to production.
Do not introduce V3/V5 names in new UI or user-facing documentation; remaining
versioned fields are compatibility aliases for BRI Score and Forward Risk only.

## Production Setup

The scraper runs daily on a **home Mac Mini server (DietPi, x86_64)** at `dietpi@10.0.1.10`:
- Cron: `0 6 * * *` → `~/run_bitcoinscore.sh` → git pull → scraper → git push
- GitHub Actions (`update-data.yml`) runs at **10:00 UTC as fallback only** — checks `data.json` timestamp; if already today's date, skips everything

## Development Commands

```bash
# Install dependencies
pip install -r scraper/requirements.txt
python -m playwright install chromium

# Run the scraper (primary entry point)
python -m scraper.scraper

# Force live fetch (bypass 24h cache)
FORCE_LIVE=1 python -m scraper.scraper

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

## Architecture

### Data flow

```
python -m scraper.scraper
  └── build_payload()              # collect all metrics
        ├── CMC / CoinGecko        → btc_price, fear_greed, btc_dominance
        ├── BMP (via Playwright)   → nupl, mvrv, rhodl_ratio, cvdd_ratio, asopr
        ├── Farside (HTML parse)   → etf_flows
        ├── FRED REST              → yield_curve
        ├── MacroMicro / FRED      → m2_yoy (YoY%)
        ├── Kraken OHLCV           → cipherb, mayer_multiple, smc, funding_rate
        └── alternative.me         → fear_greed (fallback)
  └── scoring_pipeline.py          # V1 → V3 → orchestrator → V3.2 Override
  └── write_json('data/data.json') # final output
  └── append to data/history/      # daily_vector.json + per-metric history files
```

### Scoring — `scraper/score.py` is the only source of truth

**`scraper/scoring.py` (V1) is LEGACY.** Still runs in the pipeline to produce `zone_forecast`, `commentary`, and `adaptive_calibration`, but its scores are overwritten by V3. Do NOT rely on V1 scores. `v1_score` has been removed from JSON output.

**`scraper/score.py` (V4 Market Context — authoritative)** pipeline:

1. Normalize raw metrics to 0–100 via `scraper/normalizer.py`.
2. Build continuous BOTTOM / NEUTRAL / TOP heuristic weights from bottom confluence, HMM state similarity, and the halving-cycle prior. These are not calibrated probabilities.
3. Evaluate dynamic utility coefficients via `scraper/utility_evaluator.py`.
4. Compute four utility-weighted baskets: OC (on-chain), MS (market sentiment), MC (macro), and CP (cycle position).
5. Build structural context from OC + CP + MC, then apply TiZ and coherence dampening.
6. Build vectorial context from MS and top-phase divergence, then synthesize the authoritative `final_score`.
7. Apply the DXY modifier and Pi Cycle top override; Forward Risk is a downstream diagnostic output. `market_regime` is actionable only when Forward Risk beats its purged walk-forward baseline and basket data quorums pass.

`onchain_avg` and `tech_avg` remain informational compatibility fields. The four baskets and structural/vectorial synthesis feed `final_score`.

**`scraper/scoring_pipeline.py`** runs legacy V1/V2 first, then its authoritative Market Context block overwrites `final_score`, `onchain_score`, and `tech_score` with `score.py` outputs.

### Key JSON fields in `data/data.json`

| Field | Meaning |
|---|---|
| `final_score` | V3 meta_score (0–100, authoritative) |
| `onchain_score` / `tech_score` | V3 group averages (informational) |
| `v3_score` | Raw V3 before orchestrator |
| `v3_phase` | BOTTOM / NEUTRAL / TOP |
| `v3_utilities` | Per-metric relevance weights (0–1) — NOT risk scores |
| `v3_normalized_scores` | Per-metric V3 risk scores (0–100) — shown in UI |
| `v3_w_bot` / `v3_w_top` | Heuristic phase mixture weights (not calibrated probabilities) |
| `v5b_validated` | Whether purged walk-forward MAE beats the naive baseline |
| `decision_suppressed` | True when validation or data-quality gates block a decision |

### HMM Phase Model

`data/v3_phase_model.pkl` — trained by `tools/train_v3_hmm_model.py`. Uses `HMMPhaseClassifier` (custom class). **Must import this class before `pickle.load`** — `score.py` does this automatically. State cache `data/v3_hmm_state_cache.json` is tracked in git so daily HMM state chains correctly across CI runs.

### Caching behaviour

`scraper.py` reads `data/data.json` on startup. If file is <24 hours old and NUPL is non-null, reuses cached metrics. Override with `FORCE_LIVE=1`.

### Playwright scraping (`scraper/mm_utils.py`)

`get_bmp_trace(chart_url, trace_name)` opens headless Chromium, waits for BMP Plotly chart to render, extracts last data point. Used for NUPL, MVRV, RHODL, CVDD, aSOPR.

### History files (`data/history/`)

- `daily_vector.json` — append-only daily log: raw scalars + 0-100 scores. Used by adaptive calibration.
- `scores.json` — score-only history for dashboard sparkline + HMM phase chaining.
- `{metric}_history.json` — seed series for adaptive calibration (NUPL, MVRV, RHODL, Mayer).
- `v3_hmm_state_cache.json` — HMM forward-pass state, updated daily by scraper.

### Frontend (`web/`)

Static HTML5 + vanilla JS. No build step. `web/data.json` copied from `data/data.json` by CI.

- `index.html` — main dashboard
- `classic.html` — metric table with 5 columns: Indicator / Score / vs History / V3 Risk (0–100) / Signal
  - "V3 Risk" column reads `v3_normalized_scores` (per-metric risk, color-coded green→red)
  - Signal badges use raw metric values mapped through `getSignal()`
- `minimal.html`, `compare.html` — alternative views

## CI/CD

**`update-data.yml`** — runs at 10:00 UTC daily as **fallback**:
1. Checks `data.json` timestamp — if already today's date → skips all steps (home server already ran)
2. Otherwise: scraper → retry stale → history → ICS → sparklines → copy to web/ → commit → Pages deploy

**`deploy.yml`** — redeploys Pages on any push to `main` that touches `web/`.

## Key Conventions

- **Surgical changes only** — modify exactly what the task requires; do not reformat unrelated code.
- **Ask before building** — if the task is ambiguous, describe the plan and wait for confirmation.
- **Minimum viable implementation** — avoid new classes where a plain function suffices.
- **Verify after changes** — run backtest to confirm nothing is broken: `python tools/backtest.py`
- **Architecture first** — before any change, check how it fits with scoring_pipeline.py and the V3 data flow.

## Adding or Modifying a Metric

1. Create `scraper/<metric_name>.py` with `get_<metric>()` returning a scalar or dict.
2. Add `normalize_<metric>()` logic in `scraper/normalizer.py` (maps raw value → 0–100 risk).
3. Add the metric to the appropriate `BASKET_*` set in `score.py`.
4. Wire fetch into `build_payload()` in `scraper/scraper.py`.
5. Update `utility_evaluator.py` with relevance profile per phase.
6. Run `python tools/backtest.py` to verify score impact.

Обовязково використовувати звернення до архітектури і планування проектування при внесені змін, аби зміни були узгоджені з рештою логіки коду і алгоритмів у цілому!
