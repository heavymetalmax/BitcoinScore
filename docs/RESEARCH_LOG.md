# BitcoinScore Research & Decisions Log (Alpha v5.2)

**Updated:** 2026-11-25  
**Purpose:** Single empirical ledger of all ideas tested on BitcoinScore (`0%` lookahead, `0.5%` one-way fee, real closed Monday weekly OHLC since 2011) — recording **what was accepted**, **what was rejected**, and the **exact quantitative reason**, so failed experiments are never repeated.

---

## 1. Accepted & Shipped into Production (`main`)

| Idea | Where | Empirical Evidence | Why Accepted |
|---|---|---|---|
| **Cost-Basis 4Y Corridor (`ln(Price / Realized Price)`)** | `scraper/causal_core.py`, `mvrv_ratio_store.py` | Replaced raw price amplitude (`ln(Price/ATH)`) | Eliminates post-cycle amplitude bias as BTC market cap grows. |
| **Market-Cap-Adjusted MVRV (`MVRV_MCapAdj`)** | `scraper/causal_core.py` | $\rho(\log_{10}(\text{RCap}), \text{4Y Range}) = -0.9461$ ($p = 3.8\times 10^{-288}$) | Captures structural cycle volatility compression ($\rho = -0.6600$). |
| **Smart Purged-IC Sub-Index Weighting** | `scraper/causal_core.py` | Rolling 5y purged 52w Spearman IC (`CVDD 40.6%`, `CostBasis 35.9%`, `MVRV_MCapAdj 14.0%`, `StableROC_26w 6.6%`, `CB_Div 2.7%`, `Puell 0.3%`) | Automatically down-weights decaying metrics (like post-halving Puell) with `0%` lookahead. |
| **Zero-Cold-Start `Stablecoin 26w ROC (4Y)` (`st_roc4`)** | `stablecoin_store.py`, `causal_core.py` | `DefiLlama` (`3,284` daily rows, `\$282.5B`); `2021–26` 52w IC = **`-0.762`**; lifted full-index 52w IC in both `17+` & `21+` with `0%` backtest loss | Real daily-updated crypto liquidity inflow/washout; strictly zero weight until `supply >= \$5B` & `40` matured 52w bars. |
| **Live PiT OOS Audit Evaluator** | `tools/eval_pit_ledger.py` | Scores immutable `data/pit_ledger.jsonl` snapshots at `+91d` and `+365d` against realized returns/Brier | Guarantees ongoing honest out-of-sample verification as future months mature. |
| **Smooth C1 Tail Compression `(1.0, 99.0)`** | `soft_risk_compress()` | Leaves `[15, 85]` linear; smooth `tanh` tails | Preserves monotonic rank ordering without claiming `0%` or `100%` certainty at extremes. |
| **3 Final Trading Models (`20/30/50`, `50/50`, `100/100`)** | `causal_core.py`, `allocation_engine.py`, `web/trade.html` | `20/30/50`: `70.21×` (`2017–26`) / `6.93×` (`2021–26`), `WR 100%` | Staged depth-based entry outperforms `Buy & Hold` (`61.00× / 1.84×`) with under half the MaxDD. |
| **Volatility-Adaptive Trailing Stop** | `clip(0.60 * vol52, 18%, 30%)` (when `v5.2 > 30`) | Protects mid-cycle traps (e.g. 2019 bounce) without stopping out deep bottoms | Adapts stop width to cycle volatility instead of a brittle fixed `%`. |
| **Daily Pulse (`causal_btcbri`) vs Weekly Trade Execution** | `_score_daily_point_in_time()`, `run_causal_backtest.py` | Weekly `20/30/50` (`6.31×`) vs Daily execution (`3.57×` in `2021–26`) | Live UI reflects daily on-chain/price shifts while Sunday closes filter single-day spikes (`2024-03-12`). |
| **Smart Purged-IC 13w Macro Pressure (`70% DXY + 30% US10Y`)** | `causal_core.py`, `us10y_store.py`, `tail_risk.py` | `IC_13w`: `ΔDXY = -0.385`, `ΔUS10Y = -0.361`; `20/30/50` `2017–26`: `68.37× → 70.21×` (`DD -52.4% → -47.3%`), `2021–26`: `6.22× → 6.93×` | Captures real 1-quarter dollar/bond liquidity headwinds only when `> SMA20w` without breaking cycle-top exits. |
| **Unified Single Source of Truth in Web UI** | `export_web.py` → `sync_web_scores.py`, all `web/*.html` | `index.html`, `classic.html`, `minimal.html`, `about.html`, `compare.html`, `trade.html` | Eliminates legacy `final_score` vs `causal_btcbri` split-brain and reconciles 3 main bars (`13 / 45 / 46` → `20.7`) 1-to-1. |

---

## 2. Tested & Rejected Alternatives

| Idea | Exact Test Result | Why Rejected |
|---|---|---|
| **Raw `WT1` (Cipher B) inside risk index** | Wrong 52w sign (`+0.055`) | Momentum oscillator belongs in timing/divergence (`CB_Divergence` has right risk sign `-0.2084`). |
| **Legacy V3 Macro (`DXY level`, `T10Y2Y level`, `M2`) inside 52w index** | `T10Y2Y` inverted sign (`IC = -0.490`); `DXY level` (`+0.301`); `M2` 2.5-month lag; adding 10% weight cut `100/100` from `56.47× → 43.74×` | Level-based macro lags or inverts on 52w horizon; replaced by 13w momentum above `SMA20w`. |
| **Yield Curve (`T10Y2Y`) 13w steepening in score** | `5y Purged IC (13w) = +0.394` (recovery, not pressure); `20/30/50` unchanged (`61.17×`), `YC level` hurt (`43.74×`) | Steepening after inversion coincides with recovery; kept informational only. |
| **Adaptive Threshold Learners (`Quantile 4Y`, `WF Learner`) on `20/80`** | Double-adaptation lag cut returns `2.5×–3.4×` | Index is already adaptive via 4Y corridors + Smart Purged-IC; adapting thresholds on top causes lag. |
| **V-Reversal triggers outside `20/80` (`+8..+12pt` / `-12..-15pt`)** | Caught dead-cat bounces (`2026-08-17`, DD spiked to `-74.6%`) and shook out of 2024 bull pause | Mid-zone index swings are noise compared to `≤ 20 / ≥ 80` extreme bands. |
| **Bayesian `Z_surprise` exit (`-1.5σ`)** | Cut `2021–26` return in half (`5.74× → 2.64×`) without reducing MaxDD | Exited healthy bull trends prematurely. |
| **Daily entry confirmation at `≤ 20` (`Close > EMA7`, `Trailing Buy +5%`, `Daily CB`)** | `EMA7`: `46.87×`, `TB +5%`: `53.96×`, `Daily CB`: `66.44×` (no change) vs Base `66.44×` | Waiting for a daily bounce systematically buys higher (`$3,862 → $4,296`, `$20,574 → $21,238`). |
| **Daily `20/30/50` & `100/100` execution** | `20/30/50`: `6.31× → 3.57×` (`2021–26`); `100/100`: `5.77× → 3.09×` | Single-day spike on `2024-03-12` (`80.2`) triggered premature 100% exit at `$71,452`, missing the run to `$119.8k`. |
| **Daily exit (`TS -10%` / `EMA14`) once `v5.2 ≥ 80`** | `2017–26`: `73.52× → 88.77×` (2021 blow-off), but `2021–26`: `6.40× → 6.15×` (sold 2025 top `$115k` vs `$119.8k`) | Gain came from a single 2020–21 parabola; hurts smooth 2025-style tops and adds parameter complexity. |
| **Time-based `10/100` DCA (`10%/day`, `10%/week`) & `25/75` bounds** | `Daily 10%/d (20/80)`: `56.88× / 5.33×`; `Weekly 10%/w`: `46.38× / 4.77×`; `25/75`: `7.17× / 0.82×` | Time-DCA under-allocates on 3w V-bottoms (2020) and under-exits 1w tops (2025); `25/75` buys early May 2022 crash (`$29.1k`). |
| **Symmetric `DXY` modifier (`±6` pts including negative when DXY weak)** | `20/30/50` `2021–26`: `6.08×` vs `6.93×`; `100/100`: `5.36×` vs `5.74×` | Subtracting risk when DXY is weak delayed `≥ 80` exits at cycle tops (July 2025); asymmetric `DXY > SMA20w` only is strictly superior. |
| **Drawdown-reactive overlays (`CPPI −50%` at `DD ≥ 15%`, tighter `0.45×vol` stop)** | `CPPI 15%`: `38.11× / 3.73×`; `CPPI 20%`: `52.87× / 4.76×`; `0.45×vol`: `32.93× / 3.25×` | Cutting size after a drawdown realizes losses at the bottom of normal `-15%..-25%` BTC pullbacks. |
| **`RHODL_4Y` added to Smart Purged-IC** | `20/30/50` collapsed `70.21× → 15.15×` (`DD 73.4%`); `100/100`: `56.47× → 19.66×` | High 52w correlation still lagged V-bottom/recovery transitions and caused severe mid-cycle whipsaws. |
| **`NUPL_4Y` added to Smart Purged-IC** | `20/30/50` cut `70.21× → 38.84×` (`2021–26`: `6.93× → 3.84×`) | Redundant with `CostBasis4Y` while suppressing extreme readings (`100/100`: `56.47× → 32.72×`). |
| **`Funding Rate` (`>0.03` overheat penalty or sub-index)** | Weak 52w IC (`-0.047` / `-0.139`); overheat `+5pt` cut `70.21× → 40.69×` (`2021–26`: `6.93× → 4.02×`) | Prematurely exited mid-bull euphoria surges (spring 2024) months before cycle peak. |
| **`aSOPR`, `Fear & Greed`, `Mayer Multiple` (52w cycle sub-indices)** | Flat/wrong 52w IC (`+0.002 .. +0.056` in `2017+` and `2021+`) | Fast mean-reverting sentiment/profit ratios hold zero 1-year predictive signal. |
| **`LTH Supply 13w ROC`** | Sign flipped between cycles (`-0.103` in `2017+` vs `+0.279` in `2021+`) | Unstable cross-cycle relationship (ETF custody shifts vs retail distribution). |
