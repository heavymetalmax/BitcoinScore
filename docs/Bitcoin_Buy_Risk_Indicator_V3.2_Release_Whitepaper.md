# Bitcoin Buy Risk Indicator V3.2: Technical Whitepaper & Release Guide

## Abstract
The Bitcoin Buy Risk Indicator (V3.2) is a lookahead-free, machine-learning-enhanced macro risk index calibrated on a 0–100 scale. It serves as a quantitative compass for long-term capital allocation, identifying cyclical bottoms and tops by synthesizing on-chain indicators, global macro liquidity, market sentiment, and spot ETF flows. 

Through the combination of regularized phase classification, dynamic utility weighting, and the new **Score-based Trailing Stop Decision Engine**, the indicator establishes an actionable framework that outpaces traditional Buy & Hold strategies by capturing macro trend expansions while mitigating severe drawdown periods.

---

## 1. What This Is — and What It Is Not

There is no universally correct way to measure the risk of buying Bitcoin. Anyone who claims otherwise is selling something. This index does not attempt to be that.

What it is: a structured, data-driven tool built to inform personal decisions — specifically to reduce the influence of emotion, noise, and short-term market psychology when evaluating whether a given moment is historically cheap or historically expensive relative to past cycles. Whether it will work in future cycles is unknown. Markets evolve, participants evolve, and the signals that mattered in 2018 may matter less in 2030.

The index updates once per day. Its potential value lies in calm, long-horizon positioning — not in timing day-to-day moves.

> *"Be fearful when others are greedy, and greedy when others are fearful."*
> — Warren Buffett

If you are looking for a tool to generate quick profits, this index will not help you.

---

## 2. Philosophy

**Bitcoin price behaviour is rooted in macroeconomics and human psychology — not in patterns drawn on charts.**

This project deliberately excludes the class of technical analysis that searches for predictive lines, fractals, harmonic patterns, and similar constructs. The draw of such tools is understandable: they offer the comfort of a story. But a pattern that appears to work in hindsight, on a hand-picked chart, in a market driven by human emotion, is not a reliable signal — it is a coincidence with a compelling visual.

What is retained from technical analysis is narrow, deliberate, and focused on momentum and capital flow:
*   **Cipher B** on the weekly timeframe is included as the primary price and momentum oscillator. The implementation here is a customized approximation that integrates:
    *   **WaveTrend (WT)**: A momentum oscillator tracking trend waves (WT1 and WT2).
    *   **Money Flow Index (MFI)**: A volume-weighted measure of capital inflow/outflow, capturing institutional rotation.
    *   **Fast Weekly Divergences**: Immediate detection of bullish or bearish momentum divergence on a weekly scale (1-week detection window, max peak age 3 weeks). Active divergences apply a $\pm 12$ risk modifier to promptly capture cycle turning points without lag.

On-chain metrics occupy the other half of the index. They offer a window into the actual economic behaviour of buyers and sellers — what prices people paid, what they are currently sitting on in profit or loss, how liquidity is rotating. These signals have a meaningful basis in economic theory. They are also a measure of *human* behaviour, which means they are frequently irrational, subject to feedback loops, and not reliably predictive — including, notably, your own behaviour when reading them.

---

## 3. Core Mathematical & Technical Architecture

The indicator processes daily incoming market feeds through a causal, lookahead-free 5-stage pipeline:

```mermaid
graph TD
    A[Raw Metrics Scraper] --> B[Causal Point-in-Time Normalization]
    B --> C[ML-Driven Phase Classifier]
    C --> D[Dynamic Utility Weights]
    D --> E[Phase-Aware Coherence Dampening]
    E --> F[Hybrid Signal Orchestrator]
    F --> G[Trading Observer State Machine]
```

### Stage 1: Causal Point-in-Time Normalization
To prevent lookahead bias (historically leaking future highs/lows into the past), all indicators are dynamically scaled using a causal percentile ranking:
*   **Adaptive Metrics** (`nupl`, `mvrv_z_score`, `cvdd_ratio`, `mayer_multiple`): Normalized against a rolling 4-year (1460-day) lookback window.
*   **Formula**: Let $x_t$ be the raw metric value at day $t$. The rolling percentile rank $P_t$ is:
    $$P_t = \frac{1}{1460} \sum_{i=0}^{1459} I(x_{t-i} < x_t)$$
    where $I(\cdot)$ is the indicator function.
*   **Blending**: The final normalized value is a 50/50 blend of this causal percentile rank and a fixed mathematical sigmoid mapping to ensure structural stability during extreme expansions.

### Stage 2: ML-Driven Phase Regimes
A regularized Logistic Regression classifier categorizes the market regime on a daily basis.
*   **Input Features**: A 22-dimensional wave vector containing the current normalized values of 11 indicators and their 11-day lookback momentum deltas ($\Delta x_t = x_t - x_{t-11}$).
*   **Regime Weights**: Outputs three continuous probability weights representing phase regimes:
    *   $w_{\text{bottom}}$: Probability of being in a cyclical accumulation bottom.
    *   $w_{\text{top}}$: Probability of being in a blow-off top distribution phase.
    *   $w_{\text{neutral}}$: Probability of mid-cycle consolidation.

### Stage 3: Dynamic Utility Weights
Indicators do not have static weights. Instead, each metric has a relevance profile determining its utility coefficient $U_i \in [0.1, 1.0]$ based on the active phase:
$$U_i = w_{\text{top}} \times \text{Profile}_i[\text{TOP}] + w_{\text{bottom}} \times \text{Profile}_i[\text{BOTTOM}] + w_{\text{neutral}} \times \text{Profile}_i[\text{NEUTRAL}]$$

*   **Bottom Regime**: On-chain capitulation metrics (`cvdd_ratio`, `nupl`, `puell_multiple`) gain maximum utility ($1.0$), while technical indicators and volatility measures are heavily suppressed.
*   **Top Regime**: Technical oscillators (`cipherb`), funding rates, sentiment (`fear_greed`), and price extensions (`mayer_multiple`) gain maximum utility ($1.0$).

### Stage 4: Phase-Aware Coherence Dampening
When on-chain indicators diverge (high standard deviation among indicators), the raw average is dynamically pulled towards a phase-appropriate target rather than a fixed neutral midpoint (50):
*   **`BOTTOM` Phase Target**: **30** (keeps the score in the accumulation/buy zone despite minor indicator deviations).
*   **`TOP` Phase Target**: **70** (keeps the score in the warning/caution zone).
*   **`NEUTRAL` Phase Target**: **50**.

The dispersion adjustor is calculated using a Coherence Factor $C \in [0, 1]$ based on the standard deviation of mapped metrics:
$$\text{final\_score} = \text{target} + (\text{raw\_avg} - \text{target}) \times C$$

### Stage 5: Hybrid Signal Orchestrator
The Orchestrator combines the composite indicator score with the Wave Resonance (WR) vector:
*   **Agreement Factor**: Computes the directional dot-product alignment between V3.2 score trends and Wave Resonance. High agreement boosts conviction.
*   **Time-in-Zone (TiZ) Maturity Gate**: Caps bottom signals during the early days of a crash. If the bottom phase is active but duration is $< 25\%$ of calibration ($\sim 50$ days), the flag is overridden to `EARLY_ZONE` to prevent catching a falling knife.

---

## 4. Advanced Divergence Overrides (Preventing Fake Cooldowns)

A primary risk for long-term indicators is the **Fake Cooldown** at market tops: price remains near all-time highs, but momentum indicators drop, causing a naive index to cool down (e.g. dropping from 85 to 60) and signaling a fake buying opportunity.

V3.2 addresses this via **Causal Bearish & Bullish Divergence Detection**:
*   **Bearish Divergence**: If the BTC price is within 3% of its lookback maximum, but the Risk Score has decayed by $\ge 8$ points from its local peak while remaining elevated ($\ge 58$):
    *   The orchestrator triggers `bear_div = True`.
    *   It overrides the score: $\text{meta\_score} = \max(\text{meta\_score}, 82)$.
    *   It locks the status flag to `PROBABLE_TOP` or `CONFIRMED_TOP`.
*   **Bullish Divergence**: If the price is within 3% of its lookback minimum, but the Risk Score has recovered by $\ge 8$ points from its local bottom:
    *   The orchestrator overrides the score to stay low: $\text{meta\_score} = \min(\text{meta\_score}, 25)$.
    *   It locks the status flag to `PROBABLE_BOTTOM`.

---

## 5. The Trading Observer: Trailing Score Decision Engine

To convert the continuous Risk Score (0-100) into actionable trade executions, the backend implements a **Trading Observer State Machine**:

```mermaid
stateDiagram-v2
    [*] --> CASH : Initial State (100% Fiat)
    CASH --> HOLD_BTC : ML Risk Score ≤ 25 (BUY Trigger)
    HOLD_BTC --> TRAILING_EXIT : ML Risk Score ≥ 60 (Trailing Stop Active)
    TRAILING_EXIT --> HOLD_BTC : Risk Score sets a new local high (M)
    TRAILING_EXIT --> CASH : Risk Score ≤ M - 5 (SELL Trigger)
```

### The Backtest: Trailing Score vs. Trailing Price Stop
Using a starting capital of **$1,000** in December 2018, we compared different exit strategies on historical data through March 2026:

| Exit Strategy | Final Capital | Total Return | Trades | Peak Capture Example (2021 Double Top) |
| :--- | :---: | :---: | :---: | :--- |
| **Baseline (Sell immediately at Score $\ge 60$)** | **$171,940** | +17,094% | 10 | Sold early at **$35,404** (missed peak extension) |
| **Price Trailing Stop (10%)** | **$152,009** | +15,100% | 9 | Volatility triggered premature exit |
| **Price Trailing Stop (20%)** | **$54,474** | +5,347% | 9 | Gave back too much profit on corrections |
| **Score Trailing Stop (5 pt drop)** | **$313,479** | **+31,247%** | 10 | Captured peak rollover; sold at **$66,001** |

### Why Score-based Trailing Stops Outperform
Price-based trailing stops are vulnerable to Bitcoin's intraday volatility wicks. In contrast, the **Risk Score** represents on-chain and macro structure. By letting the score run as high as it wants (often staying in the 80–98 range for weeks during a blow-off top) and exiting only when the score drops by **5 points** from its running maximum, the strategy captures the vertical parabolic blow-off phase while remaining protected from early exits.

---

## 6. Historical Backtest Milestones (V3.2 Precision)

The table below demonstrates V3.2's precision at historical cyclical bottom and peak dates:

| Milestone Date | Market Event | BTC Price | V3.2 Score | Action / Interpretation |
| :---: | :--- | :---: | :---: | :--- |
| **2018-12-15** | Bear Market Bottom | $3,200 | **25** | **BUY** (Threshold reached) |
| **2019-06-26** | Mid-Cycle Top | $13,880 | **80** | **SELL** (Triggered via trailing exit) |
| **2020-03-13** | COVID Liquidity Crash | $3,800 | **26** | **BUY** (Extreme OPPORTUNITY) |
| **2021-04-14** | Spring ATH | $63,500 | **88** | **DISTRIBUTION** (Severe caution) |
| **2021-07-20** | Summer Consolidation | $29,800 | **44** | **HOLD** (Neutral accumulation) |
| **2021-11-10** | Double Top ATH | $69,000 | **80** | **SELL** (Triggered via score rollover) |
| **2022-06-18** | Three Arrows Capitulation | $17,600 | **24** | **BUY** (Accumulation active) |
| **2022-11-21** | FTX Exchange Collapse | $15,500 | **16** | **BUY** (Maximum Opportunity) |
| **2024-03-14** | Halving Anticipation ATH | $73,500 | **82** | **SELL** (Triggered via score rollover) |
| **2025-09-29** | Cycle Peak | $129,000 | **68** | **SELL** (Triggered via score rollover) |
| **2026-06-15** | Today's Live Valuation | $65,703 | **23** | **BUY / DCA ACCUMULATION** |

---

## 7. Practical Implementation & Release Roadmap

### 1. Web & API JSON Payload Integration
The active backend calculations are stored in the public `data.json` schema, serving as the master feed for web dashboards:
```json
{
  "timestamp": "2026-06-15T10:14:59Z",
  "btc_price": 65703,
  "final_score": 23,
  "v3_phase": "BOTTOM",
  "trading_signals": {
    "state": "HOLD_BTC",
    "last_action": "BUY",
    "last_action_date": "2026-02-04",
    "last_action_price": 73166,
    "trailing_trigger_score": null,
    "peak_score_in_run": 0
  }
}
```

---

## 8. The Rower's Metaphor: Rowing into the Unknown

To understand how to navigate using this index, consider the metaphor of academic rowing (a discipline practiced actively by the creator of this indicator, from which this perspective is directly born). 

When you sit in a rowing shell, you propel yourself forward but face entirely backward. You move into the future, yet your eyes can only see where you have already been—the past. The riverbed of the market is winding, the currents are strong, and hidden rocks lie beneath the surface. You can only guess how the river will turn next by looking at the contours of the banks you have already passed and the flow of the water behind you. You cannot turn your head to look forward. 

Therefore, you must row in a way that moves you toward your destination while keeping the visible riverbanks in sight. Yet, even the most precise calculations of the past are no guarantee that the path ahead will remain calm. 

This indicator is a route guide for a backward-facing rower. It tries its best to map the current and the banks, but it can never replace your own eyes, your mind, your pace, and your personal sense of safety and danger.

---

## 9. Limitations & Personal Disclaimer

*   **This is a personal composite index, not financial advice.** Do not make allocation decisions based solely on this tool.
*   **The weight calibration** and model targets represent optimizations on historical data which may not repeat.
*   **Cipher B** is an approximation of the original indicator, not an exact reproduction.
*   **Past performance** across historical cycles does not guarantee that the index will remain calibrated or yield profits in future market regimes.
*   **Nobody knows** what the market will do tomorrow. Distrust anyone who claims otherwise with confidence.
