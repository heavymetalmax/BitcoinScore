# BitcoinScore — Project Roadmap

**Мета проекту:** Щоденний індекс ризику покупки Bitcoin (0–100). 0 = генераційна можливість купити, 100 = час виходити. Орієнтований на трейдера який купує дно циклу і продає топ.

---

## Що маємо зараз (v4.3-stable, Jun 2026)

### Скоринг
| Компонент | Файл | Статус |
|-----------|------|--------|
| On-chain метрики (NUPL, MVRV, RHODL, CVDD, aSOPR) | `scraper/scoring.py` | ✅ Piecewise mapping (V4.3) |
| Tech/Macro метрики (CipherB, Mayer, ETF, FG, YC, M2) | `scraper/scoring.py` | ✅ |
| Adaptive calibration (rolling 4-year percentile) | `scraper/scoring.py` | ✅ |
| V3 dynamic z-weighted mixing | `scraper/scoring_v3.py` | ✅ |
| HMM Phase Classifier (BOTTOM/NEUTRAL/TOP) | `scraper/scoring_v3.py` | ✅ Exp 2 |
| Mahalanobis coherence dampening | `scraper/scoring.py` | ✅ Exp 3 |
| Phase-aware coherence targets (26/68) | `scraper/scoring_v3.py` | ✅ Grid search |
| Stage 2 conviction (LogReg) | `scraper/orchestrator.py` | ✅ V4.2 |
| Dynamic TiZ pull | `scraper/orchestrator.py` | ✅ V4.2 |
| Dynamic divergence detection (volatility-adjusted) | `scraper/orchestrator.py` | ✅ V4.2 |

### Дані
| Джерело | Метрика | Статус |
|---------|---------|--------|
| BMP (Playwright) | NUPL, MVRV, RHODL, CVDD, aSOPR, Puell | ✅ |
| Kraken OHLCV | CipherB, Mayer Multiple, SMC, Funding Rate | ✅ |
| Farside | ETF Flows | ⚠️ Застрягає (остання дата Jun 05) |
| CMC | Fear & Greed | ✅ |
| FRED | Yield Curve | ✅ |
| MacroMicro (Zyte) | M2 YoY | ⚠️ Рідкі оновлення |

### Фронтенд
| Елемент | Файл | Статус |
|---------|------|--------|
| Головний дашборд | `web/classic2.html` | ✅ |
| Sparklines (метрики) | `web/sparklines.json` | ✅ по Jun 14 |
| Score history chart | `data.score_history` | ✅ 3087 точок |
| Health badge | `classic2.html` JS | ✅ |

### Бекенд / CI
| Компонент | Статус |
|-----------|--------|
| GitHub Actions щоденний скрейп (08:00 UTC) | ✅ |
| GitHub Pages деплой | ✅ |
| PKL модель (Stage 1 + Stage 2) | ✅ `data/v3_phase_model.pkl` |

---

## Що плануємо додати

### P0 — Критично для довіри до системи

#### 1. Signal Ledger (журнал сигналів) ✅
**Що:** При кожному перетині порогу (score ≤ 25 = "buy zone", ≥ 75 = "sell zone") автоматично записувати в `data/history/signal_ledger.json`:
```json
{ "date": "2026-06-15", "score": 23, "btc_price": 65706, "signal": "BUY_ZONE", "phase": "BOTTOM", "conviction": 0.938, "flag": "PROBABLE_BOTTOM" }
```
**Реалізовано:** `tools/signal_ledger.py` + крок в CI після retry. De-dup по даті. Перший запис: 2026-06-15.

#### 2. Drift Monitor (supervisor)
**Що:** Щоденна перевірка:
- Score змінився без зміни метрик (model drift)
- HMM застряг в одній фазі > 90 днів
- Метрики виходять за межі тренувального розподілу (OOD alert)
**Де:** `tools/drift_monitor.py`, запускається в CI після скрейпу

---

### P1 — Покращення якості

#### 3. Ensemble uncertainty
**Що:** Показувати діапазон score від різних моделей (V1, V3, Fisher, Orchestrator). Якщо розбіжність > 8 пунктів — окремий індикатор "low confidence".
**Навіщо:** Одна цифра без контексту вводить в оману.

#### 4. Regime fingerprint
**Що:** Cosine similarity поточного 22-dim wave vector з "відбитками" відомих режимів (2018 bottom, FTX bottom, 2021 top). Виводити в UI: "Найближчий режим: FTX Bottom (схожість 87%)".
**Навіщо:** Пояснення чому score такий — більше довіри з боку трейдера.

---

### P2 — Якість даних

#### 5. ETF flows — fallback scraper
**Що:** Якщо Farside не оновлювався > 5 днів — скрейпити альтернативне джерело або позначати метрику як stale у health check.

#### 6. M2 YoY — автоматичне оновлення
**Що:** Зараз оновлюється вручну. Потрібен автоматичний парсер.

---

## Порядок роботи (наступна сесія)

1. Обговорити і погодити план
2. Реалізувати Signal Ledger (P0)
3. Реалізувати Drift Monitor (P0)
4. Перевірити що CI підхоплює обидва

---

## Ключові інваріанти (не регресувати)

| Дата | Score | Зона |
|------|-------|------|
| 2018-12-15 | ≤ 27 | BOTTOM |
| 2022-11-21 (FTX) | ≤ 25 | BOTTOM |
| 2021-11-10 (ATH) | ≥ 83 | TOP |
| 2024-03-14 (ATH) | ≥ 85 | TOP |
| 2026-06 (сьогодні) | 20–28 | BOTTOM |
