# BTCBRI V5 Redesign — Engineering Specification & Step-by-Step Guide (for Coding Agent)

> **Target Repo:** `/Users/max/Projects/BitcoinScore` (branch: `main`)  
> **Goal:** Upgrade BitcoinScore to **BTCBRI V5** by solving 4 empirically proven weaknesses discovered in `docs/strategy_backtest_2026_11_24.md`:
> 1. **Decaying Cycle Peaks (ETF Era):** Modern tops (`2024-03` & `2025-10` @ `$124.7k`) have structurally lower on-chain ratios (`NUPL 58.5%`, `MVRV 2.78`, `CVDD 3.19`, `RHODL 6258`, `Puell 1.59`) than `2013/2017` (`MVRV 10.04`, `CVDD 20.15`), causing V4 `BTCBRI` to cap out at `79–83` at the 2025 ATH.
> 2. **Daily Cipher B Noise:** `_extract_cipherb()` currently mixes `20%` daily score + `±14..±20` pts from noisy `1D` divergences (`1D` zero-lookahead control = `1.41×`, Calmar `0.06` vs `1W = 6.08×`, Calmar `0.48`).
> 3. **6-Day Weekly Lookahead Bug in Historical Rebuilds:** `tools/rebuild_all_v4.py` maps Thursday-dated Kraken 1W candles (`cipherb_btcusdt_1w.json`) directly onto Thursday `D` instead of their actual Wednesday close (`D + 6 days`), leaking up to 6 days of future momentum into historical `scores.json`.
> 4. **Stateful Hybrid Decision Engine:** `market_regime` currently sells statically at `BRI >= 65–75` (exiting months before parabolic tops) and buys statically at `BRI < 35` (catching falling knives). Replace with a zero-lookahead **Dual-Sleeve Allocation Engine** (`BTCBRI Trailing Peak + TiZ` + `Cipher B 1W ±2 Hysteresis`).

---

## 0. NON-NEGOTIABLE GUARDRAILS (READ FIRST — DO NOT VIOLATE)

1. **NEVER create or re-introduce any field named `v5_score`, `v5_confidence`, `v5_shap_top5`, or `legacy_model`.**  
   `tools/test_legacy_model_retired.py` explicitly asserts these keys are retired and popped in `scraper/scoring_pipeline.py`. Use `bri_score`, `bri_version: "v5"`, `allocation_signal`, and `target_allocation_pct` instead.
2. **NEVER break `_causal_vals(metric, ex_type, target_date_str)` return signature** in `scraper/cycle_normalizer.py`.  
   `tools/test_quant_integrity.py::test_cycle_anchor_is_hidden_until_confirmation` asserts:
   ```python
   assert cycle_normalizer._causal_vals('nupl', 'BOTTOM', '2020-02-01') == []
   assert cycle_normalizer._causal_vals('nupl', 'BOTTOM', '2020-03-01') == [(1.5, 1.0)]
   ```
   It MUST continue returning `[(float_val, float_weight), ...]` strictly filtered by `confirmed_at <= target_date_str`.
3. **NEVER break `market_regime` enum compatibility.**  
   Keep `market_regime` strictly in `{'Buy', 'Wait', 'Hold', 'Sell', 'Unavailable'}`. Put richer regime labels and percentages (`0 / 50 / 100`) inside a new `allocation_signal` dict + `target_allocation_pct` int.
4. **ZERO LOOKAHEAD — STRICTLY ENFORCED:**
   - Every calculation for `target_date` may ONLY read data with `date < target_date` (or `<= target_date` for current-day close) and `confirmed_at <= target_date`.
   - Every Kraken 1W candle in `data/history/cipherb_btcusdt_1w.json` has an **open date on Thursday `D`** and closes on **Wednesday `D + 6 days`** (empirically verified `0.0%–0.1%` match). Only expose a Thursday weekly row on or after **`D + 6 days`**. Ignore non-Thursday tail snapshot rows when building historical weekly series.
5. **NEVER run live Playwright scrapers (`scraper/cvdd.py`, `run_scraper.py`) locally.** Only run offline recompute, backtest, invariant, and unit test scripts.
6. **BACKUP FIRST:** Before touching `data/history/scores.json`, copy it to `/tmp/scores_v4_backup.json` (and `/tmp/data_v4_backup.json`) so you can run an exact V4 vs V5 walk-forward comparison and roll back if needed.

---

## 1. PRE-FLIGHT SETUP

Run this exact command before editing any code:
```bash
cd /Users/max/Projects/BitcoinScore
cp data/history/scores.json /tmp/scores_v4_backup.json
cp data/data.json /tmp/data_v4_backup.json
cp data/bucket_returns.json /tmp/bucket_returns_v4_backup.json 2>/dev/null || true
pytest -q
```
Confirm baseline passes (`55 passed, 1 skipped`).

---

## 2. TASK 1 — Adaptive Decaying-Peak Normalization (`scraper/cycle_normalizer.py`)

### Why V4 fails on 2024–2025 ETF Tops
In `scraper/cycle_normalizer.py` (lines 169–200):
- `t_pct = _interp_fraction(top_vals, v)` still anchors `1.0` to `max(top_vals)` (`2013/2017`: `MVRV=10.04`, `CVDD=20.15`, `Puell=9.29`), while modern ETF peaks are much lower (`2025-10-06`: `NUPL=58.5%`, `MVRV=2.78`, `CVDD=3.19`, `Puell=1.59`, `RHODL=6258`).
- `intra_cycle_percentile()` collects all days since the last major bottom (`2022-11-21`). Because `2024-03-14` (`$73.5k`) had higher raw ratios (`MVRV=3.36`, `NUPL=64.1%`, `CVDD=3.68`) than `2025-10-06` (`$124.7k`), the entire 2025 ATH rally was permanently shadowed by March 2024 and scored only `~70` on-chain.
- Conversely, **cycle bottoms remain structurally anchored** (`CVDD ~ 1.0`, `Puell ~ 0.32`, `MVRV < 0`), so bottoms MUST NOT be distorted by rolling short-window normalization.

### Exact Implementation in `scraper/cycle_normalizer.py`

1. **Keep** `_RECENCY_GAMMA = 0.55` for `_causal_vals()` (or `0.50`) — do NOT change `_causal_vals()` return type or single-item weight (`1.0`).
2. **Add helper `_weighted_quantile(pairs, q)`** (pure Python, no external deps):
   - Input: `pairs = [(val, weight), ...]` sorted ascending by `val`, `q in [0.0, 1.0]`.
   - Compute cumulative normalized weights and linearly interpolate the value at quantile `q`.
   - Edge cases: if `len(pairs) == 1`, return `pairs[0][0]`.
3. **Add helper `rolling_window_percentile(metric: str, value, target_date, window_days: int = 540) -> int | None`:**
   - Strictly causal: collect `float(row[metric])` from `_scores_map` where `(target_date - window_days) <= date < target_date`.
   - Require `len(vals) >= 90`; otherwise return `None`.
   - Return `round(sum(1 for x in vals if x <= float(value)) / len(vals) * 100)`.
   - **Why 540 days (18 months)?** Long enough to span a full multi-quarter rally/pullback without firing false tops in a 3-month bounce, yet short enough that early-cycle spikes (like March 2024) roll off before late-cycle ETF tops (Oct 2025).
4. **Update `cycle_normalize(metric: str, value, target_date)` (lines 153–201):**
   - Keep `bottom_vals = _causal_vals(metric, 'BOTTOM', target_str)` and `top_vals = _causal_vals(metric, 'TOP', target_str)`.
   - Keep `if len(bottom_vals) < 2 or len(top_vals) < 2: return None`.
   - Compute recency-weighted anchors (using **fast decay** `gamma_top = 0.45` over chronologically sorted `top_vals` so the latest 2 confirmed cycle tops dominate `~75%+` of the ceiling, NOT 2013/2017):
     ```python
     # Chronologically-weighted top anchor (decays old 2013/2017 bubble peaks)
     # Note: _causal_vals() returns pairs in chronological order with recency weights.
     # Recompute top reference ceiling using recency-weighted mean + upper bound:
     t_wsum = sum(w for _, w in top_vals)
     t_ref = sum(val * w for val, w in top_vals) / t_wsum  # recency-weighted top center
     # Diminishing-peak damping: if the two most recent tops strictly decreased,
     # project a gentle causal damping factor clamped to [0.78, 1.00].
     if len(top_vals) >= 2 and top_vals[-2][0] > 0:
         ratio = top_vals[-1][0] / top_vals[-2][0]
         decay_factor = max(0.78, min(1.00, 0.5 + 0.5 * ratio))
     else:
         decay_factor = 1.0
     t_eff_low  = min(x[0] for x in top_vals[-2:]) * decay_factor   # ~75-pt zone entry
     t_eff_high = max(t_ref, top_vals[-1][0])                       # ~95-100-pt zone ceiling
     if t_eff_high <= t_eff_low:
         t_eff_high = t_eff_low * 1.15 if t_eff_low > 0 else t_eff_low + 0.5

     b_max = max(x[0] for x in bottom_vals)
     # Guard against degenerate overlap if decay pulls t_eff_low near b_max
     if t_eff_low <= b_max:
         t_eff_low = max(b_max + 1e-6, min(x[0] for x in top_vals))

     if v <= b_max:
         cross_score = 15.0 * b_pct
     elif v < t_eff_low:
         mid_frac = (v - b_max) / (t_eff_low - b_max)
         cross_score = 15.0 + 60.0 * mid_frac
     elif v < t_eff_high:
         top_frac = (v - t_eff_low) / (t_eff_high - t_eff_low)
         cross_score = 75.0 + 23.0 * top_frac
     else:
         # Strictly monotonic smooth asymptotic tail in [98.0, 100.0] so Layer 2
         # monotonicity (s_mid < s_high) NEVER saturates flatly on large inputs!
         excess = (v - t_eff_high) / max(abs(t_eff_high), 1.0)
         cross_score = 98.0 + 2.0 * (excess / (1.0 + excess))
     ```
   - **Blend with causal 540-day rolling + intra-cycle percentile ONLY above `cross_score > 40.0`** (so bottoms `<= 40` stay 100% anchored to invariant bottom floors!):
     ```python
     intra_score = intra_cycle_percentile(metric, value, target_date)
     roll_score  = rolling_window_percentile(metric, value, target_date, window_days=540)
     local_score = None
     if intra_score is not None and roll_score is not None:
         local_score = 0.40 * float(intra_score) + 0.60 * float(roll_score)
     elif roll_score is not None:
         local_score = float(roll_score)
     elif intra_score is not None:
         local_score = float(intra_score)

     if local_score is not None and cross_score > 40.0:
         # Smooth ramp: 0% at cross_score=40 -> 65% at cross_score>=58
         w_local = min(0.65, (cross_score - 40.0) / 18.0 * 0.65)
         blended = (1.0 - w_local) * cross_score + w_local * local_score
         return max(0, min(100, round(blended)))
     return max(0, min(100, round(cross_score)))
     ```
5. **Update `rhodl_ratio` (`INTRA_CYCLE_METRICS`)** in `scraper/normalizer.py` lines 137–141:
   - For `rhodl_ratio`, blend `0.40 * intra_cycle_percentile + 0.60 * rolling_window_percentile(..., 540)` when both are available (falling back to whichever is not `None`). This keeps `rhodl_ratio` strictly monotonic (`180 < 1500 < 15000` on `2025-10-06`) while eliminating the March-2024 shadow over Oct 2025!

### Verification for Task 1
```bash
python3 -c "
import datetime
from scraper.normalizer import normalize_metric
td = datetime.date(2025, 10, 6)
# Check 2025 ATH raw values (should now score high, ~78-96, instead of 60-72!)
for m, v in [('nupl', 58.54), ('mvrv_z_score', 2.78), ('cvdd_ratio', 3.19), ('puell_multiple', 1.59), ('rhodl_ratio', 6258.0)]:
    print(f'2025-10-06 ATH {m:15s} ({v}) -> {normalize_metric(m, v, td)}')
# Check 2026-11-23 current values (must stay LOW, ~10-30!)
td_now = datetime.date(2026, 11, 23)
for m, v in [('nupl', 12.73), ('mvrv_z_score', 0.26), ('cvdd_ratio', 1.20), ('puell_multiple', 0.71), ('rhodl_ratio', 691.0)]:
    print(f'2026-11-23 NOW {m:15s} ({v}) -> {normalize_metric(m, v, td_now)}')
# Check strict Layer 2 monotonicity on 2025-10-06
for m, lo, mid, hi in [('nupl',-25,35,75),('mvrv_z_score',-0.4,1.5,6.0),('rhodl_ratio',180,1500,15000),('cvdd_ratio',1.0,2.2,5.5),('puell_multiple',0.35,1.1,3.5)]:
    a, b, c = normalize_metric(m, lo, td), normalize_metric(m, mid, td), normalize_metric(m, hi, td)
    assert 0 <= a < b < c <= 100, f'Monotonicity failed for {m}: {a}, {b}, {c}'
    print(f'Monotonic OK {m:15s}: {a} < {b} < {c}')
"
```

---

## 3. TASK 2 — Remove Daily Cipher B Noise from `_extract_cipherb()` (`scraper/score.py`)

### Exact Changes in `scraper/score.py` (lines 119–192)
1. **In `_extract_cipherb(cb)` (lines 119–176):**
   - Keep exact return tuple `(weekly_raw, daily_raw, blended)` so `scraper/normalizer.py` and callers keep working unchanged.
   - Keep weekly divergence adjustments (`double_bearish_div`, `fast_bearish_div`, `bearish_divergence`/`bearish_div`, and bullish equivalents).
   - **REMOVE** the daily divergence shifts and `0.8 * w + 0.2 * d` daily blend (lines 156–173). `blended` MUST be derived **100% from weekly `w`**!
   - **ADD** a gentle, strictly causal weekly hysteresis momentum adjustment using `wt1` and `wt2` (when both are present in `val_dict`):
     ```python
     wt1 = val_dict.get('wt1')
     wt2 = val_dict.get('wt2')
     if wt1 is not None and wt2 is not None:
         spread = float(wt1) - float(wt2)
         # Confirmed weekly momentum direction (±2 hysteresis deadband from 2017-2026 backtest)
         if spread >= 2.0 and w >= 55.0:
             w = min(100.0, w + 3.0)
         elif spread <= -2.0 and w <= 45.0:
             w = max(0.0, w - 3.0)
     blended = round(max(0.0, min(100.0, w)))
     return weekly_raw, daily_raw, blended
     ```
2. **In `_cipherb_has_div(cb)` (lines 178–192):**
   - Check ONLY the 8 weekly divergence keys (`double_bearish_div`, `fast_bearish_div`, `bearish_divergence`, `bearish_div`, `double_bullish_div`, `fast_bullish_div`, `bullish_divergence`, `bullish_div`). Remove the 8 `daily_*` / `fast_*_div` daily keys so noisy daily divergences never override utility weights.

### Verification for Task 2
```bash
python3 -c "
from scraper.score import _extract_cipherb, _cipherb_has_div
# Daily noise must NOT alter blended score
w1, d1, b1 = _extract_cipherb({'weekly_score': 40, 'daily_score': 5, 'daily_double_bullish_div': True})
w2, d2, b2 = _extract_cipherb({'weekly_score': 40, 'daily_score': 95, 'daily_double_bearish_div': True})
assert b1 == b2 == 40, f'Daily noise leaked: {b1} vs {b2}'
assert _cipherb_has_div({'daily_fast_bullish_div': True}) is False
assert _cipherb_has_div({'bearish_div': True}) is True
print('Task 2 Cipher B 1W-only verification PASSED')
"
```

---

## 4. TASK 3 — Stateful Dual-Sleeve Decision Engine (`scraper/allocation_engine.py` + `scraper/score.py` + `scraper/scoring_pipeline.py`)

### Architecture
Create a clean, dedicated, pure-Python module **`scraper/allocation_engine.py`** implementing our empirically proven zero-lookahead 50/50 Dual-Sleeve Decision Engine (`HYB_5050_BRI35_HYST2` + `TiZ` confirmation state):

```python
# scraper/allocation_engine.py
"""Stateful zero-lookahead Dual-Sleeve Allocation & Regime Engine (BTCBRI V5).

Combines two orthogonal sleeves validated on 2017-2026 zero-lookahead backtests:
  1. Valuation Sleeve (50% weight): BTCBRI Trailing Peak + TiZ awareness
     - Enters when BRI <= 35 (full confirmation when TiZ(BRI <= 25) >= 21 days)
     - Arms exit only after BRI reaches >= 85; exits ONLY when BRI drops >= 15 pts below cycle peak
  2. Momentum Sleeve (50% weight): Weekly Cipher B Hysteresis (±2.0 deadband)
     - Enters when closed weekly (wt1 - wt2) >= +2.0
     - Exits when closed weekly (wt2 - wt1) >= +2.0
"""
```

### Exact Function Contract in `scraper/allocation_engine.py`
Implement `compute_allocation_signal(final_score, scores_history, cipherb_val, data_quality_status='valid', confidence_score=50, forward_risk=None, forward_risk_validated=False) -> dict`:

1. **Replay causal BTCBRI Valuation Sleeve** across `scores_history` (list of `(date, score, ...)` tuples strictly `< target_date`) plus today's `final_score`:
   - Track:
     - `bri_in_pos: bool` (starts `False`)
     - `trailing_armed: bool` (starts `False`)
     - `trailing_peak_bri: float` (starts `0.0`)
     - `tiz_le25_days: int` (consecutive trailing days with `score <= 25`)
   - Causal transition rules for each historical score `sc` (and finally today's `final_score`):
     - Update `tiz_le25_days = tiz_le25_days + 1 if sc <= 25 else 0`.
     - If not `bri_in_pos` and `sc <= 35`:
       - `bri_in_pos = True`, `trailing_armed = False`, `trailing_peak_bri = sc`
     - Else if `bri_in_pos`:
       - If `sc >= 85`: `trailing_armed = True`
       - If `sc > trailing_peak_bri`: `trailing_peak_bri = sc`
       - If `trailing_armed` and `sc <= trailing_peak_bri - 15`:
         - `bri_in_pos = False`, `trailing_armed = False`, `trailing_peak_bri = 0.0`
   - `bri_sleeve_pct = 50 if bri_in_pos else 0`
   - `tiz_confirmed = tiz_le25_days >= 21`
2. **Evaluate Weekly Cipher B Momentum Sleeve** from `cipherb_val` (dict containing `wt1`, `wt2`, `weekly_score` of the latest **closed** weekly bar):
   - `wt1 = cipherb_val.get('wt1')`, `wt2 = cipherb_val.get('wt2')`
   - `wt_spread = round(float(wt1) - float(wt2), 2)` if both not None else `None`
   - Note: to handle hysteresis (`-2 < spread < +2`) causally without flipping on deadband, check optional `prev_cb_in_pos` (or if `wt_spread is not None`: `cb_in_pos = wt_spread >= 2.0` when `abs(wt_spread) >= 2.0`, else `wt_spread > 0` fallback when single-day stateless call). Better: if `scores_history` isn't carrying weekly state, `cb_in_pos = (wt_spread is not None and wt_spread >= 2.0)` for clean, deterministic stateless evaluation (and document this clearly).
   - `cipherb_sleeve_pct = 50 if cb_in_pos else 0`
3. **Combine into `target_allocation_pct` (`0`, `50`, or `100`):**
   - `target_allocation_pct = bri_sleeve_pct + cipherb_sleeve_pct`
   - **High-drawdown / data-quality guard:**
     - If `data_quality_status == 'invalid'`: `market_regime = 'Unavailable'`, `action_label = 'UNAVAILABLE'`
     - Else if `data_quality_status != 'valid'`: `market_regime = 'Wait'`, `action_label = 'DEGRADED_WAIT'`
     - Else map to backward-compatible `market_regime` in `{'Buy', 'Wait', 'Hold', 'Sell'}` AND rich `action_label`:
       - **Trailing Sell Triggered** (`final_score >= 65` and NOT `bri_in_pos` and NOT `cb_in_pos`):
         `market_regime = 'Sell'`, `action_label = 'EXIT_CASH'`
       - **Armed Top Warning** (`bri_in_pos` and `trailing_armed`):
         `market_regime = 'Hold'`, `action_label = 'ARMED_TOP_HOLD'` (or `'TRIM_50'` if `not cb_in_pos`)
       - **Confirmed Accumulation (`target_allocation_pct == 100` and `final_score <= 40`)**:
         `market_regime = 'Buy'`, `action_label = 'STRONG_BUY_100'`
       - **Early Valuation Accumulation (`final_score <= 35` and `bri_in_pos` and `confidence_score >= 45` and not high_dd_guard)**:
         `market_regime = 'Buy'`, `action_label = 'ACCUMULATE_50'` (when `not cb_in_pos`) or `'STRONG_BUY_100'` (when `cb_in_pos` or `tiz_confirmed`)
       - **In-Trend Hold (`target_allocation_pct >= 50` and `final_score > 35`)**:
         `market_regime = 'Hold'`, `action_label = 'HOLD_100'` (if 100%) or `'HOLD_50'` (if 50%)
       - **Otherwise**:
         `market_regime = 'Wait'`, `action_label = 'WAIT_CASH'`
4. **Return dict:**
   ```python
   {
       'market_regime': market_regime,
       'action_label': action_label,
       'target_allocation_pct': target_allocation_pct,
       'bri_sleeve_pct': bri_sleeve_pct,
       'cipherb_sleeve_pct': cipherb_sleeve_pct,
       'bri_in_position': bri_in_pos,
       'trailing_armed': trailing_armed,
       'trailing_peak_bri': round(trailing_peak_bri, 1),
       'trailing_exit_trigger': round(trailing_peak_bri - 15.0, 1) if trailing_armed else None,
       'tiz_le25_days': tiz_le25_days,
       'tiz_confirmed': tiz_confirmed,
       'weekly_wt_spread': wt_spread,
       'weekly_score': cipherb_val.get('weekly_score') if isinstance(cipherb_val, dict) else None,
   }
   ```

### Wire into `scraper/score.py` and `scraper/scoring_pipeline.py`
1. In `scraper/score.py` (around lines 825–902):
   - Call `compute_allocation_signal(...)` passing `final`, `scores_history` (already loaded/passed into `compute_score`), `_cb_inner`, `quality['status']`, `confidence_score`, `v5b_score`, `v5b_validated`.
   - Use its `market_regime` as the authoritative `market_regime`.
   - Include `'bri_version': 'v5'`, `'target_allocation_pct': alloc['target_allocation_pct']`, `'allocation_signal': alloc` in the returned dict.
2. In `scraper/scoring_pipeline.py`:
   - In `sync_public_names(payload, scores)`: set `payload['bri_version'] = scores.get('bri_version', 'v5')`, `payload['target_allocation_pct'] = scores.get('target_allocation_pct')`, and `payload['allocation_signal'] = scores.get('allocation_signal')`.
   - In `_scoring` dict (lines 365–378): also include `'bri_version'`, `'target_allocation_pct'`, `'allocation_signal'`.
   - In `_new_entry` for `scores.json` (lines 418–445): set `'source': 'live_v5'` and include `'target_allocation_pct': scores.get('target_allocation_pct')`.

---

## 5. TASK 4 — Fix Weekly +6d Lookahead & Payload Parity in `tools/rebuild_all_v4.py`

### Two Existing Bugs to Fix in `tools/rebuild_all_v4.py`
1. **BUG A — 6-Day Weekly Lookahead (lines 104–146):**
   - Currently keys `wk_rows_by_date` by Thursday open date `r['date'][:10]` and includes non-Thursday daily tail snapshots!
   - **FIX:** Filter to exact Thursday rows (`datetime.date.fromisoformat(d).strftime('%a') == 'Thu'`), compute causal `wt2 = SMA(wt1, 3)` on the sorted Thursday series, and key `wk_rows_by_close_date` by **`close_date = (open_date + datetime.timedelta(days=6)).isoformat()`** (Wednesday close)!
   - Also pass `'wt1': wk_r.get('wt1')` and `'wt2': wk_r.get('wt2')` inside `cb_dict` (lines 151–166) so `compute_score()` and `allocation_engine` see exact causal `wt1`/`wt2` on every historical day!
2. **BUG B — `payload['phase']` Schema Corruption & Missing `sync_public_names` (lines 217–255):**
   - Currently line 225 overwrites `payload['phase']` (which is a dict `{'phase': 'BOTTOM', 'w_bot': ...}` in `data.json`) with a raw string `last_res.get('phase')`, breaking `web/trade.html` (`DATA.phase?.phase`)!
   - **FIX:** Preserve dict schema for `payload['phase']` (matching `scoring_pipeline.py` lines 295–305) AND call `sync_public_names(payload, last_res)` plus update `payload['scoring']['final_score']`, `payload['scoring']['market_regime']`, `payload['scoring']['target_allocation_pct']`, `payload['scoring']['allocation_signal']`, and `payload['computed']['raw_utility_score']`.
3. **Add `--full-v5` flag (or allow `--days 3500`)** to `tools/rebuild_all_v4.py` so you can cleanly recompute the entire `2017-08-17 → present` history in `scores.json` with zero lookahead, then automatically run:
   - `python3 tools/sync_web_scores.py`
   - `python3 tools/bucket_returns.py`
   - `python3 tools/generate_sparklines.py`
   - `python3 tools/discover_strategies.py`

---

## 6. TASK 5 — Surface V5 `allocation_signal` in Trading Desk (`web/trade.html`)

Small, clean UI update in `web/trade.html`:
- In `renderKPIs()`:
  - Under `#kpi-raw-util`, display V5 allocation badge when available: e.g. `Raw utility: 27 · Режим: Buy · Ціль: 50% BTC (V5)`.
- In `renderRecommendations()`:
  - If `DATA.allocation_signal` exists, show a concise **V5 Engine Banner / Pill** summarizing `target_allocation_pct` (`0% / 50% / 100%`), `bri_sleeve_pct`, `cipherb_sleeve_pct`, `trailing_armed` (and exit trigger if armed), and `tiz_le25_days`.
- Include `allocation_signal: DATA.allocation_signal` inside `buildContextPackageText()`.
- Verify JS syntax with `node --check`.

---

## 7. TASK 6 — Walk-Forward V4 vs V5 Verification & Unit Tests (MANDATORY GATE)

### 7.1 Create `tools/validate_v5_walkforward.py`
Write a standalone comparison script that reads `/tmp/scores_v4_backup.json` (V4 baseline) and `data/history/scores.json` (new V5) and prints a **head-to-head comparison table** using `tools/discover_strategies.py`'s exact zero-lookahead simulator (0.5% fee, `t+1` execution, weekly `+6d` close):

1. **Cycle Milestone Calibration (V4 vs V5):**
   - Bottoms (`2018-12-15`, `2020-03-13`, `2022-06-18`, `2022-11-21`): MUST all be `<= 20` and `phase == 'BOTTOM'`.
   - Tops (`2017-12-17`, `2021-04-14`, `2021-11-10`, `2024-03-14`, `2025-10-06`): MUST all be `>= 80` and `phase == 'TOP'`, AND **`2025-10-06` ATH MUST improve from `79–83` to `>= 85`** so `Trailing Peak (arm >= 85)` catches modern ETF cycle tops!
   - Mean Top−Bottom separation MUST be `>= 72` pts (and `>=` V4).
2. **Walk-Forward Out-of-Sample Strategy Metrics (2018–2026 + Cycle 1 `2018–2022` + Cycle 2 `2022–2026` + 22 Rolling 4Y Windows + 6Y Monthly Cash Pool):**
   - Compare V4 vs V5 on:
     - `BRI_35_TRAIL_85_15`
     - `BRI_TIZ25_21D_T85`
     - `HYB_5050_BRI35_HYST2`
     - `HYB_5050_TIZ21_HYST2`
     - `HYB_5050_BRI35_CBSCORE`
   - **Check specifically whether V5's `BRI_35_TRAIL_85_15` now catches the `2025` cycle peak exit (`$100k–$120k`) and improves Cycle 2 (`2022–2026`) return & MaxDD vs V4!**
3. **Forward-Return Monotonicity (365d):**
   - Compute Spearman rank correlation between `final_score` and `365d forward BTC return` on `2018-01-01 .. (latest - 365d)` for BOTH V4 and V5. V5 Spearman must be strongly negative (`<= -0.45`) and at least as strong as V4 (within `0.03` tolerance).
4. **Save the V4 vs V5 comparison log** to `docs/v5_walkforward_report.md`.

### 7.2 Create Unit Tests in `tools/test_v5_redesign.py`
Write pytest unit tests covering:
1. `test_cycle_normalize_decaying_peaks_2025_ath`: verifies `nupl`, `mvrv`, `cvdd_ratio`, `puell` score higher at `2025-10-06` ATH while remaining strictly `<= 25` at `2022-11-21` bottom and strictly monotonic.
2. `test_cipherb_ignores_daily_noise`: verifies daily divergences and `daily_score` do NOT change `_extract_cipherb` blended score or `_cipherb_has_div`.
3. `test_allocation_engine_trailing_peak_and_tiz`: verifies:
   - `BRI=30` enters BRI sleeve (`50%`),
   - `BRI=87` arms trailing stop without selling (`Hold`),
   - `BRI=71` (`<= 87 - 15`) triggers trailing exit (`0%`, `Sell`),
   - `wt1 - wt2 = +3.0` activates Cipher B sleeve (`+50%`),
   - `wt1 - wt2 = -3.0` deactivates Cipher B sleeve (`0%`),
   - `data_quality_status='invalid'` returns `market_regime == 'Unavailable'`.
4. `test_no_retired_v5_keys`: verifies `compute_score()` output never contains `v5_score`, `v5_confidence`, `v5_shap_top5`, `legacy_model`.

---

## 8. EXECUTION ORDER & PASS/FAIL CHECKLIST

Execute strictly in this order — **STOP AND FIX** if any check fails:

```bash
# 1. Backup V4 baseline
cp data/history/scores.json /tmp/scores_v4_backup.json
cp data/data.json /tmp/data_v4_backup.json

# 2. Implement Tasks 1, 2, 3, 4, 5, 6

# 3. Full zero-lookahead V5 recompute (2017-08-17 -> today)
python3 tools/rebuild_all_v4.py --days 3500

# 4. Regenerate downstream artifacts & strategy patterns
python3 tools/sync_web_scores.py
python3 tools/bucket_returns.py
python3 tools/generate_sparklines.py
python3 tools/discover_strategies.py

# 5. Run V4 vs V5 walk-forward comparison
python3 tools/validate_v5_walkforward.py

# 6. Run ALL 6 system invariant layers (MUST BE 0 FAILED!)
python3 tools/verify_system_invariants.py

# 7. Run full pytest suite (MUST BE 100% PASS!)
pytest -q

# 8. Verify trade.html JS syntax
python3 -c "import re, subprocess; m=re.search(r'<script>([\s\S]*?)</script>', open('web/trade.html').read()); open('/tmp/t.js','w').write(m.group(1)); subprocess.run(['node','--check','/tmp/t.js'], check=True); print('JS OK')"
```

### Bounded Tuning Rule (ONLY if `validate_v5_walkforward.py` shows `2025-10-06` < 85 or Layer 4/5 fails)
Do NOT blindly grid-search! Adjust ONLY these 3 bounded hyperparameters if needed:
- `window_days` in `rolling_window_percentile`: allowed set `{365, 540, 730}`
- `w_local` max weight in `cycle_normalize`: allowed range `[0.50, 0.70]`
- `decay_factor` floor in `cycle_normalize`: allowed range `[0.75, 0.85]`

If V5 fails to match or beat V4 on **both** Cycle 2 (`2022–2026`) Calmar **and** `verify_system_invariants.py`, restore `/tmp/scores_v4_backup.json` → `data/history/scores.json` and report the exact failure diff rather than committing a regression.

### Final Commit Scope (ONLY commit clean V5 files — never commit untracked scratch/book files!)
```bash
git add scraper/cycle_normalizer.py scraper/normalizer.py scraper/score.py \
        scraper/allocation_engine.py scraper/scoring_pipeline.py \
        tools/rebuild_all_v4.py tools/validate_v5_walkforward.py tools/test_v5_redesign.py \
        data/history/scores.json web/history/scores.json \
        data/data.json web/data.json data/data_exp.json web/data_exp.json \
        web/sparklines.json data/strategy_patterns.json web/strategy_patterns.json \
        web/trade.html docs/strategy_backtest_2026_11_24.md docs/v5_walkforward_report.md
```
