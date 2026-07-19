# BitcoinScore — Математична карта скорингового конвеєра

**Статус**: Фінальна архітектура v2 (structural/vectorial)
**Замінює**: flat_avg через всі 16 метрик (V3.1)

---

## Позначення

| Символ | Значення |
|--------|----------|
| k | метрика |
| N[k] ∈ [0,100] | нормалізований ризик-скор метрики |
| u[k] ∈ [0.1,1] | корисність метрики (utility weight) |
| w_bot, w_neu, w_top | ваги фаз, сума=1, з HMM + cycle prior |
| F | фінальний скор (0–100) |

---

## Basket склад

```
OC (structural — holder positioning):
  nupl, mvrv_z_score, rhodl_ratio, cvdd_ratio, puell, lth_supply

MS (vectorial — momentum/flow):
  cipherb, mayer_multiple, funding_rate, fear_greed, etf_flows

MC (structural — macro backdrop):
  m2_yoy, yield_curve_spread

CP (structural — cycle clock):
  btc_price_cycle, pi_gap
```

Кожен basket = `util_avg(metrics)` — utility-зважене середнє нормалізованих N[k].

---

## Фазовий механізм (незмінний)

```
1. HMM_phase_model → [hmm_bot, hmm_neu, hmm_top]
2. Halving cycle prior:
     cd  = days_since_last_halving(target_date)
     net = G_top(cd, μ=500, σ=200) − G_bot(cd, μ=900, σ=200)
     prior_top = max(0, net),  prior_bot = max(0, −net),  prior_neu = 1−|net|
3. Dynamic blend:
     L1    = (|hmm_bot−prior_bot| + |hmm_neu−prior_neu| + |hmm_top−prior_top|) / 2
     alpha = L1 × 0.55
     w_* = (1−alpha)×hmm_* + alpha×prior_*  → нормалізація до суми 1
4. Phase label:
     TOP    if w_top ≥ 0.40 and w_top > w_bot
     BOTTOM if w_bot ≥ 0.65 and w_bot > w_top
     else   NEUTRAL
```

---

## Повна формула F

### Крок 1 — CP контекстуалізує OC

```
OC_read = OC × (0.60 + 0.40 × CP/100)
```

Логіка: OC=70 на початку циклу (CP≈0) → OC_read=42 (менш небезпечно).
        OC=70 на піку (CP≈100) → OC_read=70 (повна небезпека).

### Крок 2 — F_structural ("де ми є")

```
w_mc        = w_bot×0.05 + w_neu×0.25 + w_top×0.05
F_structural = (1 − w_mc) × OC_read + w_mc × MC
```

Макро (MC) має малу вагу на обох екстремах (контрциклічний характер).
Основний сигнал — OC_read, масштабований CP.

**[BOTTOM only] TiZ blend:**
```
if tiz_score is not None:
    F_structural = (1 − 0.20×w_bot) × F_structural + 0.20×w_bot × tiz_score
```

**OC coherence dampening (тільки на structural):**
```
oc_coherence = correlation_consistency(OC_metrics)   ∈ [0,1]
coh_floor    = 0.70×w_bot + 0.45×w_neu + 0.55×w_top
coh_factor   = coh_floor + (1 − coh_floor) × oc_coherence
neutral_s    = 26×w_bot + 50×w_neu + 68×w_top
F_structural = neutral_s + (F_structural − neutral_s) × coh_factor
```

Якщо OC метрики не узгоджені → F_structural тягнеться до neutral_s.

### Крок 3 — F_vectorial ("куди рухаємось")

```
div = max(0, CP − OC_read)          ← ціна обігнала context-adjusted on-chain
w_ms  = w_bot×0.30 + w_neu×0.60 + w_top×0.80
w_div = w_top × 0.40
F_vectorial = (w_ms × MS + w_div × div) / (w_ms + w_div)
```

Асиметрія max(0, div): коли on-chain випереджає ціну — не знижуємо,
просто не додаємо премію (ризик не зменшується від того що метрики перегріті).

### Крок 4 — Синтез (headroom normalization)

```
phase_blend = w_bot×0.20 + w_neu×0.50 + w_top×1.00
headroom    = 100 − F_structural
F = F_structural + headroom × (F_vectorial/100) × phase_blend
```

Логіка headroom: векторний сигнал заповнює ЧАСТКУ залишкового простору до 100.
Не може вийти за межі [0,100] навіть без явного clamp.

### Крок 5 — Pi Cycle override

```
if pi_cross:  F = max(F, 85)
F = clamp(round(F), 0, 100)
```

---

## Верифікація на ключових датах

| Дата | Ціна | OC | MS | CP | F | Ціль |
|------|------|----|----|-----|---|------|
| 2018-12-15 | $3.2K | ~5 | ~10 | ~5 | **~10** | 5–15 |
| 2021-04-14 | $63K | ~80 | ~75 | ~70 | **~82** | 75–85 |
| 2021-11-10 | $69K | ~70 | ~78 | ~80 | **~80** | 80–90 |
| 2022-11-21 | $15.5K | ~5 | ~10 | ~5 | **~13** | 5–15 |
| 2025-01-20 | $109K | ~76 | ~72 | ~66 | **~80** | 75–85 |
| 2025-10-06 | $124K | ~52 | ~68 | ~78 | **~74** | 70–80 |
| 2026-04-25 | $77.5K | ~30 | ~35 | ~40 | **~38** | 30–50 |

**Примітка**: Jan 2025 (~80) > Oct 2025 ATH (~74) — математично обґрунтовано.
Січень: повна узгодженість OC+CP+MS. Жовтень: дивергенція OC↓ при ціні↑ —
це ризик, але менш *визначений*. Вища ціна ≠ вищий скор автоматично.

---

## Два типи впливу

| Тип | Характер | Домени | Роль у формулі |
|-----|----------|--------|----------------|
| **Структурний** | рівень позиції, лаговий | OC, MC, CP | F_structural |
| **Векторний** | напрямок, моментум | MS, дивергенція | F_vectorial |

---

## Відкриті питання (для наступних сесій)

1. Перенавчити HMM з 2025 ATH як підтвердженим TOP?
2. Чи потрібен backfill scores.json з basket-рівневими скорами?
3. Уточнити параметри w_ms та w_div через backtest на 7 датах.
