# BTC Signal Score — Технічне завдання для автоматизації

## Огляд проекту

Створити систему автоматичного збору on-chain метрик Bitcoin та генерації торгового сигналу (0-100 балів) на основі зважених індикаторів. Система працює на GitHub Actions з щоденним оновленням даних через Playwright скрейпінг + публічні API, та відображає результат на статичній веб-сторінці через GitHub Pages.

---

## Архітектура

```
GitHub Actions (cron: щодня 8:00 UTC)
    ↓
scraper.py (Python + Playwright)
    ↓
Збирає дані з:
  - MacroMicro (Playwright скрейп)
  - Alternative.me API (Fear & Greed)
  - CoinGecko API (BTC ціна)
    ↓
Зберігає data.json в репо
    ↓
GitHub Pages хостить index.html
    ↓
index.html читає data.json → показує дашборд
```

---

## Компоненти системи

### 1. scraper.py — Збір даних

**Мова:** Python 3.9+

**Залежності:**
```
playwright==1.42.0
requests==2.31.0
```

**Що збирає:**

| Метрика | Джерело | Метод |
|---------|---------|-------|
| NUPL (Net Unrealized Profit/Loss) | https://en.macromicro.me/series/45910/bitcoin-nupl | Playwright скрейп |
| MVRV Z-Score | https://en.macromicro.me/series/8365/bitcoin-mvrv-zscore | Playwright скрейп |
| Profit Taking Ratio | https://en.macromicro.me/charts/143605/bitcoin-ratio-of-loss-and-profit-addresses | Playwright скрейп |
| Fear & Greed Index | https://api.alternative.me/fng/?limit=1 | REST API |
| BTC Price | https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd | REST API |

**Вихідний формат (data.json):**
```json
{
  "timestamp": "2026-04-21T08:00:00Z",
  "btc_price": 72139.50,
  "fear_greed": 29,
  "fear_greed_label": "Fear",
  "nupl": 26.4,
  "mvrv_z_score": 0.41,
  "profit_taking_ratio": 2.95,
  "metrics": {
    "nupl": {
      "value": 26.4,
      "source": "MacroMicro",
      "updated": "2026-04-21T08:00:00Z"
    },
    "mvrv": {
      "value": 0.41,
      "source": "MacroMicro",
      "updated": "2026-04-21T08:00:00Z"
    },
    "profit_taking": {
      "value": 2.95,
      "source": "MacroMicro",
      "updated": "2026-04-21T08:00:00Z"
    },
    "fear_greed": {
      "value": 29,
      "label": "Fear",
      "source": "Alternative.me",
      "updated": "2026-04-21T08:00:00Z"
    }
  }
}
```

**Логіка скрейпера:**

1. **Ініціалізація Playwright:**
   - Запуск headless Chromium
   - User-agent: реальний браузер (не детектується як бот)
   - Viewport: 1920x1080

2. **Скрейпінг MacroMicro:**
   - Відкрити сторінку
   - Чекати завантаження графіка (wait for selector)
   - Знайти останнє значення метрики в DOM
   - Якщо значення в canvas графіку (недоступне в тексті) → зробити скріншот → використати Claude Vision API для розпізнавання цифр
   - Fallback: якщо не вдалось — повернути `null` + логувати помилку

3. **API виклики:**
   - Fear & Greed: GET запит, парсинг JSON
   - CoinGecko: GET запит, парсинг JSON
   - Retry логіка: 3 спроби з 5 сек затримкою між ними

4. **Збереження data.json:**
   - Перевірка на валідність даних (всі поля присутні)
   - Форматування JSON з indent=2
   - Запис в файл `data.json` в корені репо

5. **Error handling:**
   - Якщо будь-яка метрика не вдалась → залишити попереднє значення
   - Логувати всі помилки в stderr
   - Exit code 0 навіть при частковій невдачі (щоб не ламати GitHub Actions)

---

### 2. GitHub Actions Workflow

**Файл:** `.github/workflows/update-data.yml`

**Тригери:**
- Cron: `0 8 * * *` (щодня о 8:00 UTC)
- Manual: `workflow_dispatch` (кнопка "Run workflow")

**Кроки:**

1. Checkout репо
2. Setup Python 3.9
3. Встановити залежності: `pip install playwright requests`
4. Встановити браузер: `playwright install chromium`
5. Запустити скрипт: `python scraper.py`
6. Commit + push `data.json` якщо змінився
7. GitHub Pages автоматично підхопить зміни

**Секрети (якщо потрібні):**
- `ANTHROPIC_API_KEY` — для Claude Vision API якщо використовується для розпізнавання графіків

---

### 3. index.html — Дашборд

**Технології:**
- Vanilla JavaScript (без фреймворків)
- CSS змінні для тематизації
- Fetch API для завантаження data.json

**Функціонал:**

#### 3.1. Автоматичне оновлення даних

При завантаженні сторінки:
```javascript
fetch('data.json')
  .then(r => r.json())
  .then(data => {
    updateAutoMetrics(data); // Fear & Greed, BTC ціна
    updateSliders(data);     // NUPL, MVRV, Profit Taking
    calculateScore();        // Обрахувати загальний score
  });
```

#### 3.2. Розрахунок Signal Score

**Формула:**
```
Score = Σ (slider_value_i × weight_i)

де slider_value_i ∈ [0, 100]
   weight_i — вага індикатора
```

**Ваги індикаторів:**

| Категорія | Індикатор | Вага |
|-----------|-----------|------|
| **On-Chain (38%)** | NUPL | 11% |
| | MVRV Z-Score | 11% |
| | Profit Taking Ratio | 16% |
| **Sentiment (22%)** | Whale Flow | 11% |
| | Fear & Greed | 11% |
| **Macro (25%)** | Global M2 | 8% |
| | DXY | 6% |
| | Нафта/Геополітика | 6% |
| | ФРС/Ставки | 5% |
| **ТА (15%)** | Cipher B (тижневий) | 8% |
| | Ціна vs Підтримка (SMC) | 7% |

**Інтерпретація Score:**

| Score | Зона | Сигнал | Колір |
|-------|------|--------|-------|
| 0-30 | Buy Strong | 🟢 КУПУЙ АГРЕСИВНО | #00ff87 |
| 30-45 | Buy | 🟡 НАКОПИЧУЙ / DCA | #7fff5c |
| 45-55 | Neutral | ⚪ ЧЕКАЙ | #ffd166 |
| 55-70 | Caution | 🟠 ОБЕРЕЖНО | #ff9a3c |
| 70-100 | Sell | 🔴 ФІКСУЙ ПРИБУТОК | #ff3d5a |

#### 3.3. Маппінг даних на слайдери

**NUPL (%):**
- Вхід: `-50` до `100` (реальне значення)
- Вихід: `0` до `100` (slider позиція)
- Логіка:
  - `< 0` (капітуляція) → slider = 0-20 (🟢 зона купівлі)
  - `0-35` (страх/надія) → slider = 20-40
  - `35-60` (оптимізм) → slider = 40-60
  - `60-75` (віра) → slider = 60-80
  - `> 75` (ейфорія) → slider = 80-100 (🔴 зона продажу)

**MVRV Z-Score:**
- Вхід: `-2` до `10` (реальне значення)
- Вихід: `0` до `100` (slider)
- Логіка:
  - `< 0` → slider = 0-15 (🟢)
  - `0-1` → slider = 15-30
  - `1-2.5` (справедлива ціна) → slider = 30-50
  - `2.5-5` → slider = 50-75
  - `> 5` (перегрів) → slider = 75-100 (🔴)

**Profit Taking Ratio:**
- Вхід: `0.1` до `10` (співвідношення)
- Вихід: `0` до `100`
- Логіка:
  - `< 0.5` (збитки > прибутки) → slider = 0-20 (🟢)
  - `0.5-1` (баланс) → slider = 20-50
  - `1-2.5` → slider = 50-70
  - `> 2.5` (прибутки >> збитки) → slider = 70-100 (🔴)

**Fear & Greed:**
- Вхід: `0-100` (індекс)
- Вихід: `0-100` (slider)
- Пряме маппування — індекс вже в правильному діапазоні

#### 3.4. Ручні слайдери (оновлюються користувачем)

Метрики без автоматичних даних:
- Whale Flow
- DXY
- Нафта/Геополітика
- ФРС/Ставки
- Global M2
- Cipher B
- Ціна vs Підтримка

**Функціонал:**
- Зберігати значення в `localStorage`
- Показувати дату останнього оновлення
- Кнопка "скинути" повертає до дефолтних значень
- Посилання на джерело даних для кожної метрики

---

## Структура репозиторію

```
btc-signal-scraper/
├── .github/
│   └── workflows/
│       └── update-data.yml      # GitHub Actions workflow
├── scraper.py                    # Python скрейпер
├── requirements.txt              # Python залежності
├── data.json                     # Автооновлюваний файл з даними
├── index.html                    # Головна сторінка дашборду
├── README.md                     # Документація
└── .gitignore                    # Python cache, node_modules, etc.
```

---

## Інструкції для розгортання

### Крок 1: Локальна розробка (macOS)

```bash
# Створити репо
mkdir btc-signal-scraper
cd btc-signal-scraper
git init

# Встановити залежності
pip3 install playwright requests
playwright install chromium

# Запустити скрейпер локально
python3 scraper.py

# Перевірити що data.json створився
cat data.json

# Відкрити index.html в браузері
open index.html
```

### Крок 2: Налаштування GitHub

1. Створити репо на GitHub
2. Push коду:
   ```bash
   git remote add origin https://github.com/username/btc-signal-scraper.git
   git add .
   git commit -m "Initial commit"
   git push -u origin main
   ```

3. Увімкнути GitHub Pages:
   - Settings → Pages
   - Source: Deploy from a branch
   - Branch: `main` / root
   - Save

4. Перевірити що сайт доступний:
   - `https://username.github.io/btc-signal-scraper/`

### Крок 3: Тестування автоматизації

1. Запустити workflow вручну:
   - Actions → "Update BTC Data" → Run workflow

2. Перевірити що `data.json` оновився

3. Перевірити що сайт показує нові дані

---

## Додаткові фічі (опціонально)

### Фіча 1: Історичні дані

Зберігати `data.json` як `data-YYYY-MM-DD.json` щодня → будувати графік історії score

### Фіча 2: Telegram/Discord бот

При зміні зони сигналу (купуй → чекай) — відправляти нотифікацію

### Фіча 3: Бектестинг

Завантажити історичні дані → порахувати скільки разів індикатор давав правильний сигнал

### Фіча 4: API endpoint

Зробити `https://username.github.io/btc-signal-scraper/api/score` що повертає лише число

---

## Troubleshooting

### Проблема: MacroMicro блокує Playwright

**Рішення:**
- Додати затримки між запитами (time.sleep)
- Використати residential proxies
- Змінити User-Agent на реальний браузер
- Fallback: скріншот + Claude Vision API

### Проблема: GitHub Actions падає на Playwright

**Рішення:**
```yaml
- name: Install Playwright browsers
  run: |
    playwright install --with-deps chromium
```

### Проблема: data.json не оновлюється

**Перевірити:**
- Чи є права на commit/push у GitHub Actions?
- Додати в workflow:
  ```yaml
  - name: Commit and push
    run: |
      git config user.name "github-actions[bot]"
      git config user.email "github-actions[bot]@users.noreply.github.com"
      git add data.json
      git commit -m "Update data.json" || echo "No changes"
      git push
  ```

---

## Контрольний чеклист

- [ ] Python скрейпер працює локально
- [ ] GitHub Actions успішно запускається
- [ ] data.json оновлюється щодня
- [ ] GitHub Pages відображає дашборд
- [ ] Автооновлення Fear & Greed працює
- [ ] Автооновлення BTC ціни працює
- [ ] Автооновлення NUPL, MVRV, Profit Taking працює
- [ ] Score обраховується правильно
- [ ] Кольори зон відповідають score
- [ ] Ручні слайдери зберігаються в localStorage
- [ ] Дати останнього оновлення відображаються
- [ ] Посилання на джерела працюють
- [ ] Мобільна версія виглядає нормально

---

## Очікуваний результат

Після виконання всіх кроків ти матимеш:

1. **Повністю автоматизований дашборд** що оновлюється щодня без твоєї участі
2. **Актуальний BTC Signal Score** на основі реальних on-chain метрик
3. **Історію змін** через git commits
4. **Публічний URL** яким можна ділитися

**Приклад використання:**

```
Ранок → відкрив https://username.github.io/btc-signal-scraper/
       → бачу score 55 (⚪ ЧЕКАЙ)
       → оновив вручну DXY, Cipher B, Whale Flow
       → score змінився на 48 (🟡 НАКОПИЧУЙ)
       → виставив ліміт ордер на $66K
```

---

## Контакти / Підтримка

Якщо щось не працює — перевір логи GitHub Actions та консоль браузера для помилок JavaScript.