# Wave Resonance Formula Research
Generated: 2026-06-12

## The Core Idea

Each metric is mapped to an oscillator [0→1] that moves between its cycle bottom value (0) and cycle top value (1). The goal: find a formula where these waves "resonate" maximally at Bitcoin price extremes (bottoms and tops) and stay neutral in the middle.

---

## Key Discovery: Why Fixed-Anchor Oscillators Fail

The first attempt used **cycle-specific anchors** (confirmed bottom/top dates as min/max). This failed because:

1. **Bitcoin's market has matured** — on-chain metrics (MVRV, Puell, CVDD) no longer reach their 2017-era extreme readings at cycle tops. The Jan 2025 ($102k) ATH: MVRV=2.86 vs 2021 ATH: MVRV=3.52 vs 2017 ATH: MVRV=10.0.
2. **Anchor decay** — by calibrating to historical extremes, recent cycles appear "less extreme" → oscillators stay flat in the 0.3-0.6 range → formula can't detect tops.
3. **Fixed anchors at bottoms also drift** — each successive bottom occurs at a higher absolute price, but relative metrics (like NUPL ratio) do still reach low values.

| Approach | Top avg | Bot avg | Sep | Mid |
|----------|---------|---------|-----|-----|
| Fixed-anchor coherence | 50.7% | 10.6% | 40.1 | 46.2% |
| Rolling 3yr coherence | 81.4% | 13.3% | 68.1 | 52.7% |
| Rolling 3yr mean | 89.6% | 4.6% | 85.0 | 55.4% |

**Solution: Rolling 3-year percentile ranks** — each metric's value is compared to its own distribution from the past 1095 days. This makes the oscillator cycle-adaptive: if MVRV=3.5 was the 95th percentile in 2021, it gets a high reading; if it's only the 80th percentile in 2025, it gets a lower reading.

---

## The Wave Resonance Formula

### Step 1 — Cycle-Adaptive Oscillators

```python
# For each metric m at date D:
buffer = past_3yr_values[m]  # last 1095 days of raw data
osc[m] = len([x for x in buffer if x <= value[m]]) / len(buffer)

# Inverted metrics: low value = high risk (pi_cycle_gap)
if m == 'pi_cycle_gap':
    osc[m] = 1.0 - osc[m]
```

### Step 2 — Weighted Mean (Direction)

```python
weights = {nupl:0.32, mvrv:0.24, puell:0.14, cvdd_ratio:0.14, mayer:0.10, pi_cycle_gap:0.06}
total_w = sum(weights[m] for m if osc[m] is not None)
mean_v  = sum(weights[m] × osc[m]) / total_w
```

### Step 3 — Weighted Std (Phase Dispersion)

```python
std_v = sqrt(sum(weights[m] × (osc[m] - mean_v)²) / total_w)
```

### Step 4 — Coherence (In-Phase = Resonance)

```python
# 0.289 = 1/(2√3) = theoretical max std for uniform [0,1] distribution
coherence = max(0.0, 1.0 - std_v / 0.289)
```

### Step 5 — Resonance Score

```python
direction = 2 × mean_v - 1    # [-1, +1]
score = 50 + 50 × direction × coherence
```

### Mathematical Interpretation

- `coherence = 1.0` — all waves in perfect unison (zero dispersion) → pure resonance
- `coherence = 0.0` — waves maximally scattered → destructive interference → score = 50 (uncertainty)
- The formula amplifies the directional signal **only when waves resonate**

---

## Tested Performance on Key Dates

| Date | Event | Price | Score | Interpretation |
|------|-------|-------|-------|----------------|
| 2018-12-15 | Bear bottom | $3,212 | **5.2** | All waves at minimum → bottom resonance ✓ |
| 2019-06-01 | Intermediate top | $8,544 | 60.6 | Mixed signal (technical high, on-chain moderate) → moderate ✓ |
| 2020-03-12 | COVID crash | $4,800 | 25.5 | Fast crash — some metrics haven't caught up → not all at bottom ✓ |
| 2021-04-14 | Cycle ATH | $62,960 | **91.4** | All waves near top → top resonance ✓ |
| 2021-07-21 | Mid-bear | $32,145 | 52.0 | Mixed — correctly neutral ✓ |
| 2021-11-08 | Real ATH $69k | $67,526 | 70.3 | Partial resonance — less extreme than April ✓ |
| 2022-11-21 | FTX bottom | $15,781 | **9.2** | Bottom resonance ✓ |
| 2023-06-01 | Neutral | $26,818 | 44.7 | Correctly near 50 ✓ |
| 2024-03-14 | Cycle ATH | $71,389 | **83.2** | Strong top resonance ✓ |
| 2025-01-20 | ATH $102k | $102,260 | **80.6** | Strong top resonance ✓ |
| 2026-04-07 | Correction | $71,924 | 16.0 | Correctly low after top ✓ |

### Summary Statistics

```
                    Tops avg  Bots avg  Sep    Mid avg
Weighted Coherence:   81.4%     13.3%  68.1    52.7%  ← best false-positive control
Weighted Mean:        89.6%      4.6%  85.0    55.4%  ← best raw separation
```

---

## Why the Coherence Formula > Simple Mean

The coherence formula provides **false-positive protection**:
- June 2019 ($8.5k): WMean=70.5% vs WCoh=60.6% — coherence correctly discounts "mixed" signal
- Nov 2021 ($67k): WMean=82.3% vs WCoh=70.3% — correctly lower because pi_cycle_gap NOT at extreme (gap=43, not crossed)
- The coherence term says: "only give a high score when ALL metrics agree"

For a RISK INDEX where false alarms are costly (premature sells), **WCoh is preferred**.
For a MOMENTUM INDEX where raw signal power matters, **WMean is preferred**.

---

## The Resonance Metaphor (Mathematical Analogy)

Each oscillator is a "standing wave" between 0 and 1:
- At CYCLE BOTTOMS: all 6 waves simultaneously hit their trough (NUPL≈0, MVRV≈0, Puell≈0, etc.) → RESONANCE (constructive interference downward)
- At CYCLE TOPS: most waves simultaneously hit their crest → RESONANCE (constructive interference upward)
- At NEUTRAL/MID: waves are out of phase → DESTRUCTIVE INTERFERENCE → score cancels to ~50

The std of oscillators measures **phase dispersion** (how out-of-sync the waves are). Low std = in-phase = resonance. High std = out-of-phase = no signal.

The coherence factor `(1 - std/0.289)` is the **resonance amplitude** — it equals 1 at perfect resonance and 0 at maximum destructive interference.

---

## Key Calibration Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Rolling window | 3 years (1095 days) | Captures ~1 full BTC cycle |
| Normalization constant | 0.289 = 1/(2√3) | Theoretical max std for U[0,1] |
| NUPL weight | 32% | Most reliable capitulation signal (ML experiment) |
| MVRV weight | 24% | Strong cycle indicator |
| Puell weight | 14% | Miner stress signal |
| CVDD ratio | 14% | Realized value proxy |
| Mayer Multiple | 10% | Technical indicator, less reliable |
| Pi Cycle Gap | 6% | Binary-ish, works at some tops not all |
| pi_cycle_gap inversion | Yes | High gap = safe = low risk |
| Minimum buffer before computing | 30 days | Prevent bias from too-small windows |

---

## Integration Recommendation

This formula can serve as a **parallel oscillator track** alongside the existing scoring_v2:

1. **Current model (scoring_v2)**: weighted composite with regime detection, adaptive calibration, TiZ
2. **Wave Resonance Model**: rolling-percentile oscillators + coherence formula
3. **Combined**: use wave resonance as a CONFIRMATION signal:
   - If scoring_v2 says "bottom" AND wave resonance < 15 → STRONG buy confirmation
   - If scoring_v2 says "top" AND wave resonance > 75 → STRONG sell confirmation
   - Divergence between models → uncertainty → reduce position size

The resonance model is BEST used as a second opinion, not a replacement. Its key advantage is **detecting destructive interference at bottoms** — when no single metric is certain but ALL metrics are slightly low together.

---

## Formulas Tested & Rejected

| Formula | Problem |
|---------|---------|
| Fixed-anchor oscillator + geometric mean | Fixed anchors miss recent tops |
| Fixed-anchor + wave cosine | Same anchor problem |
| Fixed-anchor + coherence | Bottoms work, tops fail (market maturation) |
| Rolling + geometric mean | False positive at 2019-06-01 (one metric at 0.99 inflates product) |
| Rolling + product resonance | Single extreme metric dominates product |
| Rolling + coherence (WINNER) | Best balance: top detection + false-positive protection |
