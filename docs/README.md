# Bitcoin Buy Risk

**A personal composite index for tracking Bitcoin market positioning — 0 (extreme bottom) to 100 (extreme top).**

---

## What This Is — and What It Is Not

There is no universally correct way to measure the risk of buying Bitcoin. Anyone who claims otherwise is selling something. This index does not attempt to be that.

What it is: a structured, data-driven tool built to inform personal decisions — specifically to reduce the influence of emotion, noise, and short-term market psychology when evaluating whether a given moment is historically cheap or historically expensive relative to past cycles. Whether it will work in future cycles is unknown. Markets evolve, participants evolve, and the signals that mattered in 2018 may matter less in 2030.

The index updates once per day. Its potential value lies in calm, long-horizon positioning — not in timing day-to-day moves.

> *"Be fearful when others are greedy, and greedy when others are fearful."*
> — Warren Buffett

If you are looking for a tool to generate quick profits, this index will not help you.

---

## Philosophy

**Bitcoin price behaviour is rooted in macroeconomics and human psychology — not in patterns drawn on charts.**

This project deliberately excludes the class of technical analysis that searches for predictive lines, fractals, harmonic patterns, and similar constructs. The draw of such tools is understandable: they offer the comfort of a story. But a pattern that appears to work in hindsight, on a hand-picked chart, in a market driven by human emotion, is not a reliable signal — it is a coincidence with a compelling visual.

What is retained from technical analysis is narrow, deliberate, and focused on momentum and capital flow:

- **Cipher B** on the weekly timeframe is included as the primary price and momentum oscillator. The implementation here is a customized approximation that integrates:
  - **WaveTrend (WT)**: A momentum oscillator tracking trend waves (WT1 and WT2).
  - **Money Flow Index (MFI)**: A volume-weighted measure of capital inflow/outflow, capturing institutional rotation.
  - **Fast Weekly Divergences**: Immediate detection of bullish or bearish momentum divergence on a weekly scale (1-week detection window, max peak age 3 weeks). Active divergences apply a $\pm 12$ risk modifier to promptly capture cycle turning points without lag.

On-chain metrics occupy the other half of the index. They offer a window into the actual economic behaviour of buyers and sellers — what prices people paid, what they are currently sitting on in profit or loss, how liquidity is rotating. These signals have a meaningful basis in economic theory. They are also a measure of *human* behaviour, which means they are frequently irrational, subject to feedback loops, and not reliably predictive — including, notably, your own behaviour when reading them.

---

## How the Index Is Constructed

The index is a weighted composite of **10 indicators** across two groups:

| Group | Weight in final score | Indicators |
|---|---|---|
| **On-Chain (OC)** | 50% | Addresses in Profit ×25%, RHODL Ratio ×20%, MVRV Z-score ×20%, CVDD Ratio ×20%, NUPL ×15% |
| **Tech / Macro** | 50% | Cipher B ×50%, Fear & Greed ×20%, Pi Cycle Top ×10%, Real Yield ×10%, Global M2 YoY ×10% |

### Metric Mapping & Calibration
To keep the composite index robust and stable, the macro and technical metrics are mapped directly:
- **Global M2 YoY**: High year-over-year expansion of aggregated global M2 liquidity indicates stimulative monetary policy (increased system liquidity), pushing asset risk higher (direct mapping: higher YoY M2 = higher risk score).
- **Real Yield**: Measured via US 10-Year TIPS Real Yield. Higher real yields represent tight money and higher risk for risk assets like Bitcoin (direct mapping: higher yield = higher risk score).

**Removed indicators (with reasons):**
- **SOPR**: Removed due to weak differentiation and high noise in 2024–2025 cycles.
- **SMC (Smart Money Concept)**: Removed due to a ~40% false bottom rate in bear markets.
- **DXY**: Superseded by the more direct US 10Y TIPS Real Yield metric.
- **Geopolitical Risk**: Excluded due to exhibiting <10% explanatory weight in historical backtests.

The two groups are also displayed as separate sub-scores for readers who weight one approach more than the other.

**On weight distribution:** the relative weights between indicators were assigned based on informed assumptions, not on a formal optimisation study. They reflect a view of how each signal *should* contribute to a meaningful reading — not a result derived from backtesting alone. This is a known limitation.

---

## Retrospective Performance (2018–2026)

The table below lists historical risk score outcomes under the updated composite model (which includes the MFI-enhanced Cipher B with fast divergences and removes SOPR/SMC):

| Date | Event | BTC Price | Final Score |
|---|---|---|---|
| **2018-12-15** | Cycle bear bottom | $3,200 | **14** |
| **2020-03-13** | COVID crash | $3,800 | **32** |
| **2022-06-18** | Capitulation dip | $17,600 | **20** |
| **2022-11-21** | FTX cycle bottom | $15,500 | **18** |
| **2021-04-14** | Spring ATH | $63,500 | **91** |
| **2021-11-10** | Nov 2021 ATH | $69,000 | **86** |
| **2024-03-14** | 2024 ATH | $73,500 | **85** |
| **2025-09-29** | 2025 ATH (Intraday) | $129,000 | **72** |

Average score at cycle bottoms (including COVID crash): **21.0 / 100**
Average score at cycle bottoms (excluding COVID crash): **16.0 / 100**
Average score at confirmed cycle tops: **83.5 / 100**

The peak scores across cycles (91 → 86 → 85 → 72) reflect a maturing market — lower speculative excess at each top, not a failure of the index. The sell zone is not fixed; it shifts with the market's structure.

---

## Premium Dashboard Interface (UI Option 2)

The web dashboard implements a modern, premium design system focused on usability and sleek aesthetics:
- **Visuals**: A glassmorphic dark-mode theme (`backdrop-filter`) built on the **Outfit** Google Font. Supports automatic light-mode fallback matching system preferences.
- **Horizontal Risk Pointer (Option 2)**: The final composite score is represented by a linear horizontal track transitioning from green (Buy) to yellow (Neutral) to red (Sell). A floating pointer-marker animates smoothly to the score position using a `cubic-bezier` elastic transition, showing a tooltip with the risk category and precise value.
- **Neon Glow Charts**: Transparency-backed historical charts render risk trends and Bitcoin price over time with customized canvas glows (`shadowBlur`).
- **Freshness Monitor**: A warning banner automatically appears at the top of the interface if the cached data file is older than 36 hours.

---

## Limitations

- **This is one person's tool, not financial advice.** Do not make financial decisions based solely on this index.
- **Weight calibration** is assumption-based, not research-derived.
- **Cipher B** is an approximation of the original indicator, not an exact reproduction.
- **Global M2 data** is scraped live from MacroMicro, tracking the combined M2 YoY change of the four major central banks (Fed, ECB, BOJ, PBOC) denominated in USD.
- **Past performance** across 8 cycles does not guarantee the index will remain calibrated in future cycles.
- **Nobody knows** what the market will do tomorrow. Distrust anyone who claims otherwise with confidence.

---

## Project Structure

- `scraper/` — automated metric collection (Playwright + API wrappers, runs via GitHub Actions)
- `data/` — generated `data.json` and historical snapshots
- `web/` — static dashboard (`index.html`)
- `docs/` — this file, retrospective analysis, and audit documentation
- `tools/` — developer and backtesting utilities
