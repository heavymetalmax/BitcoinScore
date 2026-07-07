#!/usr/bin/env python3
"""
Generate a structured Bitcoin market report for manual LLM analysis.

Usage:
  python3 tools/generate_report.py [--out reports/]

Output: reports/btc_report_YYYY-MM-DD.md
"""
import json
import os
import shutil
import sys
import datetime

DATA_PATH    = 'data/data.json'
HISTORY_PATH = 'data/history/scores.json'
OUT_DIR      = 'reports'
STORAGE_PATH = '/mnt/storage/btc_report.md'

MILESTONES = [
    ('2018-12-15', 'Дно 2018',        3_212),
    ('2020-03-13', 'COVID краш',       5_579),
    ('2022-06-18', 'Capitulation',    18_971),
    ('2022-11-21', 'FTX дно',         15_781),
    ('2021-04-14', 'Пік квітень 2021', 62_960),
    ('2021-11-10', 'Пік листопад 2021',64_882),
    ('2024-03-14', 'ATH березень 2024',71_389),
    ('2025-07-17', 'CB пік',          119_178),
    ('2025-09-29', 'Intraday ATH',    129_000),
]


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def mv(obj, *keys):
    """Safe nested get from dict or wrapped {value:...} objects."""
    for k in keys:
        if isinstance(obj, dict):
            obj = obj.get(k)
        else:
            return None
    if isinstance(obj, dict) and 'value' in obj:
        return obj['value']
    return obj


def pct_change(new, old):
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old * 100


def score_zone(s):
    if s is None: return '?'
    if s <= 20:  return 'СИЛЬНА ЗОНА КУПІВЛІ'
    if s <= 35:  return 'Зона накопичення'
    if s <= 55:  return 'Нейтральна зона'
    if s <= 75:  return 'Зона обережності'
    return 'ЕКСТРЕМАЛЬНИЙ РИЗИК'


def build_report(d, history):
    today  = datetime.date.today().isoformat()
    lines  = []
    a      = lines.append

    # ── Header ────────────────────────────────────────────────────────────────
    a(f'# Bitcoin Risk Report — {today}')
    a('')
    a('> Звіт для аналізу ситуації. Завантаж в чат і обговори що зараз відбувається.')
    a('')

    # ── Головний сигнал ───────────────────────────────────────────────────────
    score   = d.get('final_score')
    v3score = d.get('v3_score')
    oc      = d.get('v3_onchain_score') or d.get('onchain_score')
    tech    = d.get('v3_tech_score')    or d.get('tech_score')
    price   = d.get('btc_price')
    phase   = d.get('v3_phase', d.get('scoring_regime', '?'))
    sig     = d.get('v3_signal') or d.get('signal') or {}
    flag    = sig.get('flag', '?')
    conv    = sig.get('conviction')

    a('## Головний сигнал')
    a('')
    a(f'| Параметр | Значення |')
    a(f'|----------|----------|')
    a(f'| **Фінальний score** | **{score}/100** — {score_zone(score)} |')
    a(f'| On-chain score | {oc}/100 |')
    a(f'| Tech/macro score | {tech}/100 |')
    a(f'| Фаза ринку | {phase} |')
    a(f'| Сигнал | {flag} |')
    a(f'| Conviction | {round(conv*100) if conv else "?"}% |')
    a(f'| BTC ціна | ${price:,.0f} |' if price else '| BTC ціна | ? |')
    a('')

    # ── TiZ та Wave Resonance ──────────────────────────────────────────────────
    tiz_days = d.get('v3_tiz_days') or d.get('tiz_days')
    tiz_mat  = d.get('v3_tiz_maturity')
    tiz_cal  = d.get('v3_tiz_calibration')
    wr       = d.get('wave_resonance') or {}
    wr_score = wr.get('score')
    wr_coh   = wr.get('coherence')
    oc_coh   = d.get('v3_oc_coherence') or d.get('oc_coherence')
    w_bot    = d.get('v3_w_bot')
    w_top    = d.get('v3_w_top')
    w_neu    = d.get('v3_w_neutral')

    a('## Стан моделі')
    a('')
    a(f'| | |')
    a(f'|--|--|')
    if tiz_days is not None:
        mat_pct = round(tiz_mat * 100) if tiz_mat else '?'
        a(f'| Time-in-Zone (TiZ) | {tiz_days} днів у зоні ({mat_pct}% зрілості, калібрування {tiz_cal} днів) |')
    if wr_score is not None:
        a(f'| Wave Resonance | score={wr_score}/100  coherence={wr_coh} ({"висока" if wr_coh and wr_coh > 0.7 else "низька"} синхронізація) |')
    else:
        a(f'| Wave Resonance | не розраховано (дані застарілі) |')
    if oc_coh is not None:
        a(f'| On-chain coherence | {round(oc_coh, 3)} |')
    if w_bot is not None:
        a(f'| Ваги фаз | BOTTOM={round(w_bot,2)}  NEUTRAL={round(w_neu or 0,2)}  TOP={round(w_top or 0,2)} |')
    a('')

    # ── Нормалізовані метрики ─────────────────────────────────────────────────
    ns = d.get('v3_normalized_scores') or {}
    ut = d.get('v3_utilities') or {}

    METRIC_LABELS = {
        'nupl':             ('NUPL',             'On-chain'),
        'mvrv_z_score':     ('MVRV Z-Score',     'On-chain'),
        'rhodl_ratio':      ('RHODL Ratio',       'On-chain'),
        'cvdd_ratio':       ('CVDD Ratio',        'On-chain'),
        'puell':            ('Puell Multiple',    'On-chain'),
        'lth_supply':       ('LTH Supply %',      'On-chain'),
        'asopr':            ('aSOPR',             'On-chain'),
        'cipherb':          ('CipherB',           'Tech'),
        'mayer_multiple':   ('Mayer Multiple',    'Tech'),
        'fear_greed':       ('Fear & Greed',      'Tech'),
        'pi_gap':           ('Pi Cycle Gap',      'Tech'),
        'etf_flows':        ('ETF Flows',         'Tech'),
        'funding_rate':     ('Funding Rate',      'Tech'),
        'yield_curve_spread':('Yield Curve',      'Macro'),
        'm2_yoy':           ('M2 YoY',            'Macro'),
        'dxy':              ('DXY',               'Macro'),
    }

    a('## Метрики (normalized 0-100, низький = дешево/дно)')
    a('')
    a('| Метрика | Група | Score | Utility | Сигнал |')
    a('|---------|-------|-------|---------|--------|')
    for k, (label, group) in METRIC_LABELS.items():
        s = ns.get(k)
        u = ut.get(k)
        if s is None:
            a(f'| {label} | {group} | — | — | немає даних |')
            continue
        signal = ('🟢 дно' if s <= 25 else '🟡 нейтр' if s <= 60 else '🔴 топ')
        u_str  = f'{round(u,2)}' if u is not None else '—'
        a(f'| {label} | {group} | {s} | {u_str} | {signal} |')
    a('')

    # ── Сирі значення ──────────────────────────────────────────────────────────
    m = d.get('metrics') or {}
    a('## Сирі значення метрик')
    a('')
    # All metric values in data.json follow: {value: INNER, source: ..., updated: ...}
    # mv() already unwraps the outer wrapper — so these helpers get INNER as their input.

    def lth_pct():
        v = mv(m, 'lth_supply_pct')
        if v is None: return None
        if isinstance(v, (int, float)) and v > 100:
            return round(v / 21_000_000 * 100, 2)
        return v

    def fg_val():
        # fear_greed has no outer wrapper: {latest: 29.0, avg_7d: 23.3, ...}
        fg = m.get('fear_greed')
        if isinstance(fg, dict):
            return fg.get('latest') or fg.get('avg_7d')
        return fg

    def fr_val():
        # mv() returns {latest: -0.028, avg_7d: -0.028, score: 15, ...}
        inner = mv(m, 'funding_rate')
        if isinstance(inner, dict):
            return inner.get('avg_7d') or inner.get('latest')
        return inner

    def pi_val():
        # mv() returns {ma111: ..., gap_pct: 60.16, score: 40, ...}
        inner = mv(m, 'pi_cycle')
        if isinstance(inner, dict):
            return inner.get('gap_pct')
        return inner

    def dxy_val():
        # mv() unwraps outer {value: X, ...} → X directly
        return mv(m, 'dxy')

    def mm_val():
        # mv() returns {value: 0.858, dma_200: ..., score: 22} — one more level needed
        inner = mv(m, 'mayer_multiple')
        if isinstance(inner, dict):
            return inner.get('value')
        return inner

    def etf_val():
        # mv() returns {value: -29.4, daily_flow: ..., total_cumulative: ...} — one more level
        inner = mv(m, 'etf_flows')
        if isinstance(inner, dict):
            return inner.get('value')
        return inner

    raw_items = [
        ('NUPL',                  mv(m, 'nupl')),
        ('MVRV Z-Score',          mv(m, 'mvrv_z_score') or mv(m, 'mvrv')),
        ('RHODL Ratio',           mv(m, 'rhodl_ratio')),
        ('CVDD Ratio',            mv(m, 'cvdd_ratio')),
        ('Puell Multiple',        mv(m, 'puell_multiple')),
        ('LTH Supply % (від 21M)', lth_pct()),
        ('aSOPR',                 mv(m, 'asopr')),
        ('Fear & Greed (1-100)',   fg_val()),
        ('Mayer Multiple',        mm_val()),
        ('ETF Flows 14d (M$)',    etf_val()),
        ('Funding Rate avg7d %',  fr_val()),
        ('DXY',                   dxy_val()),
        ('Yield Curve %',         mv(m, 'yield_curve')),
        ('M2 YoY %',              mv(m, 'm2_mom')),
        ('Pi Cycle Gap %',        pi_val()),
    ]
    a('| Метрика | Значення |')
    a('|---------|----------|')
    for label, val in raw_items:
        if val is None:
            a(f'| {label} | — |')
        elif isinstance(val, float):
            a(f'| {label} | {val:.4f} |')
        else:
            a(f'| {label} | {val} |')
    a('')

    # ── Динаміка ───────────────────────────────────────────────────────────────
    prev_d = d.get('prev_day') or {}
    prev_w = d.get('prev_week') or {}
    sh     = [x for x in (d.get('score_history') or []) if x.get('score') is not None]

    a('## Динаміка score')
    a('')
    rows = []
    if prev_d.get('final_score') is not None:
        rows.append(('Вчора',    prev_d['final_score'], prev_d.get('btc_price')))
    if prev_w.get('final_score') is not None:
        rows.append(('7 днів тому', prev_w['final_score'], prev_w.get('btc_price')))
    for days in [30, 60, 90, 180]:
        if len(sh) >= days:
            e = sh[-days]
            rows.append((f'{days} днів тому', e['score'], e.get('price')))
    rows.append(('Сьогодні', score, price))

    a('| Період | Score | BTC ціна | Δ score | Δ ціна |')
    a('|--------|-------|----------|---------|--------|')
    prev_s, prev_p = None, None
    for label, s, p in rows:
        ds = f'{s - prev_s:+d}' if prev_s is not None else '—'
        dp = f'{pct_change(p, prev_p):+.1f}%' if (prev_p and p) else '—'
        p_str = f'${p:,.0f}' if p else '—'
        a(f'| {label} | {s} | {p_str} | {ds} | {dp} |')
        prev_s, prev_p = s, p
    a('')

    # ── Порівняння з мілстоунами ──────────────────────────────────────────────
    sh_dict = {e['date']: e for e in sh}
    a('## Порівняння з ключовими ринковими подіями')
    a('')
    a('| Подія | Дата | Score | BTC ціна |')
    a('|-------|------|-------|----------|')
    for date_str, label, ref_price in MILESTONES:
        hist = sh_dict.get(date_str)
        s    = hist['score'] if hist else '?'
        p    = hist.get('price', ref_price) if hist else ref_price
        a(f'| {label} | {date_str} | {s} | ${p:,.0f} |')
    a(f'| **Зараз** | **{today}** | **{score}** | **${price:,.0f}** |' if price else
      f'| **Зараз** | **{today}** | **{score}** | — |')
    a('')

    # ── Зона прогнозу ─────────────────────────────────────────────────────────
    zf = d.get('zone_forecast') or {}
    if zf:
        a('## Zone Forecast')
        a('')
        buy  = zf.get('buy',  {})
        sell = zf.get('sell', {})
        real = zf.get('realized_price')
        a(f'| | Ціна |')
        a(f'|--|------|')
        if real:        a(f'| Realized Price | ${real:,.0f} |')
        if buy.get('price'):  a(f'| Buy zone | ${buy["price"]:,.0f} |')
        if sell.get('price'): a(f'| Sell zone | ${sell["price"]:,.0f} |')
        a('')

    # ── Data quality ──────────────────────────────────────────────────────────
    dq = d.get('data_quality') or {}
    ts = d.get('timestamp', '')
    a('## Якість даних')
    a('')
    a(f'- Дата даних: {ts[:10] if ts else "?"}'  )
    if dq:
        a(f'- Метрики: {dq.get("active_metrics")}/{dq.get("total_metrics")} активних ({dq.get("quality_pct")}%)')
        if dq.get('missing_metrics'):
            a(f'- Відсутні: {", ".join(dq["missing_metrics"])}')
    a('')

    # ── Контекст для аналізу ──────────────────────────────────────────────────
    a('## Контекст для обговорення')
    a('')
    a('**Питання до аналізу:**')
    a('1. Де ми зараз знаходимось в циклі і що це означає для наступних 3-6 місяців?')
    a('2. Які метрики найбільш важливі зараз і чому?')
    a('3. Що може змінити поточний сигнал — які каталізатори?')
    a('4. Порівняй поточну ситуацію з найближчим аналогом з минулого.')
    a('5. Які ризики ринок зараз недооцінює?')
    a('')

    return '\n'.join(lines)


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else OUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    d       = load(DATA_PATH)
    history = load(HISTORY_PATH) if os.path.exists(HISTORY_PATH) else []

    report  = build_report(d, history)

    out_path = os.path.join(out_dir, 'btc_report.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f'Report saved: {out_path}')
    print(f'Size: {len(report)} chars, {report.count(chr(10))} lines')

    try:
        shutil.copy2(out_path, STORAGE_PATH)
        print(f'Copied to storage: {STORAGE_PATH}')
    except Exception as e:
        print(f'Storage copy skipped: {e}')


if __name__ == '__main__':
    main()
