# Graph Report - /Users/max/BitcoinScore  (2026-06-08)

## Corpus Check
- 87 files · ~159,355 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 622 nodes · 1013 edges · 60 communities (47 shown, 13 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 9 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_backtest.py|backtest.py]]
- [[_COMMUNITY_value|value]]
- [[_COMMUNITY_value|value]]
- [[_COMMUNITY_get_bmp_trace()|get_bmp_trace()]]
- [[_COMMUNITY_TechMacro Metric Group (50% weight)|Tech/Macro Metric Group (50% weight)]]
- [[_COMMUNITY_cipherb.py|cipherb.py]]
- [[_COMMUNITY_BitcoinScore Project|BitcoinScore Project]]
- [[_COMMUNITY_generate_ics.py|generate_ics.py]]
- [[_COMMUNITY_scraper.py|scraper.py]]
- [[_COMMUNITY_@claude-flowembeddings|@claude-flow/embeddings]]
- [[_COMMUNITY_cmc.py|cmc.py]]
- [[_COMMUNITY_session-1780856233859.json|session-1780856233859.json]]
- [[_COMMUNITY_session-1780856237105.json|session-1780856237105.json]]
- [[_COMMUNITY_retry_stale_metrics.py|retry_stale_metrics.py]]
- [[_COMMUNITY_current.json|current.json]]
- [[_COMMUNITY_zone_forecast.py|zone_forecast.py]]
- [[_COMMUNITY_zone_forecast|zone_forecast]]
- [[_COMMUNITY_funding_rate.py|funding_rate.py]]
- [[_COMMUNITY_cipherb_band_backtest.py|cipherb_band_backtest.py]]
- [[_COMMUNITY_zone_forecast|zone_forecast]]
- [[_COMMUNITY_cvdd_history.json|cvdd_history.json]]
- [[_COMMUNITY_mayer_history.json|mayer_history.json]]
- [[_COMMUNITY_mvrv_history.json|mvrv_history.json]]
- [[_COMMUNITY_nupl_history.json|nupl_history.json]]
- [[_COMMUNITY_rhodl_history.json|rhodl_history.json]]
- [[_COMMUNITY_Scraper for Global M2 liquidity signal.  Primary S|Scraper for Global M2 liquidity signal.  Primary S]]
- [[_COMMUNITY_Pi Cycle Top Indicator — 111DMA vs 2×350DMA on dai|Pi Cycle Top Indicator — 111DMA vs 2×350DMA on dai]]
- [[_COMMUNITY_ML weight probe — honest feasibility check (NOT a|ML weight probe — honest feasibility check (NOT a ]]
- [[_COMMUNITY_buyzone_price_solver.py|buyzone_price_solver.py]]
- [[_COMMUNITY_Scraper for MVRV Z-Score metric.|Scraper for MVRV Z-Score metric.]]
- [[_COMMUNITY_Download one real and one abstract wallpaper from|Download one real and one abstract wallpaper from ]]
- [[_COMMUNITY_blend_weight_analysis.py|blend_weight_analysis.py]]
- [[_COMMUNITY_coinmetrics_flow_probe.py|coinmetrics_flow_probe.py]]
- [[_COMMUNITY_stats.json|stats.json]]
- [[_COMMUNITY_Fear & Greed index scraper.  Live source BMP Fear|Fear & Greed index scraper.  Live source: BMP Fear]]
- [[_COMMUNITY_Adaptive-normalisation probe.  Demonstrates, on re|Adaptive-normalisation probe.  Demonstrates, on re]]
- [[_COMMUNITY_Bitcoin Buy Risk Index|Bitcoin Buy Risk Index]]
- [[_COMMUNITY_dependencies|dependencies]]
- [[_COMMUNITY_Scraper for NUPL (Net Unrealized ProfitLoss) metr|Scraper for NUPL (Net Unrealized Profit/Loss) metr]]
- [[_COMMUNITY_Scraper for Bitcoin Rainbow Chart band metric.  So|Scraper for Bitcoin Rainbow Chart band metric.  So]]
- [[_COMMUNITY_US Yield Curve Spread (10Y-2Y) from FRED.|US Yield Curve Spread (10Y-2Y) from FRED.]]
- [[_COMMUNITY_calc_historical_mayer.py|calc_historical_mayer.py]]
- [[_COMMUNITY_permissions|permissions]]
- [[_COMMUNITY_launch.json|launch.json]]
- [[_COMMUNITY_Market Equilibrium  Pendulum Theory|Market Equilibrium / Pendulum Theory]]
- [[_COMMUNITY_Dynamic CSS scale() for mobile viewport fit at 908|Dynamic CSS scale() for mobile viewport fit at 908]]
- [[_COMMUNITY_Playwright BMP scraping (mm_utils.py)|Playwright BMP scraping (mm_utils.py)]]
- [[_COMMUNITY_SMC removed — 40% false bottom rate in bear market|SMC removed — 40% false bottom rate in bear market]]
- [[_COMMUNITY_Save Fear & Greed history from BMP chart to datah|Save Fear & Greed history from BMP chart to data/h]]
- [[_COMMUNITY_Save Global M2 series from BMP Global Liquidity ch|Save Global M2 series from BMP Global Liquidity ch]]
- [[_COMMUNITY_DXY removed — superseded by US 10Y TIPS Real Yield|DXY removed — superseded by US 10Y TIPS Real Yield]]
- [[_COMMUNITY_Geopolitical Risk removed — 10% explanatory weigh|Geopolitical Risk removed — <10% explanatory weigh]]
- [[_COMMUNITY_Fear & Greed neutral at 2025 ATH — reduced retail|Fear & Greed neutral at 2025 ATH — reduced retail ]]
- [[_COMMUNITY_pandas dependency|pandas dependency]]
- [[_COMMUNITY_requests==2.31.0 dependency|requests==2.31.0 dependency]]
- [[_COMMUNITY_Custom Card Playground (custom-card.html)|Custom Card Playground (custom-card.html)]]
- [[_COMMUNITY_robots.txt — allow all, sitemap at btcbri.comsite|robots.txt — allow all, sitemap at btcbri.com/site]]

## God Nodes (most connected - your core abstractions)
1. `run()` - 23 edges
2. `build_slider_map()` - 17 edges
3. `metrics` - 16 edges
4. `source` - 16 edges
5. `metrics` - 16 edges
6. `source` - 16 edges
7. `updated` - 15 edges
8. `updated` - 15 edges
9. `main()` - 13 edges
10. `get_bmp_trace()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `Peak score declining across cycles (91→86→82→68)` --rationale_for--> `Adaptive Calibration — rolling 4-year percentile rank`  [INFERRED]
  docs/retrospective_analysis.md → CLAUDE.md
- `score_with_ry()` --calls--> `weighted_score()`  [EXTRACTED]
  tools/backtest_fast.py → scraper/scoring.py
- `Tech/Macro Metric Group (50% weight)` --references--> `Cipher B — WaveTrend + MFI momentum oscillator`  [EXTRACTED]
  CLAUDE.md → docs/README.md
- `SOPR removed — weak differentiation, high noise` --conceptually_related_to--> `aSOPR — Adjusted Spent Output Profit Ratio`  [INFERRED]
  docs/README.md → CLAUDE.md
- `main()` --calls--> `fetch_ohlcv_kraken()`  [EXTRACTED]
  scratch/backtest_cipherb_variations.py → scraper/cipherb.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **CI/CD Data Pipeline: scraper → data.json → GitHub Pages** — github_workflows_update_data_yml, claude_md_data_json, claude_md_github_pages_frontend [EXTRACTED 1.00]
- **Composite Scoring: On-Chain + Tech/Macro → Final Risk Score** — claude_md_onchain_group, claude_md_tech_macro_group, claude_md_scoring_engine [EXTRACTED 1.00]
- **Dashboard Theme Variants: index routes to minimal, classic, modern** — web_index_dashboard, web_minimal_dashboard, web_classic_dashboard [EXTRACTED 1.00]

## Communities (60 total, 13 thin omitted)

### Community 0 - "backtest.py"
Cohesion: 0.05
Nodes (65): _adaptive(), build_slider_map(), compute_scores(), _load_metric_history(), map_asopr(), map_cvdd(), map_etf_flow(), map_fear_greed() (+57 more)

### Community 1 - "value"
Cohesion: 0.06
Nodes (69): adaptive_calibration, mayer, asopr, btc_dominance, btc_price, cvdd_ratio, fear_greed, fear_greed_label (+61 more)

### Community 2 - "value"
Cohesion: 0.07
Nodes (64): adaptive_calibration, mayer, asopr, btc_dominance, btc_price, cvdd_ratio, etf_flows, fear_greed (+56 more)

### Community 3 - "get_bmp_trace()"
Cohesion: 0.10
Nodes (21): calculate_7d_sma(), get_asopr(), Scraper for aSOPR (Adjusted Spent Output Profit Ratio) metric., Return 7-day SMA of aSOPR., get_cvdd_ratio(), Scraper for CVDD (Coindays Destroyed Value) proximity metric.  Source: https://w, Return BTC price / CVDD as float, e.g. 1.64.      Fetches both BTC Price and CVD, get_bmp_trace() (+13 more)

### Community 4 - "Tech/Macro Metric Group (50% weight)"
Cohesion: 0.09
Nodes (26): Adaptive Calibration — rolling 4-year percentile rank, On-Chain Metric Group (50% weight), Scoring Engine (scraper/scoring.py), Tech/Macro Metric Group (50% weight), aSOPR — Adjusted Spent Output Profit Ratio, CVDD Ratio, ETF Flows (14-day rolling net sum), Fear & Greed Index (+18 more)

### Community 5 - "cipherb.py"
Cohesion: 0.22
Nodes (17): _bearish_divergence(), _bullish_divergence(), compute_cipherb_from_ohlcv(), _divergence(), ema(), _fast_divergence(), fetch_ohlcv_kraken(), get_cipherb() (+9 more)

### Community 6 - "BitcoinScore Project"
Cohesion: 0.13
Nodes (17): BitcoinScore Project, build_payload() — metric collection entry point, 24-hour metric cache with FORCE_LIVE override, compute_scores() — scoring engine call, compute_zone_forecast() — buy/sell price levels, data/history/daily_vector.json — daily run log, data/data.json — final output file, GitHub Pages Static Frontend (web/) (+9 more)

### Community 7 - "generate_ics.py"
Cohesion: 0.17
Nodes (14): find_entry_nearest(), fold(), ics_escape(), load_history(), main(), patch_data_json(), Generate web/bitcoin_buy_risk.ics — a subscribable iCal feed of daily index valu, Return the entry whose date is closest to (and not after) target_date, or None. (+6 more)

### Community 8 - "scraper.py"
Cohesion: 0.27
Nodes (10): get_price(), build_payload(), main(), now_iso(), human_visit(), is_valid_metric(), Basic validation for metric values.     Returns True if value looks sane, False, Open a Playwright browser window to `url`, simulate light human actions, wait, t (+2 more)

### Community 9 - "@claude-flow/embeddings"
Cohesion: 0.27
Nodes (12): commands, enabled, hooks, installedAt, name, path, source, lastUpdated (+4 more)

### Community 10 - "cmc.py"
Cohesion: 0.29
Nodes (11): available(), get_btc_price(), get_fear_greed(), get_global_metrics(), _headers(), _key(), CoinMarketCap Pro API client.  Provides three data groups:   - Fear & Greed inde, Return dict with global market data, or None on error.      Keys: btc_dominance (+3 more)

### Community 11 - "session-1780856233859.json"
Cohesion: 0.17
Nodes (11): context, cwd, duration, endedAt, id, metrics, commands, edits (+3 more)

### Community 12 - "session-1780856237105.json"
Cohesion: 0.17
Nodes (11): context, cwd, duration, endedAt, id, metrics, commands, edits (+3 more)

### Community 13 - "retry_stale_metrics.py"
Cohesion: 0.27
Nodes (11): _build_metric_specs(), find_stale_metrics(), load_data(), main(), _metric_age_hours(), _now_iso(), Return metric names that are either missing or older than threshold_hours., Fetch each stale metric and update `data` in-place. Returns {name: success}. (+3 more)

### Community 14 - "current.json"
Cohesion: 0.18
Nodes (10): context, cwd, id, metrics, commands, edits, errors, tasks (+2 more)

### Community 15 - "zone_forecast.py"
Cohesion: 0.27
Nodes (8): compute_zone_forecast(), Zone-price inversion: at what BTC price would the index reach the Buy (<=20) or, Binary-search the price multiplier where final_score crosses threshold.     scor, Return the inverted Buy/Sell zone prices for the current data state., metrics for hypothetical price = price0 * k.     direction: 'down' (capitulation, _rescale(), _score(), _solve()

### Community 16 - "zone_forecast"
Cohesion: 0.28
Nodes (9): pct, price, zone_forecast, buy, buy_threshold, realized_price, ref_price, sell (+1 more)

### Community 17 - "funding_rate.py"
Cohesion: 0.39
Nodes (8): _from_binance(), _from_bybit(), _from_kraken_futures(), _get(), get_funding_rate(), Bybit Futures Funding Rate for BTCUSDT (primary), Binance fallback.  Score mappi, Kraken Futures perpetual funding rate.      Kraken returns fundingRate as an ann, Return funding rate dict or None on failure.      Source priority:       1. Krak

### Community 18 - "cipherb_band_backtest.py"
Cohesion: 0.36
Nodes (7): A(), B(), clamp(), D(), E(), G(), Cipher B score-band backtest.  Question: can Cipher B's risk score be improved o

### Community 19 - "zone_forecast"
Cohesion: 0.28
Nodes (9): pct, price, zone_forecast, buy, buy_threshold, realized_price, ref_price, sell (+1 more)

### Community 20 - "cvdd_history.json"
Cohesion: 0.25
Nodes (7): first, last, metric, n, note, series, source

### Community 21 - "mayer_history.json"
Cohesion: 0.25
Nodes (7): first, last, metric, n, note, series, source

### Community 22 - "mvrv_history.json"
Cohesion: 0.25
Nodes (7): first, last, metric, n, note, series, source

### Community 23 - "nupl_history.json"
Cohesion: 0.25
Nodes (7): first, last, metric, n, note, series, source

### Community 24 - "rhodl_history.json"
Cohesion: 0.25
Nodes (7): first, last, metric, n, note, series, source

### Community 25 - "Scraper for Global M2 liquidity signal.  Primary S"
Cohesion: 0.32
Nodes (7): _fetch_fred_fallback(), _fetch_wm2ns_series(), get_m2(), Scraper for Global M2 liquidity signal.  Primary Source: MacroMicro chart 3439 (, Fetch full WM2NS weekly series from FRED as fallback. Returns list of (date_str,, Calculate US M2 YoY % change from FRED as fallback., Return M2 year-over-year % change.          Tries MacroMicro Global M2 YoY in US

### Community 26 - "Pi Cycle Top Indicator — 111DMA vs 2×350DMA on dai"
Cohesion: 0.32
Nodes (7): compute_pi_cycle(), fetch_daily_closes(), get_pi_cycle(), Pi Cycle Top Indicator — 111DMA vs 2×350DMA on daily BTC/USD (Kraken).  Score ma, Fetch daily BTC/USD closing prices from Kraken (up to ~720 bars)., Compute Pi Cycle Top values from a list of closing prices., Return Pi Cycle dict or None on failure.

### Community 27 - "ML weight probe — honest feasibility check (NOT a "
Cohesion: 0.32
Nodes (6): closest(), m2_yoy(), parse(), ML weight probe — honest feasibility check (NOT a production model).  Question:, series: list of dicts; return value at date closest to target., YoY % change of the global-M2 level around target date.

### Community 28 - "buyzone_price_solver.py"
Cohesion: 0.48
Nodes (6): main(), "At what BTC price does the index enter the Buy zone (<=20)?" solver.  The index, Return a metrics dict for hypothetical price = price0 * k., rescale(), score_at(), solve()

### Community 29 - "Scraper for MVRV Z-Score metric."
Cohesion: 0.40
Nodes (5): get_mvrv(), _get_mvrv_from_bitcoinmagazine(), Scraper for MVRV Z-Score metric., Fetch MVRV Z-Score from BitcoinMagazinePro Plotly chart. Returns float or None., Return MVRV Z-Score as float, e.g. 0.80.      Source: BitcoinMagazinePro Plotly

### Community 30 - "Download one real and one abstract wallpaper from "
Cohesion: 0.47
Nodes (5): _fetch_pexels_photo(), Download one real and one abstract wallpaper from Pexels based on the     curren, Search Pexels for one photo and return its raw JPEG bytes, or None on failure., _score_to_category(), update_wallpapers()

### Community 31 - "blend_weight_analysis.py"
Cohesion: 0.40
Nodes (3): _m2yoy(), On-chain / Tech-Macro blend-weight analysis.  Question: is the 50/50 final blend, TECH()

### Community 32 - "coinmetrics_flow_probe.py"
Cohesion: 0.53
Nodes (5): f(), fetch(), main(), Coin Metrics market-wide money-flow probe (free Community API, no key).  Questio, rma()

### Community 33 - "stats.json"
Cohesion: 0.40
Nodes (4): lastAdaptation, patternsLearned, signalsProcessed, trajectoriesRecorded

### Community 34 - "Fear & Greed index scraper.  Live source: BMP Fear"
Cohesion: 0.50
Nodes (4): get_fear_greed(), Fear & Greed index scraper.  Live source: BMP Fear & Greed chart — Playwright sc, Return {'value': int, 'label': str} for current Fear & Greed index.      Scrapes, _score_to_label()

### Community 35 - "Adaptive-normalisation probe.  Demonstrates, on re"
Cohesion: 0.60
Nodes (4): adaptive_percentile(), main(), parse(), Adaptive-normalisation probe.  Demonstrates, on real multi-cycle history, why FI

### Community 36 - "Bitcoin Buy Risk Index"
Cohesion: 0.70
Nodes (5): Bitcoin Buy Risk Index, Index Overview Sparkline Chart, og_preview.jpg - Bitcoin Buy Risk Index Mobile UI Preview (JPEG), Mobile Trading App Dashboard UI, Risk Score Gauge (0-100 scale, current score 32 LOW RISK)

### Community 37 - "dependencies"
Cohesion: 0.50
Nodes (3): dependencies, @claude-flow/embeddings, @claude-flow/neural

### Community 38 - "Scraper for NUPL (Net Unrealized Profit/Loss) metr"
Cohesion: 0.50
Nodes (3): get_nupl(), Scraper for NUPL (Net Unrealized Profit/Loss) metric., Return NUPL as a percentage (float), e.g. 30.75.

### Community 39 - "Scraper for Bitcoin Rainbow Chart band metric.  So"
Cohesion: 0.50
Nodes (3): get_rainbow_band(), Scraper for Bitcoin Rainbow Chart band metric.  Source: https://www.bitcoinmagaz, Return current rainbow band info for BTC price.      Returns dict with keys:

### Community 40 - "US Yield Curve Spread (10Y-2Y) from FRED."
Cohesion: 0.50
Nodes (3): get_yield_curve(), US Yield Curve Spread (10Y-2Y) from FRED., Return current US Yield Curve Spread % (T10Y2Y, FRED). Returns None on failure.

### Community 41 - "calc_historical_mayer.py"
Cohesion: 0.83
Nodes (3): calculate_mayer_multiples(), fetch_all_daily_prices(), main()

### Community 44 - "Market Equilibrium / Pendulum Theory"
Cohesion: 0.67
Nodes (3): Howard Marks — Mastering the Market Cycle, Market Equilibrium / Pendulum Theory, Bitcoin Power Law Theory — Giovanni Santostasi

### Community 45 - "Dynamic CSS scale() for mobile viewport fit at 908"
Cohesion: 0.67
Nodes (3): Neo2 Dashboard with dynamic viewport scaling (neo2.html), Dynamic CSS scale() for mobile viewport fit at 908px, Neo (Modern Preview) Dashboard (neo.html)

## Knowledge Gaps
- **148 isolated node(s):** `trajectoriesRecorded`, `patternsLearned`, `signalsProcessed`, `lastAdaptation`, `lastUpdated` (+143 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.