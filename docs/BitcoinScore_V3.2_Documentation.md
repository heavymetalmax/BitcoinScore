# BitcoinScore V3.2ML: Master Technical & Financial Documentation

BitcoinScore V3.2ML is a lookahead-free, machine-learning-enhanced Buy Risk Index (0–100 scale) calibrated specifically for macro crypto traders. It measures Bitcoin buy opportunity by blending multi-source indicators (on-chain capitulation, technical analysis, macro liquidity, sentiment, and spot ETF flows) with continuous ML-driven phase classification and dynamic utility weight tuning.

---

## 1. Core Architecture

The system evaluates risk through a 6-stage pipeline:

```mermaid
graph TD
    A[Raw Metrics Scraper] --> B[Causal Normalization]
    B --> C[ML Phase Weights]
    C --> D[Dynamic Utility Weighting + TiZ]
    D --> E[Phase-Aware Coherence Dampening]
    E --> F[DXY Macro Modifier]
    F --> G[Orchestrator V3 Meta-Score]
```

### Stage 1: Causal Point-in-Time Normalization
To prevent lookahead bias during backtesting and live runs, all metrics are normalized to a uniform `0-100` scale using causal percentile ranks.
- Historical data is loaded only up to the target date.
- For adaptive metrics (`nupl`, `mvrv_z_score`, `cvdd_ratio`, `mayer_multiple`), a rolling 4-year window is utilized to compute the percentile rank of the current value.
- The final score is a blend (50/50 default) of this rolling percentile rank and a fixed mathematical mapping.

### Stage 2: ML-Driven Phase Detection
The market's regime is captured by **two independent systems** that together output continuous probability weights:

- **w_bot** (bottom probability): Computed by `bottom_confluence.py` — a data-driven gradient model anchored to confirmed historical bottom dates (`2018-12-15`, `2020-03-13`, `2022-06-18`, `2022-11-21`). Returns a continuous 0–1 probability based on how much the current indicator state resembles confirmed capitulation events.
- **w_top** (top probability): Computed by a **Gaussian Hidden Markov Model (HMM)** (`v3_phase_model.pkl`) trained on on-chain and technical features. Only `probs[2]` (TOP state probability) is used.
- **w_neutral** = 1 − w_bot − w_top (clipped to [0, 1]).

*Implementation: [bottom_confluence.py](file:///Users/max/BitcoinScore/scraper/bottom_confluence.py) · [train_v3_hmm_model.py](file:///Users/max/BitcoinScore/tools/train_v3_hmm_model.py)*

### Stage 3: Dynamic Utility Weights
Metrics do not have static weights. Instead, each metric has a relevance profile determining its utility coefficient $U_i \in [0.1, 1.0]$ based on the active phase:
$$U_i = w_{\text{top}} \times \text{Profile}_i[\text{TOP}] + w_{\text{bot}} \times \text{Profile}_i[\text{BOTTOM}] + w_{\text{neutral}} \times \text{Profile}_i[\text{NEUTRAL}]$$

- During a bottom, on-chain metrics like `cvdd_ratio` or `nupl` gain maximum utility ($1.0$), while macro and technical indicators are heavily suppressed to prevent volatility from skewing the entry signals.
- During a top, technical oscillators (`cipherb`), sentiment (`fear_greed`), and trend deviations (`mayer_multiple`) gain maximum utility.

*Implementation: [utility_evaluator.py](file:///Users/max/BitcoinScore/scraper/utility_evaluator.py)*

### Stage 4: Phase-Aware Coherence Dampening
When on-chain metrics diverge (high standard deviation among indicators), the score is pulled towards a phase-appropriate target instead of a fixed 50. All constants are read from `data/v3_calibration.json`:
- **`BOTTOM` Phase Target**: **26** (ensures score remains in buy range even with minor metric deviations).
- **`TOP` Phase Target**: **68** (ensures score remains in warning/sell range).
- **`NEUTRAL` Phase Target**: **50**.

The neutral target and coherence floor both interpolate continuously across phase weights rather than using discrete phase labels:
$$\text{neutral\_target} = 26 \cdot w_{\text{bot}} + 50 \cdot w_{\text{neutral}} + 68 \cdot w_{\text{top}}$$
$$\text{final\_score} = \text{neutral\_target} + (\text{raw\_avg} - \text{neutral\_target}) \times C$$

*Implementation: [score.py](file:///Users/max/BitcoinScore/scraper/score.py)*

### Stage 5: Orchestrator V3 Integration
The Orchestrator blends the dynamic V3.1ML score with the Wave Resonance (WR) vector:
- **Agreement Mapping**: Continuously calculates directional vector alignment between the V3.1ML score trend and WR.
- **Conviction Pulls**: Pulls the score towards the extreme buy/sell zones when there is high agreement.
- **Regime Flagging**: Generates actionable flags: `CONFIRMED_BOTTOM`, `PROBABLE_BOTTOM`, `NEUTRAL`, `PROBABLE_TOP`, `CONFIRMED_TOP`.

*Implementation: [orchestrator.py](file:///Users/max/BitcoinScore/scraper/orchestrator.py)*

---

## 2. Weight Optimization Framework

The weight profiles are calibrated using a **Trader-focused coordinate descent optimizer**. It optimizes parameters to maximize a fitness function representing a successful macro trader strategy.

### Trader Strategy Rules
- **Buy/Accumulate**: Risk index $\le 30$.
- **Sell/Distribute**: Risk index $\ge 75$.
- **Hold**: Between 30 and 75.

### Objective Function
The optimizer minimizes a compound loss function:
$$\text{Loss} = -1.0 \times \text{Return (\%)} + 4.0 \times \text{Max Drawdown (\%)} + 0.25 \times \text{MSE} + \text{Hinge Loss}$$

Where:
- **Return & Drawdown**: Computed from a full historical simulation of the trader's strategy (starting with $10,000 cash).
- **MSE**: Weighted mean squared error against the 6-month returns target to maintain a smooth curve.
- **Hinge Loss**: Penalizes wrong classifications at critical market turning points (Milestones):
  - Bottom dates (`2018-12-15`, `2020-03-13`, `2022-06-18`, `2022-11-21`): Penalty if score $> 20$.
  - Top dates (`2021-04-14`, `2021-11-10`, `2024-03-14`, `2025-01-20`, `2025-09-29`): Penalty if score $< 80$.

*Implementation: [optimize_v3_relevance.py](file:///Users/max/BitcoinScore/tools/optimize_v3_relevance.py)*

---

## 3. Backtest Milestone Performance

A comparison of historical cycle pivot points demonstrates the precision of the V3.2 calibration:

| Milestone Date | Label | BTC Price | V3.2 Score | Active Zone |
| :---: | :--- | :---: | :---: | :---: |
| **2018-12-15** | 2018 Cycle Bottom | $3,200 | **25** | BOTTOM (Buy) |
| **2020-03-13** | COVID Crash | $3,800 | **26** | BOTTOM (Buy) |
| **2022-06-18** | Capitulation | $17,600 | **24** | BOTTOM (Buy) |
| **2022-11-21** | FTX Bottom | $15,500 | **27** | BOTTOM (Buy) |
| **2021-04-14** | Spring ATH | $63,500 | **88** | TOP (Sell) |
| **2021-11-10** | Nov 2021 ATH | $69,000 | **80** | TOP (Sell) |
| **2024-03-14** | 2024 ATH | $73,500 | **82** | TOP (Sell) |
| **2025-07-17** | CB Weekly Peak | $118,735 | **80** | TOP (Sell) |
| **2026-06-14** | Today's Valuation | $64,279 | **32** | NEUTRAL (Hold/DCA) |

*Verifiable via: [backtest.py](file:///Users/max/BitcoinScore/tools/backtest.py) & [analyze_bottoms.py](file:///Users/max/BitcoinScore/scratch/analyze_bottoms.py)*

---

## 4. Codebase Directory Map

- **Core Scoring Logic**:
  - [score.py](file:///Users/max/BitcoinScore/scraper/score.py): Authoritative V3 engine — normalization, phase weights, TiZ, coherence dampening, DXY modifier, and orchestration wiring.
  - [bottom_confluence.py](file:///Users/max/BitcoinScore/scraper/bottom_confluence.py): Data-driven w_bot gradient probability model.
  - [utility_evaluator.py](file:///Users/max/BitcoinScore/scraper/utility_evaluator.py): Evaluates continuous utility coefficients based on weights.
  - [orchestrator.py](file:///Users/max/BitcoinScore/scraper/orchestrator.py): Blends V3 score and Wave Resonance into a Meta-Score.
- **Optimization & ML training**:
  - [optimize_v3_relevance.py](file:///Users/max/BitcoinScore/tools/optimize_v3_relevance.py): Trader strategy simulation, Hinge Loss, and parameter coordinate descent.
  - [train_v3_hmm_model.py](file:///Users/max/BitcoinScore/tools/train_v3_hmm_model.py): Gaussian HMM trainer for TOP phase probability (w_top).
- **Analysis & Verification**:
  - [backtest.py](file:///Users/max/BitcoinScore/tools/backtest.py): Core historical backtest harness.
  - [evaluate_today.py](file:///Users/max/BitcoinScore/scratch/evaluate_today.py): Evaluates and logs details for the current day's live scraper data.
  - [analyze_bottoms.py](file:///Users/max/BitcoinScore/scratch/analyze_bottoms.py): Analyzes scores specifically at historical cycle bottom dates.
