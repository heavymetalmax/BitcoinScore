# Score Processor v2 — Architecture

## Проблема яку вирішуємо

Поточна система (v1) обчислює ризик як зважену суму фіксованих ваг:
`score = 0.30×NUPL + 0.20×MVRV + 0.40×CB + ...`

Три фундаментальні слабкості:
1. **Snapshot** — бачить числа в моменті, не рух хвилі
2. **Фіксовані ваги** — не адаптуються до ситуації
3. **Дивергенції не виявляються** — CB падає при ціновому ATH → система цього не бачить без спеціального коду

---

## Концепція (Score Processor v2)

> Метрики — це осцилятори. Процесор дивиться не на числа в моменті, а на **хвилю**: де вона зараз і куди рухається. Ризик вимірюється як геометрична відстань поточної хвилі від підтверджених екстремумів минулого.

---

## Pipeline

```
Raw Data
    │
    ▼ Layer 1: NORMALIZATION (існуюча)
    │   map_*() + adaptive percentile (4yr rolling)
    │   кожна метрика → [0, 100]
    │
    ▼ Layer 2: SCORE PROCESSOR v2 (новий)
    │   wave_vector = scores + trajectories
    │   score = f(distance_to_tops, distance_to_bottoms)
    │   нуль вільних параметрів
    │
    ▼ Layer 3: ORCHESTRATOR (існуючий, покращений)
        WR blend + TiZ modulator → flag + conviction
```

---

## Layer 2: Score Processor v2

### 2.1 Wave Vector

Для кожної метрики: поточний бал + траєкторія відповідно до швидкості сигналу.

```
SLOW  [30d delta]:  nupl, mvrv_z_score, rhodl_ratio, cvdd_ratio, mayer_multiple
MEDIUM [14d delta]: asopr, etf_flows
FAST   [7d delta]:  cipherb, fear_greed
MACRO  [60d delta]: m2_yoy, yield_curve_spread
```

Вектор: `[s₁, s₂, ..., s₁₁, Δs₁_30d, Δs₂_30d, ..., Δs₁₁_τd]`

*Чому 30d/14d/7d/60d — не magic numbers, а природні частоти метрик (NUPL змінюється місяцями, CipherB — тижнями).*

### 2.2 Centroids (з labeled даних)

```python
TOP_DATES = [
    '2021-04-14',   # Spring ATH
    '2021-11-10',   # Nov ATH
    '2024-03-14',   # 2024 ATH
    '2025-07-17',   # CB weekly peak
    '2025-09-29',   # Price ATH
]

BOTTOM_DATES = [
    '2018-12-15',   # 2018 cycle bottom
    '2022-06-18',   # Capitulation
    '2022-11-21',   # FTX bottom
]

top_centroid    = mean(wave_vector at each TOP_DATE)
bottom_centroid = mean(wave_vector at each BOTTOM_DATE)
```

Зберігаються у `data/adaptive_weights.json`. Оновлюються при підтвердженні нових екстремумів.

### 2.3 Scoring Formula (нуль параметрів)

```python
d_top    = euclidean_distance(current_wave, top_centroid)
d_bottom = euclidean_distance(current_wave, bottom_centroid)

score = d_bottom / (d_top + d_bottom) × 100
```

**Властивості:**
- Поточний стан ≈ top_centroid → d_top → 0 → score → 100
- Поточний стан ≈ bottom_centroid → d_bottom → 0 → score → 0
- Рівновіддаленість → score = 50
- Дивергенція (CB падає при ATH ціні): вектор відходить від top_centroid → score падає автоматично

**Чому евклідова відстань?** Найменш упереджена метрика без додаткових припущень.

---

## Layer 3: Orchestrator

TiZ (Time-in-Zone) — не ризик-сигнал, а **сигнал довіри**. Залишається в оркестраторі.

```
tiz_maturity = tiz_days / 200

0–25%  → EARLY_ZONE:    "Рання аномалія, ще не підтверджено"
25–50% → DEVELOPING:    "Тренд формується"
50–80% → CONFIRMED:     "Підтверджений сигнал"
80%+   → MATURE:        "Зрілий — максимальна довіра"
```

TiZ впливає на **flag та conviction**, а не на числовий score.

---

## Layer 2b: Phase Signals (TiZ v2)

> Не "наскільки ризиковано", а **"в якому часі циклу ми знаходимося"**.

### Концепція

Замість одного числа TiZ — два дзеркальних сигнали:

```
top_signal  = наскільки метрики схожі на підтверджений TOP  (0–100%)
bot_signal  = наскільки метрики схожі на підтвердженe BOTTOM (0–100%)
```

**Ключова властивість:** вони НЕ сумуються до 100%. Обидва можуть бути низькими (transition). Обидва не можуть бути високими одночасно (геометрія забороняє).

### Формула (нуль вільних параметрів)

```python
top_signal = max(0, min(100, (d_top_max - d_top) / (d_top_max - d_top_min) × 100))
bot_signal = max(0, min(100, (d_bot_max - d_bot) / (d_bot_max - d_bot_min) × 100))
```

Де `d_top_min/max` і `d_bot_min/max` — **calibration anchors**, обчислені під час тренування з labeled дат:
- `d_top_min` = відстань до top_centroid у Nov 2021 ATH (найчистіший топ) → top_signal = **100%**
- `d_bot_min` = відстань до bottom_centroid у Jun 2022 (найчистіше дно) → bot_signal = **100%**

Зберігаються у `data/adaptive_weights.json['calibration']`.

### Фаза ринку

```python
if   top_signal > 50 and top_signal > bot_signal: phase = 'TOP'
elif bot_signal > 50 and bot_signal > top_signal: phase = 'BOTTOM'
elif top_signal - bot_signal >  10:               phase = 'BULL'
elif top_signal - bot_signal < -10:               phase = 'BEAR'
else:                                             phase = 'NEUTRAL'
```

| phase | Значення |
|-------|---------|
| `TOP` | Метрики в топовій зоні — максимальна обережність |
| `BOTTOM` | Метрики в донній зоні — акумуляція підтверджується |
| `BULL` | Перехід: метрики рухаються від дна до топа |
| `BEAR` | Перехід: метрики рухаються від топа до дна |
| `NEUTRAL` | Обидва сигнали рівні — щирa невизначеність |

### Валідація на ключових датах

```
Date         top%   bot%  phase    BTC
─────────────────────────────────────────────────
Nov 2021 ATH  100%   12%  TOP      $69k
CB peak 2025   92%   16%  TOP     $119k
Price ATH 2025 72%   41%  TOP     $129k  ← divergence: bot росте при ATH
Post-ATH 2025  41%   65%  BOTTOM   $94k
Today (Jun'26) 32%   71%  BOTTOM   $64k
Capitulation   8%   100%  BOTTOM   $18k
```

**Divergence автоматично видно:** на Sep 2025 Price ATH (max ціна) bot_signal=41% — метрики вже від'їжджають від топа, хоч ціна ще на вершині.

### Три сигнали разом

```
v2_score    = позиція між екстремумами (0–100, де ми)
top_signal  = близькість до TOP territory (0–100%)
bot_signal  = близькість до BOTTOM territory (0–100%)
```

| Комбінація | Інтерпретація |
|-----------|--------------|
| v2=80, top=90%, bot=5% | Підтверджений топ — максимальний ризик |
| v2=15, bot=95%, top=8% | Підтверджене дно — можливість накопичення |
| v2=44, bot=59%, top=38% | Mid-bull correction: позиційно OK, технічно як дно |
| v2=50, top=25%, bot=30% | Перехід — невизначеність, чекаємо підтвердження |

---

## Що НЕ входить в цю версію

- State Identifier / per-state weight matrix → потребує sklearn + більше даних
- Mixture of Experts → v3+
- Neural networks → v3+

---

## Реалізація (порядок)

| # | Що | Файл | Статус |
|---|---|---|---|
| 1 | `cipherb_weekly` / `cipherb_daily` як окремі виміри | `scraper/scoring.py` | ✓ DONE |
| 2 | Precompute centroids + calibration anchors | `tools/train_adaptive_weights.py` | ✓ DONE |
| 3 | `score_processor_v2()` — distance scoring | `scraper/scoring_v2.py` | ✓ DONE |
| 4 | `phase_signals()` — top/bot dual signal | `scraper/scoring_v2.py` | ✓ DONE |
| 5 | Backtest validation (v1 vs v2 side-by-side) | `tools/backtest.py` | ✓ DONE |
| 6 | Live scraper: prev_scores з historical data | `scraper/scraper.py` | TODO |

---

## Ключові рішення та обґрунтування

| Рішення | Чому |
|---|---|
| Геометрія (відстань) замість ваг | Нуль вільних параметрів — питань "чому X?" не виникає |
| Trajectory в wave vector | Дивергенції видно без спеціального коду (CB↓ при ATH автоматично збільшує d_top) |
| TiZ в оркестраторі | TiZ = тривалість умови, не рівень ризику — різна семантика |
| 4-year adaptive calibration | Один цикл Bitcoin = природній контекстний вікно |
| Евклідова відстань | Мінімум припущень без numpy (pure Python) |
| Labeled centroids з 8 дат | Мало даних — зберігаємо простоту, уникаємо перенавчання |
