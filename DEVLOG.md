# BitcoinScore — Dev Log

Хронологія сесій, що і чому змінювалось. Мета: не повторювати одні й ті самі недопрацювання.

---

## 2026-07-09

**Методологічний аудит (agent) + 3 критичні баги**

Запустили окремого агента для повного аудиту пайплайну — він знайшов всі проблеми одразу, на відміну від поступового пошуку.

### Баги виправлено (commit `99ff5ac`)

**1. NUPL units mismatch** (`scraper/scoring.py`)
- `unified_history.json` зберігає NUPL як fraction (0–1)
- `daily_vector.json` зберігає NUPL як % (0–100)
- `_PCTILE_DIVISOR['nupl'] = 100` ділило live value правильно, але не ділило значення з daily_vector при завантаженні
- Всі 38 свіжих записів виглядали як "надзвичайно високі" (13–17 замість 0.13–0.17)
- При наступному bull top це занижувало б percentile ризику на ~3–5 пп
- **Fix**: `float(v) / dv_divisor` при читанні daily_vector tail

**2. CipherB divergence flags на неправильному рівні** (`scraper/scoring_v3.py`)
- `raw_metrics['cipherb']` — outer dict: `{value: {weekly_score, fast_bullish_div,...}, source, updated}`
- Код перевіряв `cb.get('fast_bearish_div')` — outer dict, де таких ключів немає → penalty ніколи не застосовувалась
- Live impact: `fast_bullish_div=True` → weekly_score мав би стати 0, але рахувався як 9.87 → score 19 замість 11
- **Fix**: `val_dict.get('fast_bearish_div')` (inner dict)

**3. Mayer Multiple не отримував adaptive blending** (`scraper/normalizer.py`)
- `ADAPTIVE_METRICS = {'nupl', 'mvrv', 'mayer', 'cvdd_ratio', 'puell', 'etf_flows'}`
- `normalize_metric` отримує metric=`'mayer_multiple'`, але перевіряє `hist_key in ADAPTIVE_METRICS`
- `'mayer_multiple' not in ADAPTIVE_METRICS` → завжди повертав тільки fixed map score
- Аналогічний ремаппінг вже був для `mvrv_z_score → 'mvrv'`
- **Fix**: додано `elif metric == 'mayer_multiple': hist_key = 'mayer'`

### HMM перетренований після фіксів

### Знайдено але не виправлено (не критично зараз)
- `asopr` в unified_history має неправильні значення (0–0.18 замість ~1.0). Dormant — asopr не в ADAPTIVE_METRICS
- `ADAPTIVE_WIN_YEARS=4` виключає Nov 2021 top з поточного вікна (вікно: 2022–2026). При наступному циклі якщо пік перевищить 2021 — percentile буде занижений. Розглянути 5–6 років
- ETF flows floor at −1000 тоді як history тепер сягає −3137; без градації між "трохи негативний" і "екстремальний відтік"
- classic.html не показує 4 метрики: puell, lth_supply, pi_gap, funding_rate (вони є в v3_score але невидимі в UI)

---

## 2026-07-08

**Calibration sweep + ETF migration + UI cleanup**

### ADAPTIVE_BLEND 0.5 → 0.7 (commit `e099961`)
- Sweep по 10 циклічних подіях (5 tops, 5 bottoms), 4 метрики (nupl, mvrv, cvdd, mayer)
- blend=0.7 дав separation +1.6 пп (top avg 68.2 vs bottom avg 6.0)
- **Застереження**: sweep не перевіряв повний пайплайн (без HMM фази, utility weights, coherence)
- HMM перетренований після зміни

### ETF flows: 14d → 7d migration (commit `5e64c81`)
- Виявлено: history зберігала 14d суми, scraper почав писати 7d з commit `f84a65f` (2026-07-05T12:25Z) під старим ім'ям
- Міграція: pre-switch → etf_flow_7d = etf_flow_14d / 2; post-switch → etf_flow_7d = old value (вже 7d)
- Прибрано `_PCTILE_DIVISOR['etf_flows']` (більше не потрібен)
- `history_writer.py` тепер пише `etf_flow_7d`

### DXY метрика
- FRED DTWEXBGS (broad trade-weighted), map: ≤108→0, 116→50, ≥128→100
- Додано в V3 pipeline і відображено в classic.html

### JS mapScore видалено з classic.html (commit `b1ae3af`)
- Legacy функція ~100 рядків, рахувала absolute scores — ніде не використовувалась
- Всі сигнали і спарклайни тепер беруть V3 normalized scores з `v3_normalized_scores`

---

## 2026-07-05

**V3 pipeline debug + data quality**

- `data_quality` field в data.json (commit `85940be`)
- etf_flows → ADAPTIVE_METRICS (commit `f5dc641`)
- Utility weights calibration → v3_relevance_weights.json
- HMM retrain (sklearn 1.9.0)
- scoring_v3 key aliasing fix — 14/14 метрик активні (commit `5fcfb98`)

---

## Паттерни проблем що повторюються

1. **Dict nesting** — raw_metrics часто є `{value: {...}, source, updated}`. Завжди перевіряти на якому рівні лежать потрібні поля перед тим як писати `.get()`
2. **Units mismatch** — history і live value можуть бути в різних одиницях. Завжди документувати що і в яких units зберігається. NUPL: history=fraction, live=%, ÷100 при порівнянні
3. **String key vs ADAPTIVE_METRICS set** — якщо metric name в коді відрізняється від ключа в `ADAPTIVE_METRICS`, blending мовчки не застосовується. Перевіряти всі `hist_key` маппінги
4. **Sweep ≠ full pipeline** — локальна оптимізація одного параметру без E2E валідації може дати оманливий результат. Завжди запускати повний backtest
5. **Whitepaper може відставати від коду** — especially classifier (docs: LogReg, code: HMM) і blend ratio (docs: 50/50, code: 70/30)
