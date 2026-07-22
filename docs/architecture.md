# BitcoinScore — Architecture & Calculation Reference

**Version:** V3.1 + V5A.17 + V5B.1  
**Updated:** 2026-07-21

---

## 1. What It Is

BitcoinScore is a daily-updated Bitcoin risk engine with two models and one actionable output:

| Layer | Question answered | Role |
|---|---|---|
| **V5B (Outlook)** | What is the expected max drawdown over the next 365 days? | **Prediction Layer** — forecasts future downside risk |
| **Market Regime** | Is this a Buy / Sell / Hold / Wait moment? | **Decision Layer** — the only field to act on; combines Outlook + Market Context |
| **V3 (Market Context)** | Where are we in the Bitcoin cycle, and how overheated/coherent are the underlying metrics? | **Explanation Layer** — explains why; prevents false exits |

```
Prediction Layer    (V5B / Outlook)
        ↓
Decision Layer      (Market Regime — Buy / Sell / Hold / Wait)
        ↑
Explanation Layer   (V3 / Market Context)
```

**Design philosophy:** BitcoinScore separates prediction from interpretation. V5B estimates future downside risk directly from market structure — it is the prediction layer. V3 does not predict returns; it assesses cycle phase, overheating, and cross-metric coherence to provide context that explains and filters the prediction. Market Regime combines both into a single human-readable decision state — the decision layer, and the only field a user should act on.

**Naming note:** "Market Regime" is kept as the field/section name for continuity with `data.json`'s `market_regime` field, even though its four states (Buy/Sell/Hold/Wait) describe a decision/action, not a market regime in the classic Bull/Bear/Risk-On/Risk-Off sense. Likewise, V3 is referred to here as "Market Context" rather than "Cycle Context" — it does more than locate cycle position; it also evaluates overheating, phase, cross-metric coherence, and utility weighting (see §5). Renaming these labels in the docs does not change the underlying `market_regime` / `final_score` field names in `data.json` or the dashboard code.

**V5B is the prediction layer.** V3 does not improve prediction of future drawdown once V5B's features are known — linear meta-regression coefficient = −0.007 (effectively zero). It is used only as a decision filter to reduce premature entries and exits, not because it independently predicts risk: it prevents false exits during mid-bull rallies where Outlook stays moderate but cycle position is still rising. V3 is retained as a filter inside Market Regime, not as a standalone trading signal.

**Meta Score, Signal Agreement, and Composite Risk** are computed but not surfaced in the UI — they are internal fields available in `data.json` for debugging and backtest tooling only.

Output: `data/data.json` served via GitHub Pages static dashboards.

---

## 2. Infrastructure

### Home Mac Mini (primary)
- Runs `~/run_bitcoinscore.sh` daily at 06:00 local time via cron
- Flow: `git pull` → `python -m scraper.scraper` → `git push`

### GitHub Actions (fallback, 08:00 UTC)
- `update-data.yml` checks `data/data.json` before running:
  - If `timestamp[:10] == today` **AND** `nupl is not None` **AND** `mvrv is not None` → skip (home server already ran correctly)
  - If timestamp is today but BMP metrics are null → scraper runs again (Playwright failed locally)
  - If timestamp is stale → scraper runs

This two-condition check ensures BMP metric failures on the home server trigger the fallback.

---

## 3. Data Sources

| Metrics | Source | Method |
|---|---|---|
| `btc_price` | Kraken OHLCV | REST |
| `nupl`, `mvrv`, `rhodl_ratio`, `cvdd_ratio`, `asopr` | Bitcoin Magazine Pro (BMP) | Playwright / headless Chromium |
| `puell_multiple`, `mayer_multiple`, `lth_supply_pct` | BMP | Playwright |
| `fear_greed` | alternative.me | REST |
| `etf_flows` | Farside | HTML parse |
| `yield_curve` | FRED | REST |
| `m2_yoy` | MacroMicro / FRED | Zyte proxy + REST |
| `cipherb` (weekly + daily oscillator) | Kraken OHLCV | Computed locally |
| `funding_rate` | Kraken | REST |
| `btc_dominance` | CoinGecko | REST |

### BMP Metrics (Playwright)
BMP metrics are scraped via `get_bmp_trace()` in `scraper/mm_utils.py`, which opens a headless Chromium instance, waits for the Plotly chart to render, and extracts the last data point. These are the most fragile metrics — if Playwright fails, they come back as `None`.

**Forward-fill policy:** `build_training_features.py` and `scraper/scoring_v3.py` forward-fill BMP metrics from the last known good value so the feature vector remains stable across scraping failures.

---

## 4. Normalizer — Raw → 0-100 Risk Score

Each raw metric is normalized to a 0–100 risk score via `scraper/normalizer.py`.

### Method priority (on-chain metrics)
1. **Cycle normalize** (`scraper/cycle_normalizer.py`) — anchored to confirmed historical cycle bottoms and tops. Hard-coded zero-points ensure the scale is structurally meaningful.
2. **Causal percentile** (fallback) — fraction of all historical values below current value, using only data up to `target_date` (no lookahead).

### Causal percentile (adaptive metrics)
```
pct_rolling = rank(value, 4yr window) / N_rolling
pct_alltime = rank(value, all data) / N_alltime
result = max(pct_rolling, pct_alltime)   # ratchet — can't drop during mid-cycle correction
```

### Risk orientation
All normalized scores are **risk-oriented**: high value = high risk.
Inverted metrics (higher raw = lower risk): `m2_yoy`, `yield_curve`, `lth_supply_pct`.

---

## 5. V3 Scoring Pipeline

**Entry point:** `scraper/scoring_v3.py → compute_scores_v3()`

### Step 1 — Normalize
All raw metrics → 0-100 via `normalize_metric()` (see §4).

### Step 2 — CipherB handling
CipherB has two sub-signals:
```
cipherb_score = 0.80 × weekly_score + 0.20 × daily_score
```
Bearish/bullish divergence flags are extracted separately and used to boost utility weight (not the score itself).

### Step 3 — Cycle phase detection (HMM)

Model: `data/v3_phase_model.pkl` — custom `HMMPhaseClassifier` trained by `tools/train_v3_hmm_model.py`.

Input: 22-dimensional wave vector of normalized metric values + 30d/90d lookback deltas.

Output: continuous mixture weights:
- `w_bot` — probability of BOTTOM phase
- `w_top` — probability of TOP phase  
- `w_neutral = 1 - w_bot - w_top`

Discrete `v3_phase`:
- `BOTTOM` if `w_bot > 0.5`
- `TOP` if `w_top > 0.5`
- `NEUTRAL` otherwise

State is carried forward via `data/v3_hmm_state_cache.json` (committed to git) so the daily HMM chain is continuous across CI runs.

Fallback when model unavailable: Mahalanobis distance to pre-computed centroids.

### Step 4 — Utility coefficients
`scraper/utility_evaluator.py → evaluate_all_utilities_continuous()`

Each metric gets a relevance weight `U_i ∈ [0.1, 1.0]` based on:
- Current phase (BOTTOM / NEUTRAL / TOP)
- How much the metric typically moves in this phase
- Divergence flags (e.g., bearish CipherB divergence → higher utility in NEUTRAL/TOP)

### Step 5 — Flat utility-weighted average
```
flat_avg = Σ(normalized[k] × U[k]) / Σ(U[k])   for all metrics k
```

Group sub-scores `oc_avg` and `tech_avg` are computed for display only — they do **not** feed into `flat_avg`.

Metric groups:
- **OC_GROUP** (on-chain): `nupl`, `mvrv_z_score`, `rhodl_ratio`, `cvdd_ratio`, `asopr`, `puell`, `lth_supply`
- **TECH_GROUP** (technical/macro): `cipherb`, `mayer_multiple`, `fear_greed`, `etf_flows`, `yield_curve_spread`, `m2_yoy`, `pi_gap`, `funding_rate`

### Step 6 — Time-in-Zone (TiZ) blend (BOTTOM phase only)
If `v3_phase == BOTTOM`:
```
final = (1.0 - 0.20 × w_bot) × flat_avg + 0.20 × w_bot × tiz_score
```
`tiz_score` measures how long we have been in BOTTOM zone (0–365 days). Longer time in accumulation → lower score (0.20 blend of `tiz_score` pulls final down).

TiZ is off in NEUTRAL/TOP phases — `final = flat_avg`.

### Step 7 — Coherence dampening
If on-chain metrics disagree significantly (high cross-metric standard deviation), `flat_avg` is dampened slightly toward 50. Prevents overconfident reads when indicators conflict.

---

## 6. ML Models

Both V5A and V5B use the same 49-feature vector and XGBoost architecture (`n_estimators=400, max_depth=5, lr=0.04`). They differ only in their training label and therefore answer different questions.

### Shared feature set (49 features)

| Group | Count | Description |
|---|---|---|
| PRICE_PCT | 1 | `pct_btc_price` — causal percentile of BTC price (ATH = 1.0) |
| MICRO | 13 | Risk-oriented causal percentile per metric (high = high risk) |
| BASKET HOT | 3 | Fraction of QC/DY/MC metrics in danger zone (>0.75) |
| BASKET COLD | 3 | Fraction of QC/DY/MC metrics in safe zone (<0.25) |
| DIVERGENCE | 3 | Cross-cluster signed diffs: `dy_vs_qc`, `mc_vs_qc`, `all_spread` |
| COHERENCE | 2 | Intra-cluster standard deviation: `qc_std`, `dy_std` |
| EXTREME | 3 | Global consensus: `count_danger`, `count_safe`, `extreme_bias` |
| VELOCITY | 7 | 90d/180d basket deltas + `delta_w_bot_90d` |
| PRICE_DIV | 3 | Price ahead of on-chain: `price_vs_qc`, `price_vs_dy`, `price_vs_all` |
| V3_PHASE | 11 | `w_top`, `w_bot`, `v3_score`, flags, 30d velocity, peaks, `ath_divergence`, `ath_v3_divergence` |

**Metric taxonomy:**
- **QC** (quantitative / cycle position): `nupl`, `mvrv`, `rhodl_ratio`, `cvdd_ratio`, `puell`
- **DY** (dynamic / flow): `cipherb_daily`, `mayer_multiple`, `fear_greed`, `funding_rate`
- **MC** (macro / backdrop): `m2_yoy`*, `yield_curve`*, `dxy`   (* inverted = lower raw → higher risk)
- **Standalone:** `lth_supply_pct`*

**Key compound features:**
- `ath_divergence = pct_btc_price × max(0, w_top_peak_90d − w_top)` — price at ATH while TOP phase fades; was 0.532 at $124k 2025 peak and is the #1 feature for V5B
- `price_vs_qc = pct_btc_price − qc_avg` — price running ahead of on-chain = bearish divergence

**Velocity smoothing:** basket averages at lookback dates use a ±3-day window to prevent single-day gaps from creating discontinuities in velocity features.

**Missing value handling:** BMP metrics (`rhodl_ratio`, `cvdd_ratio`, `puell`, `mayer_multiple`, `lth_supply_pct`, `asopr`) are forward-filled before feature computation. Remaining nulls → column median.

---

### 6A. V5B — Forward Risk Model (v5b.1)

**Entry point:** `scraper/mixing_model_b.py → predict_b()`  
**Model file:** `data/v5b_model.pkl`  
**Output field:** `v5b_score`

**Question answered:** *"What is the expected maximum drawdown over the next 365 days if I hold BTC from today?"*

**Label:**
```
label = max(0, (price_today − min_price_next_365d) / price_today) × 100
```
- Not computable from current features → genuine ML task, no circular dependence
- Training data: 2017-08-17 to ~2025-07-19 (last ~30 rows lack 365d future window)
- Label range: 0% (perfect buy — price only rises) to ~83% (major bear market)

**Training performance:** MAE = 13.2%, RMSE = 16.5%, Extreme MAE = 14.3%

**Top feature importances:** `ath_divergence` (0.35), `w_top_vs_peak` (0.06), `phase_is_top` (0.05), `qc_cold` (0.05)

**Key cycle validation:**

| Date | BTC | V5B predicted | Actual drawdown |
|---|---|---|---|
| Dec 2018 bottom | $3,200 | 2.9% | 0.0% |
| Jun 2019 peak | $13,100 | 60.6% | 63.3% |
| Nov 2021 ATH | $64,900 | 75.0% | 75.5% |
| FTX bottom | $15,800 | 0.2% | 0.0% |
| Mar 2024 ATH | $71,400 | 24.0% | — (BTC went to $124k) |

Note: Mar 2024 ATH case — V5B correctly assessed moderate risk (24%), while V3 called 83 (sell). BTC subsequently rallied to $119–124k. V5B was right.

**Practical interpretation (all-in / all-out strategy):**

| V5B | Regime signal |
|---|---|
| < 20% | **BUY** — confirmed entry (backtest-optimal threshold) |
| 20–45% | **WAIT** — approaching buy threshold; stay out |
| ≥ 45% | **SELL** — exit trigger (combined with Position ≥ 65 for full Config A) |
| > 65% | High risk — major bear market historically follows |

Single-threshold alternative (183× backtest): Outlook ≤ 20% → BUY, ≥ 45% → SELL.  
See §7 for dual-threshold Config A (163× with Position confirmation).

**Retrain:**
```bash
python3 tools/build_v5b_labels.py && python3 tools/train_v5b_model.py
```

**Historical backtest chart:** `web/v5b_chart.html`

---

### 6B. V5A — Structural Cycle Position (v5.17, legacy compatibility)

> ⚠️ **Known limitation:** The V5A label (`mean(pct_btc_price, pct_nupl, pct_mvrv, pct_rhodl_ratio) × 100`) is directly computable from 4 of its own input features → baseline MAE = 0 (tautology). V5A does not learn anything beyond that formula. It is retained in the pipeline for legacy continuity but should not be used as an independent signal.

**Entry point:** `scraper/mixing_model.py → predict()`  
**Model file:** `data/v5_mixing_model.pkl`  
**Output field:** `v5_score`

---

## 7. Signal Combination

**Computed in:** `scraper/scoring_v3.py`  
**UI-visible fields:** `v5b_score` (Outlook), `market_regime`, `final_score` (Market Context)  
**Internal/debug only:** `signal_agreement`, `composite_risk`, `meta_score`

### 7.1 Outlook (V5B) — prediction layer

Backtest finding (2018–2026): **Outlook alone is the most powerful single signal** for an all-in/all-out strategy.

```
Outlook ≤ 20%  → entry zone   (183× total return vs 4.8× buy-and-hold)
Outlook ≥ 45%  → exit zone
otherwise       → stay out
```

Market Context (V3) does not improve prediction of future drawdown once V5B's features are known — V5B was trained on 49 features that already include all V3 phase outputs (linear meta-regression coefficient = −0.007, effectively zero). It is used only as a decision filter (§7.2), not because it independently predicts risk.

### 7.2 Market Regime — decision layer

The only field to act on. Combines Outlook (prediction layer) and Market Context (protective filter).

```
Market Context < 35  AND  Outlook < 20%  → Buy   (confirmed entry — both agree)
Market Context ≥ 65  AND  Outlook ≥ 45%  → Sell  (confirmed exit — both agree)
Market Context ≥ 65  AND  Outlook < 45%  → Hold  (in market; rally may continue)
otherwise                                → Wait  (out; entry not yet safe by Outlook)
```

Backtest result: **163× return, 6 trades in 8 years.**

| Market Context | Outlook | Regime | Meaning |
|---|---|---|---|
| < 35 | < 20% | **Buy** | Confirmed: enter |
| ≥ 65 | ≥ 45% | **Sell** | Confirmed: exit |
| ≥ 65 | < 45% | **Hold** | In market: stay; late bull may continue |
| other | ≥ 20% | **Wait** | Out of market: entry not yet safe |

**`Wait` vs `Hold` — the distinction that matters most:**

| State | Position | Meaning |
|---|---|---|
| `Wait` | No position | Do not enter yet — entry criteria not yet met |
| `Hold` | Already invested | Do not exit — rally may still continue |

These are opposite instructions that happen to look similar in a status readout — `Wait` is easy to misread as "pause, do nothing" when in fact it means "stay out entirely." A user reading `Wait` should have no BTC position; a user reading `Hold` should keep the position they already have.

**On the thresholds (35 / 65 / 20% / 45%):** these were selected through historical optimization on 2018–2026 backtest data (§7.3) and are not derived from a closed-form model — they may be revised in future versions as more cycle data becomes available.

**Why Market Context as a filter?** At mid-cycle ATHs (e.g. Mar 2024, $71k), Outlook stays moderate while the cycle is still ascending. Without the Market Context ≥ 65 gate, the Sell threshold would never fire anyway in that scenario — but the Hold branch explicitly keeps holders in. The filter's real value is in preventing ambiguous Wait→Sell transitions based on Outlook noise alone.

### 7.3 Backtest — Key Events

| Date | BTC | Market Context | Outlook | Regime | Action |
|---|---|---|---|---|---|
| Nov 2018 | $3,900 | 11 | 16.9% | **Buy** | Enter |
| Jun 2019 | $9,500 | 70 | 50% | **Sell** | Exit |
| Mar 2020 | $4,800 | 30 | 4.9% | **Buy** | Enter |
| Mar 2021 | $61k | 93 | 50% | **Sell** | Exit |
| Jun 2022 | $19.9k | 15 | 19.9% | **Buy** | Enter |
| May 2025 | $105k | 82 | 53% | **Sell** | Exit |
| Jul 2026 | ~$65k | ~34 | ~22% | **Wait** | Outlook not yet ≤ 20% |

### 7.4 Case Study — Mar 2024 ATH ($71k)

Market Context=83 alone would imply sell. Outlook=24% said moderate risk. BTC subsequently rallied to $119–124k (+70%).

```
Market Context:  83   → elevated, but…
Outlook:        24%  → below 45% exit threshold
market_regime:  Hold → correctly: if in, stay in
```

No sell fired. The Hold branch of Market Regime correctly kept holders in through the full rally to May 2025 at $105k — where both thresholds aligned and Sell fired.

### 7.4b Case Study — May 2025 ATH ($105k) — Confirmed Agreement

The mirror case to §7.4: Market Context=82 and Outlook=53% cross their exit thresholds together instead of diverging.

```
Market Context:  82   → ≥ 65 gate met
Outlook:        53%  → ≥ 45% exit threshold met
market_regime:  Sell → confirmed exit — both models agree
```

Sell fired cleanly, with no Hold ambiguity. Read together, §7.4 and §7.4b show the system handles both outcomes correctly: it holds through disagreement at an extended-bull ATH (Mar 2024), and it exits cleanly when both models confirm a classic cycle top (May 2025).

### 7.5 Internal fields (debug only — not shown in UI)

| Field | Formula | Purpose |
|---|---|---|
| `composite_risk` | `√(Market Context × Outlook)` | Single-dial gauge shown in the pipeline diagram widget (`web/classic.html`); display only, not used in Market Regime logic |
| `signal_agreement` | `1 − \|Context − Outlook\| / 100` | Diagnostic: how much the two models agree |
| `meta_score` | Agreement-weighted blend of Context + Outlook | Deprecated aggregate; kept for backtest tooling |

These fields remain in `data.json` for debugging and historical analysis but are not surfaced in the dashboard UI. None of them feed into `market_regime` — that decision is made solely from Outlook and Market Context thresholds (§7.2).

---

## 8. Scoring Pipeline Orchestration

`scraper/scoring_pipeline.py` is the integration layer. Execution order:

1. **V1 scoring** (`scraper/scoring.py`) — legacy; produces `zone_forecast`, `commentary`, `adaptive_calibration`
2. **V3 scoring** (`scraper/scoring_v3.py`) — produces `final_score` (Market Context 0–100), `v5b_score` (Outlook %), `market_regime`, and internal fields (`composite_risk`, `signal_agreement`, `meta_score`)
3. **V3.2 Override block** — final step in `scoring_pipeline.py`; writes V3 outputs to top-level payload fields (`final_score`, `market_regime`, `v5b_score`, etc.)

`v1_score` has been removed from JSON output. Do not use V1 scores.

---

## 9. Output — data/data.json

Key fields:

| Field | Description |
|---|---|
**UI-visible (primary):**

| Field | Description |
|---|---|
| `v5b_score` | **Outlook** — expected max drawdown % over next 365 days (prediction layer output) |
| `market_regime` | **Buy / Sell / Hold / Wait** — decision layer output; the only field to act on |
| `final_score` | **Market Context** — V3 composite 0–100 (explanation layer; 0=bottom, 100=top) |
| `v3_normalized_scores` | Per-metric V3 risk scores (0–100) — shown in metric table |
| `onchain_score` / `tech_score` | V3 group averages (display only) |
| `btc_price` | BTC/USD from Kraken OHLCV close |
| `timestamp` | ISO 8601 UTC timestamp of this run |

**Internal / debug (in JSON, not in UI):**

| Field | Description |
|---|---|
| `composite_risk` | `√(Market Context × Outlook)` — single-dial gauge for the pipeline diagram widget (`web/classic.html`); display only, not used in decision logic |
| `signal_agreement` | `1 − \|Context − Outlook\| / 100` — model agreement (0–1) |
| `meta_score` | Agreement-weighted blend — deprecated aggregate |
| `v5_score` | V5A ⚠️ tautological label — internal use only |
| `v3_score` | Raw V3 before pipeline overrides |
| `v3_phase` | `BOTTOM` / `NEUTRAL` / `TOP` |
| `v3_w_bot` / `v3_w_top` | HMM phase mixture weights |
| `v3_utilities` | Per-metric relevance weights (0–1) |
| `stale_metrics_count` | How many metrics used forward-filled (cached) values this run |
| `failed_live_fetches` | List of metric names that fell back to cached values |

---

## 10. History & State Files

All in `data/history/`:

| File | Content |
|---|---|
| `scores.json` | Per-day: `date`, `final_score`, `v3_phase`, `w_bot`, `w_top`, all raw metrics — used by V5 velocity features and HMM chaining |
| `daily_vector.json` | Append-only: raw scalars + 0-100 scores per day |
| `{metric}_history.json` | Seed series for adaptive calibration (NUPL, MVRV, RHODL, Mayer) |
| `v3_hmm_state_cache.json` | HMM forward-pass state — updated daily, committed to git |

`scores.json` is the primary input for V5 inference: `_get_lookback()` reads it to build velocity and phase features.

---

## 11. Adding a New Metric

1. Create `scraper/<metric>.py` with `get_<metric>() → scalar`
2. Add `normalize_<metric>()` in `scraper/normalizer.py`
3. Add to `OC_GROUP` or `TECH_GROUP` in `scraper/scoring_v3.py`
4. Wire fetch into `build_payload()` in `scraper/scraper.py`
5. Add relevance profile in `scraper/utility_evaluator.py`
6. Add to `QC_METRICS` / `DY_METRICS` / `MC_METRICS` in `scraper/mixing_model.py` and `tools/build_training_features.py`
7. Rebuild training data: `python tools/build_training_features.py`
8. Retrain V5: `python tools/train_mixing_model.py`
9. Verify: `python tools/backtest.py`
