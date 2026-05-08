"""Fear & Greed index scraper.

Live source: BMP Fear & Greed chart — Playwright scrape, daily updated.
History source: data/history/fear_greed.json — for backtest use only.

Falls back to history file last entry if Playwright fails,
and to alternative.me API as last resort.
"""
import json
import os
import logging

logger = logging.getLogger(__name__)

_HISTORY_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'data', 'history', 'fear_greed.json')
)
_BMP_URL = 'https://www.bitcoinmagazinepro.com/charts/bitcoin-fear-and-greed-index/'


def get_fear_greed():
    """Return {'value': int, 'label': str} for current Fear & Greed index.

    Scrapes BMP chart via Playwright for today's fresh value.
    Falls back to history file, then alternative.me API.
    """
    # Primary: Playwright scrape of BMP (daily fresh)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            br = p.chromium.launch(headless=True)
            ctx = br.new_context(user_agent=(
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ))
            pg = ctx.new_page()
            pg.goto(_BMP_URL, wait_until='networkidle', timeout=60000)
            pg.wait_for_timeout(3000)
            result = pg.evaluate("""() => {
                const el = document.querySelector('.js-plotly-plot');
                if (!el || !el._fullData) return null;
                const fg = el._fullData.find(t => t.name && t.name.toLowerCase().includes('fear'));
                if (!fg || !fg.customdata || !fg.customdata.length) return null;
                // last entry = most recent; grab last 7 for average
                const last7 = fg.customdata.slice(-7);
                const scores = last7.map(r => r[2]).filter(s => s != null);
                const avg7d = scores.length ? Math.round(scores.reduce((a,b)=>a+b,0)/scores.length*10)/10 : null;
                const last = fg.customdata[fg.customdata.length - 1];
                return {date: last[0] ? last[0].slice(0,10) : null, score: last[2], label: last[3], avg_7d: avg7d};
            }""")
            br.close()
        if result and result.get('score') is not None:
            score = int(result['score'])
            avg_7d = round(float(result['avg_7d']), 1) if result.get('avg_7d') is not None else float(score)
            label = result.get('label') or _score_to_label(score)
            logger.info('get_fear_greed: %s score=%s avg_7d=%s label=%s (BMP live)', result.get('date'), score, avg_7d, label)
            return {'latest': score, 'avg_7d': avg_7d, 'label': label}
    except Exception as e:
        logger.warning('get_fear_greed: BMP Playwright failed: %s', e)

    # Fallback 1: history file last entry
    if os.path.exists(_HISTORY_FILE):
        try:
            with open(_HISTORY_FILE) as f:
                data = json.load(f)
            series = data.get('series', [])
            if series:
                last = series[-1]
                score = last.get('score')
                label = last.get('label') or _score_to_label(score)
                if score is not None:
                    last7 = [e['score'] for e in series[-7:] if e.get('score') is not None]
                    avg_7d = round(sum(last7) / len(last7), 1) if last7 else float(score)
                    logger.warning('get_fear_greed: using history file fallback (%s)', last.get('date'))
                    return {'latest': int(score), 'avg_7d': avg_7d, 'label': label}
        except Exception as e:
            logger.error('get_fear_greed: history file failed: %s', e)

    # Fallback 2: alternative.me API
    logger.warning('get_fear_greed: falling back to alternative.me API')
    try:
        import requests
        r = requests.get('https://api.alternative.me/fng/', params={'limit': 7}, timeout=15)
        r.raise_for_status()
        j = r.json()
        if j.get('data'):
            entries = j['data']
            values = [int(e['value']) for e in entries if e.get('value') is not None]
            if values:
                latest = values[0]
                avg_7d = round(sum(values) / len(values), 1)
                label = entries[0].get('value_classification')
                return {'latest': latest, 'avg_7d': avg_7d, 'label': label}
    except Exception as e:
        logger.error('get_fear_greed: alternative.me fallback failed: %s', e)

    return None


def _score_to_label(score):
    if score is None:
        return None
    if score <= 24:
        return 'Extreme Fear'
    if score <= 44:
        return 'Fear'
    if score <= 55:
        return 'Neutral'
    if score <= 75:
        return 'Greed'
    return 'Extreme Greed'

