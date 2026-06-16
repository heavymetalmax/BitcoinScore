# Version Comparison: BitcoinScore V1 vs. BitcoinScore V3.2

This document provides a comprehensive comparison between the legacy **BitcoinScore V1** and the newly released **BitcoinScore V3.2**. It highlights the core architectural shifts, mathematical improvements, and signal reliability advancements.

---

## 1. Architectural Overview

The fundamental difference lies in the evolution from a **static, rigid aggregator** to an **adaptive, regime-aware framework**. 

| Architectural Feature | Version 1 (Legacy) | Version 3.2 (Active Default) |
| :--- | :--- | :--- |
| **Weighting Model** | **Static weights**: Fixed allocations (50% On-Chain, 50% Technical/Macro) regardless of market conditions. | **Dynamic Utility weights**: Metric weights scale dynamically ($U_i \in [0.1, 1.0]$) based on the active market regime. |
| **Regime Detection** | **None**: No concept of cycle phase; indicators are treated identically at cycle tops and bottoms. | **ML Phase Classifier**: Regularized Logistic Regression model classifies the market into `BOTTOM`, `NEUTRAL`, or `TOP` probabilities. |
| **Indicator Calibration** | **Static linear/sigmoid maps**: Constant bounds that do not adapt to Bitcoin's structural maturity. | **Adaptive Calibration**: Blends fixed piecewise mappings with a rolling 4-year point-in-time percentile rank (50/50 blend). |
| **Indicator Dispersion** | **Unmanaged**: Diverging metrics dilute the overall score, pushing it towards a neutral 50. | **Phase-Aware Coherence Dampening**: Pulls the score towards phase targets (30 for Bottom, 70 for Top) when metrics diverge. |
| **Trend Modulators** | **None**: Hardcoded tactical alert thresholds. | **Active Modulators**: Incorporates Spot ETF flows, and the Recovery Entry Window (TiZ modulator). |

---

## 2. Deep Dive: Key Mathematical Evolutions

### A. Adaptive Calibration vs. Static Bounds
Over consecutive market cycles, Bitcoin's volatility and the amplitude of its macro metrics have decayed due to institutionalization. 
*   **V1** used static bounds (e.g., mapping NUPL linearly using `(v + 50) / 150 * 100`). As the peak amplitude of NUPL decreased over time, V1 became unable to trigger extreme risk zones.
*   **V3.2** resolves this by introducing a **Rolling 4-year Percentile Rank**. For `nupl`, `mvrv`, and `mayer_multiple`, the fixed mapping is blended 50/50 with its causal percentile rank over the preceding 4 years:
    $$\text{Score}_{\text{blended}} = 0.5 \times \text{Score}_{\text{fixed}} + 0.5 \times \text{Percentile}_{4\text{Y}}$$

### B. Regime-Aware Utility Weighting
Not all metrics are useful at all times. For example, during deep capitulation, macro interest rates are less critical than on-chain realized value.
*   **V1** maintained the same weights for all indicators. If macro indicators were neutral during a cycle bottom, they diluted the strong buy signal.
*   **V3.2** adjusts the weight $U_i$ of each metric $i$ dynamically based on regime probabilities ($w_{\text{bot}}$, $w_{\text{top}}$, $w_{\text{neutral}}$):
    $$U_i = w_{\text{top}} \times \text{Profile}_i[\text{TOP}] + w_{\text{bot}} \times \text{Profile}_i[\text{BOTTOM}] + w_{\text{neutral}} \times \text{Profile}_i[\text{NEUTRAL}]$$

### C. Phase-Aware Coherence Dampening
When indicators disagree (e.g., some point to a bottom while others remain neutral), a simple average dilutes the signal.
*   **V1** performed simple averages, causing cycle extremes to be missed when single metrics lagged.
*   **V3.2** uses a coherence factor $C$ based on on-chain dispersion to pull the index towards a phase-appropriate target:
    $$\text{final\_score} = \text{Target}_{\text{phase}} + (\text{raw\_avg} - \text{Target}_{\text{phase}}) \times C$$
    *   If the ML model detects a bottom, the target is **30** (accumulation zone). Even if metrics diverge, the score remains in the buy range.

---

## 3. Metric Mapping Specifications

Here is how key indicators are mapped to the 0-100 scale in both versions:

### 1. Net Unrealized Profit/Loss (NUPL)
*   **V1**:
    $$\text{Score} = \text{Math.max}(0, \text{Math.min}(100, \frac{\text{NUPL} + 50}{150} \times 100))$$
*   **V3.2**: Piecewise linear mapping with adaptive calibration blend:
    *   If $\text{NUPL} \le 40.0\%$: $\text{Score}_{\text{fixed}} = 8 + \frac{\text{NUPL} - (-20.0)}{40.0 - (-20.0)} \times 42$
    *   If $\text{NUPL} > 40.0\%$: $\text{Score}_{\text{fixed}} = 50 + \frac{\text{NUPL} - 40.0}{75.0 - 40.0} \times 50$
    *   **Final**: 50/50 blend with rolling 4-year percentile.

### 2. MVRV Z-Score
*   **V1**:
    $$\text{Score} = \text{Math.max}(0, \text{Math.min}(100, \frac{\text{MVRV} + 2}{7} \times 100))$$
*   **V3.2**: Piecewise linear mapping with adaptive calibration blend:
    *   If $\text{MVRV} \le 1.0$: $\text{Score}_{\text{fixed}} = 8 + \frac{\text{MVRV} - (-0.3)}{1.0 - (-0.3)} \times 42$
    *   If $\text{MVRV} > 1.0$: $\text{Score}_{\text{fixed}} = 50 + \frac{\text{MVRV} - 1.0}{5.0 - 1.0} \times 50$
    *   **Final**: 50/50 blend with rolling 4-year percentile.

### 3. Mayer Multiple
*   **V1**: Mapped linearly from $0.5$ to $2.1$:
    $$\text{Score} = \text{Math.max}(0, \text{Math.min}(100, \frac{\text{Mayer} - 0.5}{1.6} \times 100))$$
*   **V3.2**: Same fixed mapping but receives **Adaptive Calibration** (50/50 blend with 4-year rolling percentile) to account for structural changes in trend deviations.

---

## 4. Summary of Benefits

By transitioning from V1 to V3.2:
1.  **Eliminated Dilution**: Active regime detection and coherence dampening ensure that cycle extremes are identified with high conviction, even if a minor indicator is lagging or noisy.
2.  **Adaptive to Market Maturity**: The 4-year rolling percentile rank automatically compensates for the dampening amplitude of Bitcoin cycle swings as the asset class matures.
3.  **Institutional Alignment**: Integration of Spot ETF flows and refined macro indicators provides a more accurate view of today's market drivers compared to V1's pure retail/native-on-chain focus.
