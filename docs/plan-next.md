# BitcoinScore — Next Implementation Plan

**Version:** v4.4 (next)
**Branch:** main
**Date:** 2026-07-05
**Status:** CEO Review APPROVED with fixes (2026-07-05)

## CEO Review Decisions (applied)

1. **In-sample labeling (CRITICAL):** bucket_returns table must display "Based on V3 algorithm applied retroactively to historical data — not independent validation." Label the `generated` field in JSON as `"backtest_type": "in_sample"`. The 92% figure is real but was computed by the same model that learned from this data.

2. **PHASE_STUCK threshold calibration (CRITICAL):** Before shipping drift_monitor, compute the actual distribution of phase durations from `data/history/phase_history.json` and historical scores. Current tiz_days=127 already exceeds the proposed 90-day threshold — the alert fires on day 1. Either raise the threshold to the 90th percentile of historical durations, or suppress PHASE_STUCK when normalized metrics still support the current phase (high-metric-agreement case).

3. **Tail-risk framing in UI (MEDIUM):** The bucket_returns table should include a "loss scenarios" row showing: how many periods in the bucket had negative 365d returns and what the median loss was in those periods. e.g., "8 out of 92 periods were negative (median: -28%). Losses clustered around post-Fed-rate-hike regimes."

---

## Problem Statement

BitcoinScore shows a score (currently 23/100, BOTTOM phase) but the user has no historical context to trust it. When the score is low, the emotional barrier to buying remains because there's no evidence of what historically happened after similar scores. The missing piece is **historical proof**: what did BTC price actually do in the months after the score was in each range?

Additionally, the system lacks **drift monitoring**: no automated check exists to detect if the model's score is drifting without changing metrics (silent model degradation), if the HMM is stuck in one phase too long, or if input metrics are going out-of-distribution (OOD).

---

## Scope

### Feature 1: Bucket Returns (Historical Proof) — P0 / Approved

**Problem:** Score 23/100 is shown, but user doesn't know if scores in this range historically preceded positive or negative BTC returns.

**Solution:** Compute forward BTC returns by score bucket (10-point buckets), display results in `web/minimal.html` as a stats table.

**Implementation:**

#### Step 1 — `tools/bucket_returns.py`
- Read `data/history/scores.json` (3,106 daily entries as of 2026-07-04)
- Pre-build a sorted date index using `bisect.bisect_left` (O(log n) lookups)
- For each entry + each horizon (30/90/180/365d): find closest entry to `date + N days`. Exclude if >3 days gap. Apply symmetric cutoff: only include entries where `date <= last_date - N days`. This applies to ALL 4 horizons, not just 365d.
- Group by 10-point bucket: `[low, high)` boundaries. Score 20 in [20,30), score 30 in [30,40). Score 100 in [90,100].
- Known: [0,10)=0 entries, [10,20)=1 entry, [90,100]=0 entries — all suppressed.
- Compute per bucket per horizon: median, p25, p75, % positive (denominator = horizon-valid n only)
- Schema validation at startup: confirm scores.json is a list with `date` and `final_score` keys
- Suppress buckets with n < 8 (log suppression reason at generation time)
- Write `data/bucket_returns.json` per schema below
- Run standalone: `python tools/bucket_returns.py`

**Output schema (`data/bucket_returns.json`):**
```json
{
  "generated": "2026-07-05T10:00:00Z",
  "buckets": [
    {
      "label": "20–30",
      "range_low": 20,
      "range_high": 30,
      "n": 516,
      "median_30d": 4.2,
      "median_90d": 18.7,
      "median_180d": 41.3,
      "median_365d": 75.5,
      "pct_positive_30d": 61,
      "pct_positive_90d": 71,
      "pct_positive_180d": 78,
      "pct_positive_365d": 92,
      "n_30d": 516,
      "n_90d": 510,
      "n_180d": 499,
      "n_365d": 435,
      "suppressed": false
    }
  ]
}
```

Known results from pilot run:
- Score 20–30: n=516, median_365d=+75.5%, pct_positive_365d=92%
- Score 30–40: n=444, median_365d=+92.2%, pct_positive_365d=87%
- Score 70–80: n=483, median_365d=-1.6%, pct_positive_365d=49%
- Score 80–90: n=399, median_365d=-5.5%, pct_positive_365d=47%

#### Step 2 — `web/minimal.html` table (Design decisions locked)
- **Placement:** 4th back panel on the existing flip card (after Forecast, On-Chain, Tech/Macro). Triggered by tapping the score area. Follows existing `#back-forecast` pattern exactly.
- Fetch `bucket_returns.json` on page load (parallel to `data.json`, same error handling)
- **Columns:** Score Range / n / 30d / 90d / 365d / % positive 365d (drop 180d on mobile <480px via nth-child)
- **Active row** (matching today's score bucket): triple signal — left border `2px solid var(--text)`, `font-weight: 600`, "▶ " prefix on score range label
- **Suppressed rows** (n < 8): removed from DOM (not greyed-out rows)
- **States:** Loading → centered "Loading…"; Error → "Could not load historical data"; All-suppressed → "Insufficient data for this view"
- **Disclaimer:** Caption below table title, muted text: "In-sample backtest — V3 algorithm applied retroactively. Not independent validation."
- **Tail-risk row (CEO requirement):** `<tfoot>` row spanning return columns: "N periods were negative (median loss: -X%). Losses concentrated in [regime]."
- **Semantic markup:** `<th scope="col">` headers, `<th scope="row">` for score range, `aria-current="true"` on active row, `user-select: text` on table
- **Bilingual:** EN-only at launch. UA translations deferred to follow-up (strings documented in CEO decisions section above).

#### Step 3 — Wire into `update-data.yml`
- After `python tools/retry_stale_metrics.py`, add: `python tools/bucket_returns.py`
- In the copy step, add: `cp data/bucket_returns.json web/bucket_returns.json`

#### Step 4 — Verify
- Run `python tools/bucket_returns.py` locally, inspect output JSON
- Open `web/minimal.html` in browser, verify table renders with current score row highlighted
- Run `python tools/backtest_fast.py` to confirm scraper still passes

**Affected files:**
- `tools/bucket_returns.py` (new)
- `data/bucket_returns.json` (generated)
- `web/bucket_returns.json` (served)
- `web/minimal.html` (UI addition)
- `.github/workflows/update-data.yml` (CI addition)

---

### Feature 2: Drift Monitor — P0

**Problem:** No automated check exists for:
1. Model drift — score changes without metric changes (silent model degradation)
2. HMM stuck in one phase > 90 days (no regime transition detected)
3. Out-of-distribution (OOD) metrics — input values outside the training distribution

**Solution:** `tools/drift_monitor.py` — runs in CI after scraper, writes alerts to stdout and `data/drift_alerts.json`.

**Checks to implement (v1 scope, post-eng review):**

1. **Score stability check (MODEL_DRIFT)**: compare today's `final_score` vs yesterday's data.json. If delta > 15 points AND all individual `v3_normalized_scores` changed < 5 points, flag `MODEL_DRIFT`. Read yesterday from `data/history/scores.json[-2]` (second-to-last entry).

2. **HMM phase lock check (PHASE_STUCK):** REMOVED from v1. `v3_hmm_state_cache.json` is empty — no valid data source. `phase_history.json` has only 16 entries from the current phase with no multi-cycle data. Threshold of 90 days cannot be calibrated. Re-enable when multi-cycle phase_history accumulates (TODO: review after 2027).

3. **OOD metric check**: for each score in `v3_normalized_scores`, compute [5, 95] percentile from the last 4 years of `data/history/daily_vector.json` for that metric. Flag `OOD_HIGH` (> 95th) or `OOD_LOW` (< 5th). Use `bisect.bisect_left` on sorted per-metric value list.

4. **Error handling:** drift_monitor ALWAYS exits 0. On any internal error, write `{"ok": false, "alerts": [{"type": "SCRIPT_ERROR", "message": "..."}]}` to `data/drift_alerts.json`. Remove `|| true` from CI — the script handles its own errors gracefully.

**Output `data/drift_alerts.json`:**
```json
{
  "generated": "2026-07-05T10:00:00Z",
  "alerts": [
    {
      "type": "PHASE_STUCK",
      "severity": "warn",
      "message": "HMM in BOTTOM phase for 210 days (threshold: 90)",
      "days": 210
    }
  ],
  "ok": false
}
```

**CI integration (`update-data.yml`):** Add step after retry_stale_metrics:
```yaml
- name: Drift monitor
  if: steps.freshness.outputs.fresh == 'false'
  run: python tools/drift_monitor.py
```
Note: `|| true` removed. The script always exits 0 and handles errors internally.

**Affected files:**
- `tools/drift_monitor.py` (new)
- `data/drift_alerts.json` (generated)
- `.github/workflows/update-data.yml` (1 step added)

---

## Architecture Considerations

### Scoring pipeline integrity
- `bucket_returns.py` is READ-ONLY with respect to the scoring pipeline — it reads `scores.json` and writes a separate JSON file. No coupling to `scoring_pipeline.py` or V3.
- `drift_monitor.py` is also READ-ONLY — reads `data.json` and `v3_hmm_state_cache.json`, writes `drift_alerts.json`.
- Neither feature modifies the scoring pipeline, HMM model, or normalizer.

### Data dependencies
- `bucket_returns.py` requires `data/history/scores.json` (exists, 3,098 entries)
- `drift_monitor.py` requires `data/data.json` (today's data) and `data/v3_hmm_state_cache.json` (HMM state)
- Both can run without network access

### Known data quality issues
- ETF flows: Farside last updated Jun 05 (stale). This affects `etf_flows` metric but does NOT affect bucket_returns (historical data is frozen) or drift_monitor (it reads current normalized scores which already reflect the stale ETF value).
- M2 YoY: Updates infrequently (MacroMicro scraping via Zyte proxy). Same non-impact on new features.

### Historical data caveat
The `scores.json` history uses V3 applied retroactively to historical metric data — NOT live scores from the time. The bucket_returns table should note this caveat: "Based on current scoring algorithm applied retroactively."

---

## Test Plan

### bucket_returns.py
- `python tools/bucket_returns.py` — verify all buckets with n >= 8 appear, suppressed=false; 0-10 bucket likely suppressed (few BOTTOM extremes in history)
- Verify [low, high) boundary: score=20 lands in [20,30), score=30 lands in [30,40)
- Verify pct_positive denominator = horizon-valid n (not total n)
- Run `python tools/backtest_fast.py` — must still pass (5 key dates)

### drift_monitor.py
- `python tools/drift_monitor.py` — should output PHASE_STUCK alert (BOTTOM since ~Dec 2025)
- Verify `data/drift_alerts.json` written with correct schema
- Verify `ok: false` when alerts exist, `ok: true` when none

### Regression
- Run `python tools/backtest.py` (16 historical dates) — all must pass after both features added
- Verify `web/minimal.html` renders bucket_returns table in browser

---

## Success Criteria

**bucket_returns:**
- Open minimal.html when score is 23 → see "Score 20–30: historically up +75.5% median in 12 months, positive in 92% of periods (n=435)"
- That sentence eliminates the emotional uncertainty about whether now is the right time

**drift_monitor:**
- CI job runs daily, exits 0 (alerts written but script doesn't fail the pipeline with `|| true`)
- `data/drift_alerts.json` updated daily
- PHASE_STUCK alert visible when HMM stays in same phase > 90 days

---

## Not in Scope

- Ensemble uncertainty (P1 — separate implementation, 5+ files, different session)
- Regime fingerprint (P1 — requires cosine similarity against known regimes, ML training)
- ETF fallback scraper (P2 — Farside stale but not blocking anything)
- M2 auto-update (P2 — MacroMicro scraping reliability, separate project)
- Frontend redesign beyond the minimal.html table addition
- New data sources or API integrations

---

## Deployment

Daily cron on DietPi (06:00) runs the pipeline → `bucket_returns.py` and `drift_monitor.py` run as new steps → outputs committed to git → GitHub Pages serves updated JSON. GitHub Actions CI (08:00 UTC fallback) also runs both steps.

No new infrastructure. No new API keys. No new secrets.
<!-- /autoplan restore point: /Users/max/.gstack/projects/heavymetalmax-BitcoinScore/main-autoplan-restore-20260705-105646.md -->
