# BitcoinScore Alpha v5.2 · Causal Core & Trading Architecture

**Updated:** 2026-11-25  
**Status:** Production (`main` · `0%` lookahead)

---

## 1. Core Principles

Alpha v5.2 separates **zero-lookahead causal risk measurement** from **legacy V3/V4 scores** (`final_score` is preserved in `data/history/scores.json` for backward compatibility, while all live dashboards, history charts, and trading strategies run on **Causal Index v5.2**).

1. **Zero Lookahead (`0%`):** Every weekly value at bar `t` uses strictly closed Monday-opening weekly OHLC (`week_close < as_of_date`) and historical data `<= week_close`.
2. **No Hard Saturation (`(1.0, 99.0)`):** `soft_risk_compress()` in `scraper/causal_core.py` leaves `[15, 85]` linear and applies smooth `tanh` compression on extreme tails so the index never falsely claims `0.0` or `100.0` certainty.
3. **Single Reference Extremes Table:** All historical milestone evaluations read from [`docs/btc_extrema_events.csv`](btc_extrema_events.csv) via `tools/eval_reference_extremes.py` and `tools/verify_system_invariants.py`.

---

## 2. Authoritative Data Stores

| Store | Module | Contents |
|---|---|---|
| `data/history/btc_weekly_ohlc.json` | `scraper/ohlc_store.py` | 794 closed Monday-opening weekly BTC/USD OHLC bars (`2011–2026`), auto-synced from Kraken 1D OHLC. |
| `data/history/mvrv_ratio_history.json` | `scraper/mvrv_ratio_store.py` | 5,974 daily CoinMetrics Community API rows (`CapMVRVCur`, `CapMrktCurUSD`, derived `realized_cap_usd`), synced incrementally before scoring. |
| `data/history/scores.json` | `scraper/history_writer.py` | Daily on-chain/macro metrics (`cvdd_ratio`, `puell`, `mvrv`, etc.) + legacy `final_score`. |
| `web/history/scores.json` & `web/score_history.json` | `tools/sync_web_scores.py` | Backfilled daily `causal_btcbri` (from `compute_causal_weekly_history()`) used by `BTCBRI HISTORY` charts on `index.html` and `classic.html`. |

---

## 3. Causal Index v5.2 (`Smart Purged-IC`)

Computed identically for live production and backtests in `scraper/causal_core.py::compute_causal_weekly_history()`:

### Sub-Indices (all strictly causal)
1. **`CostBasis4Y` (`cb4y`):** Rolling 4-year (`208w`) corridor position of $\ln(\text{Price} / \text{Realized Price}) = \ln(\text{CapMVRVCur}) \in [0, 100]$.
2. **`CVDD_4Y` (`cvdd4`):** Rolling 4-year (`208w`) causal percentile rank of `cvdd_ratio`.
3. **`MVRV_MCapAdj` (`mcap`):** Market-cap-adjusted MVRV using causal OLS of $|\ln(\text{MVRV})|$ on $\log_{10}(\text{Realized Cap})$ over past weeks `< i` (captures empirical cycle compression: $\rho(\log_{10}(\text{RCap}), \text{4Y Range}) = -0.9461$).
4. **`CB_Divergence` (`cb_div`):** 26-week causal price vs WaveTrend `WT1` divergence (`50 + (p_pos - w_pos) * 50`), capturing exhaustion without the momentum drag of raw `WT1`.
5. **`Puell_4Y` (`puell4`):** Rolling 4-year (`208w`) causal percentile rank of `puell`.

### Smart Purged-IC Weighting
At each week `i >= 104`, weights are learned **strictly from matured 52-week forward returns** (`k in [max(0, i-260) .. i-52]`):
$$w_k(i) \propto \max(0.05,\; -\rho_{\text{Spearman}}(x_k, r_{52w}))^2$$
As of `2026-11-24`, live smart weights automatically converged to:
- `CVDD_4Y`: **43.5%**
- `CostBasis4Y`: **38.4%**
- `MVRV_MCapAdj`: **15.0%**
- `CB_Divergence`: **2.9%**
- `Puell_4Y`: **0.3%** *(halving decay detected automatically)*

Live index value (`2026-11-24`): **`causal_btcbri = 20.3 / 100`**.

---

## 4. Tail Risk 13w & Causal Uncertainty

- **13-Week Tail Risk (`scraper/tail_risk.py`):** `B1_vol13` predicts $P(\text{MaxDD}_{13w} \ge 25\%)$ on real weekly OHLC; passes expanding purged OOS (`n_oos = 503`, `BSS = +5.1%`, `ECE_5bin = 0.042 <= 0.08`, `validated = True`).
- **Causal Uncertainty (`compute_causal_uncertainty`):** Measures core dispersion + regime mismatch + data quality (`conflict_score`, `confidence_score = 100 - conflict_score`), replacing legacy anti-phase macro noise.

---

## 5. Three Production Trading Models (`web/trade.html`)

All 3 models are implemented in `scraper/causal_core.py`, surfaced via `scraper/allocation_engine.py`, backtested identically in `tools/run_causal_backtest.py` (`t+1 open`, intra-week `H/L` MaxDD, `0.5%` / `1.0%` fee), and share a volatility-adaptive trailing stop **`clip(0.60 * vol52, 18%, 30%)`** (active when `causal_btcbri > 30`):

| Model | Entry Rule | Exit Rule | 2017–2026 (`0.5%`) | 2021–2026 (`0.5%`) |
|---|---|---|---|---|
| **`20/30/50 · Піраміда`** (`Pyramid_Max`) | `20%` at `≤ 22`, `+30%` at `≤ 16`, `+50%` at `≤ 12` or `+15pt` rebound after `≤ 20` | `100%` cash at `≥ 80` or adaptive trailing stop | **68.37×** (`DD −52.4%`, `Calmar 1.01`, `WR 100%`) | **6.22×** (`DD −32.8%`, `Calmar 1.11`, `WR 100%`) |
| **`50/50 · Дно + Підтвердження`** (`Staged_50_50`) | `50%` at `≤ 20`, `+50%` at `≤ 12` or `+15pt` rebound after `≤ 20` | `50%` at `≥ 75`, remaining `50%` at `≥ 85` or adaptive trailing stop | **50.71×** (`DD −52.4%`, `Calmar 0.93`, `WR 100%`) | **4.28×** (`DD −23.7%`, `Calmar 1.18`, `WR 100%`) |
| **`100/100 · Один ордер`** (`V52_Index_Threshold`) | `100%` BTC at `≤ 20` | `100%` cash at `≥ 80` or adaptive trailing stop | **56.47×** (`DD −52.4%`, `Calmar 0.96`, `WR 100%`) | **5.74×** (`DD −38.6%`, `Calmar 0.89`, `WR 100%`) |

*Benchmark `Buy & Hold`: `61.00×` (`DD −84.1%`) in `2017–2026`, `1.84×` (`DD −77.6%`) in `2021–2026`.*

---

## 6. Verification & Deployment

```bash
python3 -m pytest -q
python3 tools/verify_system_invariants.py
python3 tools/validate_output.py
python3 tools/eval_reference_extremes.py
python3 tools/run_causal_backtest.py
```

- Static site deployment (`web/`): `.github/workflows/deploy.yml` (supports both `push` on `web/**` and manual `workflow_dispatch`).
- Trading desk (`web/trade.html`): clean 3-step UX (**1. Де ми зараз → 2. Чого чекаємо → 3. План дій і калькулятор**) + detailed analytics tabs below the fold.
