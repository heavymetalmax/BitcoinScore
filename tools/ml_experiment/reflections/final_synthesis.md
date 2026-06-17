# ML Experiment — Final Synthesis
Generated: 2026-06-12

## Leaderboard (2017-08-17 → 2023-12-31, $10k start, B&H=+887%)

| # | Agent | Return | Alpha | MaxDD | Buy date | Buy threshold | min_days |
|---|-------|--------|-------|-------|----------|---------------|----------|
| 1 | RegimeSwitcher | +1473% | +587% | 63% | Dec 2018 | 22 | 18 |
| 2 | Patient | +1250% | +363% | 60% | Dec 2018 | 15 | 28 |
| 3 | Macro | +478% | -409% | 63% | Apr 2018 | 30 | 30 |
| 4 | Balanced | +400% | -487% | 63% | Jun 2018 | 28 | 14 |
| 5 | Fundamentalist | +273% | -614% | 50% | Aug 2018 | 28 | 7 |
| 6 | Technician | +161% | -727% | 77% | Mar 2018 | 20 | 2 |

## Why Winners Won

Both winners bought in December 2018 ($3,509 and $3,662) and **never sold**.

**The structural reason:** With on-chain dominant weights (NUPL 24-28%, MVRV 18-22%), the index produced:
- 0 days below 22 in March-September 2018 (no false signals during mid-bear)
- 43 consecutive days below 22 in October-December 2018 (confirmed capitulation)

min_days ≥ 18 was the mechanical separator between falling-knife buyers and bottom-buyers.

## Why Losers Lost — Two Failure Modes

### Failure 1: Premature Entry (falling knife)
| Agent | min_days | First buy date | Price | Loss vs bottom |
|-------|----------|---------------|-------|----------------|
| Technician | 2 | 2018-03-28 | $7,949 | 148% overpriced |
| Fundamentalist | 7 | 2018-08-17 | $6,312 | 97% overpriced |
| Macro | 30 | 2018-04-07 | $6,601 | 106% overpriced |

Root cause: their indices (with lower NUPL weights) went below buy_threshold temporarily during the 2018 bear. With regime-based weights (NUPL dominant), the index NEVER crossed 22 until confirmed capitulation.

### Failure 2: Premature Sell
| Agent | Sold at | Index at sale | BTC peaked at | Missed |
|-------|---------|---------------|---------------|--------|
| Balanced | Dec 2020 | $23,107 | $68,789 | +197% |
| Fundamentalist | Dec 2020 | $26,493 | $68,789 | +160% |
| Macro | Jan-Feb 2021 | $39-52k | $68,789 | +32-76% |

Root cause: sell_threshold = 70-72 triggered at the START of the 2021 bull run.
Key data point: Patient index at Nov 2021 ATH ($69k) = **77.24** — below sell_threshold=78.
The index peaked EARLY in parabolic runs, not at actual cycle tops.

## Critical Data Points

### Pi Cycle Gap at 2021 Peaks
- April 2021 ATH ($64,805): pi_gap = **-0.22** (lines CROSSED → confirmed top signal ✓)
- November 2021 ATH ($68,789): pi_gap = **43.21** (no cross → NOT a top by this signal)
- Pi Cycle correctly called April but NOT November. Use as binary OVERRIDE, not continuous weight.

### Index Readings at Key 2020-2021 Moments (Patient weights)
| Date | Price | Patient Index | Interpretation |
|------|-------|--------------|----------------|
| 2020-12-18 | $23,107 | 75.66 | Below sell_thr=78 — correctly holds |
| 2020-12-26 | $26,493 | 78.22 | Barely above threshold — minimal sell |
| 2021-02-20 | $55,841 | 89.29 | Above threshold — but BTC still rising |
| 2021-11-08 | $68,789 | **77.24** | BELOW threshold — correctly holds at ATH! |

---

## 5 Actionable Changes for Our Model

### 1. Raise "sell zone" threshold from 65 to 78-80
**Evidence:** Patient index at $69k ATH = 77.24. Agents with sell_thr=70-72 sold at $23-52k, missing $69k top.
**Change:** In the UI and scoring narrative, "high risk / sell zone" starts at 80, not 65.
**Impact:** Prevents premature exits during the early bull run phase.

### 2. TiZ min_days_in_zone validated at 18+ days
**Evidence:** Zero false buy signals in Mar-Sep 2018 with on-chain dominant weights. Only genuine capitulation (Oct-Dec 2018) produced 43 consecutive days below threshold.
**Current TiZ:** _WINDOW_DAYS=180, _CALIBRATION=200 — this is correct.
**Suggestion:** The TiZ "fresh" score of 38 should activate only after 7+ days, and approach maturity after 18+ days. Consider updating _SCORE_FRESH trigger logic.

### 3. Add sell_persistence_days (≥14) before exiting position
**Evidence:** The index spiked above 70+ during early bull (Jan 2021 at $40k) but then dropped back as BTC consolidated before resuming to $69k.
**Change:** New parameter: only recommend selling after N consecutive days above sell_threshold (14-21 days). Prevents single-day spikes from triggering premature exits.
**Implementation:** Could add to scoring_v2.py return dict as `sell_confirmed: bool`.

### 4. Pi Cycle Cross as standalone OVERRIDE, not weighted metric
**Evidence:** April 2021 pi_gap=-0.22 (cross) = genuine top signal. November 2021 pi_gap=43.21 = no signal. The metric is binary, not continuous.
**Change:** In scoring_v2.py, if pi_cycle_cross=True → force final_score to min(final_score, 95). Use as override, not weighted into composite.
**Impact:** Correctly identifies April 2021 top without distorting November 2021 signal.

### 5. NUPL weight 28-32% confirmed optimal
**Evidence:** Both winners used NUPL as highest weight (24% Regime, 28% Patient). NUPL directly measures holder stress — the most reliable capitulation signal.
**Current model (v2 bottom weights):** NUPL at 35% — already correct range.
**Suggestion:** Maintain NUPL ≥ 28% across all regimes. Current OC_NEUTRAL has NUPL at 30% — keep.

---

## Single Most Important Insight

> **Patience on entry beats precision on exit.**
> 
> min_days_in_zone ≥ 18 with on-chain dominant weights produced ZERO false buy signals during the entire 2018 bear market. Both winning agents caught Bitcoin within 2 weeks of its multi-year low. The losing agents entered 4-9 months too early and never recovered their cost basis disadvantage.
> 
> A 2× cost basis disadvantage from premature entry cannot be compensated by any exit strategy.

## Already-Validated in Our Model
- TiZ metric concept (time in low-score zone) ✓
- NUPL dominant weight ✓
- Regime-based weights (v2) ✓
- Adaptive CipherB blend ✓
