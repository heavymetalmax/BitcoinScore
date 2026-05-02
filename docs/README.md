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

What is retained from technical analysis is narrow and deliberate:

- **Cipher B** on the weekly timeframe is included as the most behaviourally grounded TA indicator in this context. The implementation here approximates the original but does not reproduce it exactly. Keep that in mind when interpreting its signal.
- **SMC (Smart Money Concept)** is similarly approximated. The concept of tracking price relative to confirmed swing highs and lows on the weekly chart has a reasonable structural basis; the specific implementation here should be treated as an approximation.

On-chain metrics occupy the other half of the index. They offer a window into the actual economic behaviour of buyers and sellers — what prices people paid, what they are currently sitting on in profit or loss, how liquidity is rotating. These signals have a meaningful basis in economic theory. They are also a measure of *human* behaviour, which means they are frequently irrational, subject to feedback loops, and not reliably predictive — including, notably, your own behaviour when reading them.

---

## How the Index Is Constructed

The index is a weighted composite of **11 indicators** across two groups:

| Group | Weight | Indicators |
|---|---|---|
| **On-Chain (OC)** | 50% | NUPL, MVRV Z-score, SOPR, Addresses in Profit, RHODL Ratio, CVDD Ratio |
| **Tech / Macro** | 50% | Cipher B (weekly), SMC, Fear & Greed, DXY, Global M2 YoY, Geopolitical Risk |

The two groups are also displayed as separate sub-scores for readers who weight one approach more than the other.

**On weight distribution:** the relative weights between indicators were assigned based on informed assumptions, not on a formal optimisation study. They reflect a view of how each signal *should* contribute to a meaningful reading — not a result derived from backtesting alone. This is a known limitation.

---

## Retrospective Performance (2018–2026)

| Date | Event | BTC | Final Score |
|---|---|---|---|
| 2018-12-15 | Cycle bear bottom | $3,200 | **14** |
| 2020-03-13 | COVID crash | $3,800 | **26** |
| 2022-06-18 | Capitulation | $17,600 | **18** |
| 2022-11-21 | FTX bottom | $15,500 | **16** |
| 2021-04-14 | Spring ATH | $63,500 | **91** |
| 2021-11-10 | Nov 2021 ATH | $69,000 | **86** |
| 2024-03-14 | 2024 ATH | $73,500 | **82** |
| 2025-09-29 | 2025 ATH | $129,000 | **68** |

Average at confirmed cycle bottoms: **18.5 / 100**
Average at confirmed cycle tops: **81.8 / 100**

The declining peak scores across cycles (91 → 86 → 82 → 68) reflect a maturing market — lower speculative excess at each top, not a failure of the index. The sell zone is not fixed; it shifts with the market's structure.

---

## Limitations

- **This is one person's tool, not financial advice.** Do not make financial decisions based solely on this index.
- **Weight calibration** is assumption-based, not research-derived.
- **Cipher B and SMC** are approximations of established concepts, not exact reproductions.
- **Global M2 data** carries a ~6-month reporting lag; a US M2 proxy is used for current readings.
- **Past performance** across 8 cycles does not guarantee the index will remain calibrated in future cycles.
- **Nobody knows** what the market will do tomorrow. Distrust anyone who claims otherwise with confidence.

---

## Project Structure

- `scraper/` — automated metric collection (Playwright + API wrappers, runs via GitHub Actions)
- `data/` — generated `data.json` and historical snapshots
- `web/` — static dashboard (`index.html`)
- `docs/` — this file and retrospective analysis
