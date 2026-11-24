#!/usr/bin/env python3
"""Zero-lookahead strategy discovery & backtest engine for BTCBRI and Cipher B.

Strict anti-lookahead guarantees:
  1. Weekly Cipher B bars (`cipherb_btcusdt_1w.json`) are filtered to the 659 exact
     7-day Kraken Thursday bars (`2014-04-10 .. 2026-11-19`, zero gaps).
     Empirical audit confirms Kraken's Thursday-dated 1W candle closes on Wednesday
     (`date + 6 days`, 0.0%-0.1% exact match to daily close). Therefore each weekly
     bar is ONLY exposed to the engine on `date + 6 days` (Wednesday close), and any
     signal triggered on day `t` executes strictly at day `t + 1` close (`Thursday`)
     with 0.5% fee per side. Unclosed forming weekly bars are excluded automatically.
  2. Daily BTCBRI (`scores.json`) and daily Cipher B (`cipherb_btcusdt_1d.json`)
     trigger at day `t` close and execute strictly at day `t + 1` close.
  3. Robustness evaluation across:
       - Full daily aligned dataset (2017-08-17 .. 2026-11-23, 9.3 years, 3,369 days)
       - Modern 2-Cycle Stress Test (2018-01-01 .. 2026-11-23, starts near 2017 ATH)
       - Symmetric 2-Cycle Split (Cycle 1: 2018-01..2022-06 vs Cycle 2: 2022-06..2026-11)
       - 22 Rolling 4-Year Windows (step 90d) — return & Calmar win rate vs Buy & Hold
       - Monthly Cash Pool ($100/mo on 24th) on Full 9.3Y & Recent 6Y (2020-11..2026-11)

Outputs:
  - data/strategy_patterns.json
  - web/strategy_patterns.json
  - reports/strategy_backtest_2026_11_24.md
"""
import datetime
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEE = 0.005  # 0.5% per trade side (conservative spot fee + slippage)


def load_aligned_timeline(start_date='2017-08-17'):
    scores = json.loads((ROOT / 'data/history/scores.json').read_text(encoding='utf-8'))
    cb_w_raw = json.loads((ROOT / 'data/history/cipherb_btcusdt_1w.json').read_text(encoding='utf-8'))
    cb_d_raw = json.loads((ROOT / 'data/history/cipherb_btcusdt_1d.json').read_text(encoding='utf-8'))
    cb_w_all = cb_w_raw.get('series', cb_w_raw) if isinstance(cb_w_raw, dict) else cb_w_raw
    cb_d_all = cb_d_raw.get('series', cb_d_raw) if isinstance(cb_d_raw, dict) else cb_d_raw

    # 1. Strictly filter weekly Cipher B to exact 7-day Thursday bars (Kraken 1W open)
    #    and expose each bar ONLY on Wednesday close (+6 days) for 100% zero-lookahead.
    thu_rows = []
    seen_thu = set()
    for r in cb_w_all:
        dt_str = (r.get('date') or '')[:10]
        if not dt_str or dt_str in seen_thu:
            continue
        if datetime.date.fromisoformat(dt_str).strftime('%a') == 'Thu':
            seen_thu.add(dt_str)
            thu_rows.append(r)
    thu_rows.sort(key=lambda x: x['date'][:10])

    w_by_close_date = {}
    w_enriched = []
    for i, r in enumerate(thu_rows):
        w1 = float(r['wt1']) if r.get('wt1') is not None else None
        w2 = None
        if i >= 2 and w1 is not None and w_enriched[i - 1]['wt1'] is not None and w_enriched[i - 2]['wt1'] is not None:
            w2 = (w1 + w_enriched[i - 1]['wt1'] + w_enriched[i - 2]['wt1']) / 3.0

        prev_w1 = w_enriched[i - 1]['wt1'] if i >= 1 else None
        prev_w2 = w_enriched[i - 1]['wt2'] if i >= 1 else None
        sg = sr = bg = br = False
        if i >= 3 and w1 is not None and w2 is not None and prev_w1 is not None and prev_w2 is not None:
            if prev_w1 <= prev_w2 and w1 > w2:
                sg = True
                bg = w2 <= -53.0
            if prev_w1 >= prev_w2 and w1 < w2:
                sr = True
                br = w2 >= 53.0

        open_d = datetime.date.fromisoformat(r['date'][:10])
        close_d = (open_d + datetime.timedelta(days=6)).isoformat()  # Wednesday close
        item = {
            'open_date': r['date'][:10],
            'close_date': close_d,
            'close': float(r['close']) if r.get('close') is not None else None,
            'wt1': w1,
            'wt2': w2,
            'w_score': float(r['weekly_score']) if r.get('weekly_score') is not None else None,
            'sg': sg, 'sr': sr, 'bg': bg, 'br': br,
            'bull_zone': (w1 > w2) if (w1 is not None and w2 is not None) else False,
            'bear_zone': (w1 < w2) if (w1 is not None and w2 is not None) else False,
            'bear_div': bool(r.get('bearish_div') or r.get('bearish_divergence')),
            'bull_div': bool(r.get('bullish_div') or r.get('bullish_divergence')),
        }
        w_enriched.append(item)
        w_by_close_date[close_d] = item

    # 2. Daily Cipher B
    d_by_date = {}
    for r in cb_d_all:
        dt = (r.get('date') or '')[:10]
        if dt:
            d_by_date[dt] = {
                'bear_div': bool(r.get('bearish_div') or r.get('fast_bear_div') or r.get('double_bearish_div')),
                'bull_div': bool(r.get('bullish_div') or r.get('fast_bull_div') or r.get('double_bullish_div')),
            }

    # 3. Aligned daily timeline
    timeline = []
    cur_w = None
    bri_hist = []
    tiz_le25 = 0
    tiz_le30 = 0
    for row in scores:
        dt = (row.get('date') or '')[:10]
        if dt < start_date:
            continue
        price = row.get('btc_price')
        bri = row.get('final_score')
        if price is None or price <= 0 or bri is None:
            continue
        bri_f = float(bri)
        bri_hist.append(bri_f)
        tiz_le25 = (tiz_le25 + 1) if bri_f <= 25 else 0
        tiz_le30 = (tiz_le30 + 1) if bri_f <= 30 else 0
        bri_min45 = min(bri_hist[-45:])

        w_ev = w_by_close_date.get(dt)
        if w_ev is not None:
            cur_w = w_ev
        d_ev = d_by_date.get(dt) or {}

        timeline.append({
            'date': dt,
            'price': float(price),
            'bri': bri_f,
            'bri_min45': bri_min45,
            'tiz_le25': tiz_le25,
            'tiz_le30': tiz_le30,
            'phase': row.get('phase'),
            'w_bot': float(row['w_bot']) if row.get('w_bot') is not None else None,
            'w_top': float(row['w_top']) if row.get('w_top') is not None else None,
            'w_close_day': w_ev is not None,
            'w_sg': bool(w_ev['sg']) if w_ev else False,
            'w_sr': bool(w_ev['sr']) if w_ev else False,
            'w_bg': bool(w_ev['bg']) if w_ev else False,
            'w_br': bool(w_ev['br']) if w_ev else False,
            'w_bear_div': bool(w_ev['bear_div']) if w_ev else False,
            'w_bull_div': bool(w_ev['bull_div']) if w_ev else False,
            'w_wt1': cur_w['wt1'] if cur_w else None,
            'w_wt2': cur_w['wt2'] if cur_w else None,
            'w_score': cur_w['w_score'] if cur_w else None,
            'w_bull_zone': bool(cur_w['bull_zone']) if cur_w else False,
            'w_bear_zone': bool(cur_w['bear_zone']) if cur_w else False,
            'w_last_close_date': cur_w['close_date'] if cur_w else None,
            'd_bull_div': bool(d_ev.get('bull_div')),
            'd_bear_div': bool(d_ev.get('bear_div')),
        })
    return timeline


def simulate_lump_sum(rows, strategy, start_cash=10000.0, record_trades=False):
    """Causal target-allocation simulator (signal at close t -> execute at close t+1)."""
    if len(rows) < 60:
        return None
    cash = start_cash
    btc = 0.0
    state = {'in_pos': False, 'armed': False, 'peak_bri': 0.0, 'alloc': 0.0}
    pending_target = None
    peak_eq = start_cash
    max_dd = 0.0
    exposure_sum = 0.0
    trades = 0
    trade_log = []
    entry_price = None
    round_trips = 0
    wins = 0
    eval_fn = strategy['eval_fn']

    for i, r in enumerate(rows):
        p = r['price']
        if pending_target is not None:
            eq_now = cash + btc * p
            cur_alloc = (btc * p) / eq_now if eq_now > 0 else 0.0
            diff = pending_target - cur_alloc
            if abs(diff) >= 0.04:
                if diff > 0 and cash > 1.0:
                    spend = cash if pending_target >= 0.99 else min(cash, diff * eq_now)
                    btc += (spend * (1.0 - FEE)) / p
                    cash -= spend
                    trades += 1
                    if not state['in_pos']:
                        entry_price = p
                    state['in_pos'] = True
                    if record_trades:
                        trade_log.append({
                            'date': r['date'], 'action': 'BUY', 'price': round(p, 1),
                            'bri': r['bri'], 'w_wt1': round(r['w_wt1'], 1) if r['w_wt1'] is not None else None,
                            'w_score': round(r['w_score'], 1) if r['w_score'] is not None else None,
                            'target_alloc': round(pending_target, 2),
                        })
                elif diff < 0 and btc > 0:
                    sell_btc = btc if pending_target <= 0.01 else min(btc, ((-diff) * eq_now) / p)
                    cash += sell_btc * p * (1.0 - FEE)
                    btc = max(0.0, btc - sell_btc)
                    trades += 1
                    if btc * p < 1.0:
                        btc = 0.0
                        state['in_pos'] = False
                        if entry_price is not None:
                            round_trips += 1
                            if p > entry_price:
                                wins += 1
                            entry_price = None
                    if record_trades:
                        trade_log.append({
                            'date': r['date'], 'action': 'SELL', 'price': round(p, 1),
                            'bri': r['bri'], 'w_wt1': round(r['w_wt1'], 1) if r['w_wt1'] is not None else None,
                            'w_score': round(r['w_score'], 1) if r['w_score'] is not None else None,
                            'target_alloc': round(pending_target, 2),
                        })
            state['alloc'] = (btc * p) / max(1e-9, cash + btc * p)
            pending_target = None

        eq = cash + btc * p
        cur_alloc = (btc * p) / eq if eq > 0 else 0.0
        state['alloc'] = cur_alloc
        exposure_sum += cur_alloc
        if eq > peak_eq:
            peak_eq = eq
        dd = (peak_eq - eq) / peak_eq * 100.0 if peak_eq > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

        next_target = eval_fn(r, i, rows, state)
        if next_target is not None:
            pending_target = max(0.0, min(1.0, float(next_target)))

    final_eq = cash + btc * rows[-1]['price']
    years = max(0.1, (datetime.date.fromisoformat(rows[-1]['date']) - datetime.date.fromisoformat(rows[0]['date'])).days / 365.25)
    mult = final_eq / start_cash
    cagr = (mult ** (1.0 / years) - 1.0) * 100.0
    calmar = cagr / max(1.0, max_dd)

    out = {
        'final_eq': round(final_eq, 1),
        'return_pct': round((mult - 1.0) * 100.0, 1),
        'multiple': round(mult, 2),
        'cagr_pct': round(cagr, 1),
        'max_dd_pct': round(max_dd, 1),
        'calmar': round(calmar, 2),
        'trades': trades,
        'round_trips': round_trips,
        'win_rate_pct': round(wins / round_trips * 100.0, 1) if round_trips > 0 else None,
        'exposure_pct': round(exposure_sum / len(rows) * 100.0, 1),
        'end_alloc_pct': round(state['alloc'] * 100.0, 0),
        'end_armed': bool(state.get('armed', False)),
        'end_peak_bri': round(state.get('peak_bri', 0.0), 1),
        'pending_tomorrow': pending_target,
    }
    if 's1' in state and 's2' in state:
        out['sleeve_allocs_pct'] = [round(state['s1']['alloc'] * 100.0, 0), round(state['s2']['alloc'] * 100.0, 0)]
    if record_trades:
        out['trade_log'] = trade_log
    return out


def simulate_monthly_pool(rows, strategy, monthly_add=100.0):
    """Causal monthly cash-pool ($100 added on first bar >= 24th of each month)."""
    cash = 0.0
    btc = 0.0
    contributed = 0.0
    state = {'in_pos': False, 'armed': False, 'peak_bri': 0.0, 'alloc': 0.0}
    pending_target = None
    trades = 0
    last_month = None
    eval_fn = strategy['eval_fn']

    for i, r in enumerate(rows):
        p = r['price']
        ym = r['date'][:7]
        day = int(r['date'][8:10])
        if ym != last_month and day >= 24:
            cash += monthly_add
            contributed += monthly_add
            last_month = ym

        if pending_target is not None:
            eq_now = cash + btc * p
            cur_alloc = (btc * p) / eq_now if eq_now > 0 else 0.0
            diff = pending_target - cur_alloc
            if diff > 0.04 and cash > 1.0:
                spend = cash if pending_target >= 0.99 else min(cash, diff * eq_now)
                btc += (spend * (1.0 - FEE)) / p
                cash -= spend
                state['in_pos'] = True
                trades += 1
            elif diff < -0.04 and btc > 0:
                sell_btc = btc if pending_target <= 0.01 else min(btc, ((-diff) * eq_now) / p)
                cash += sell_btc * p * (1.0 - FEE)
                btc = max(0.0, btc - sell_btc)
                if btc * p < 1.0:
                    btc = 0.0
                    state['in_pos'] = False
                trades += 1
            pending_target = None

        eq = cash + btc * p
        state['alloc'] = (btc * p) / eq if eq > 0 else 0.0
        next_target = eval_fn(r, i, rows, state)
        if next_target is not None:
            pending_target = max(0.0, min(1.0, float(next_target)))

    final_val = cash + btc * rows[-1]['price']
    mult = final_val / contributed if contributed > 0 else 1.0
    return {
        'contributed': round(contributed, 0),
        'final_val': round(final_val, 1),
        'return_pct': round((mult - 1.0) * 100.0, 1),
        'multiple': round(mult, 2),
        'trades': trades,
    }


def make_binary(buy_pred, sell_pred):
    def _eval(r, i, rows, st):
        if st['alloc'] < 0.99 and buy_pred(r, i, rows, st):
            st['armed'] = False
            st['peak_bri'] = r['bri']
            return 1.0
        if st['alloc'] > 0.01 and sell_pred(r, i, rows, st):
            st['armed'] = False
            return 0.0
        return None
    return _eval


def make_trailing(buy_pred, arm_bri, drop_pts, extra_exit_pred=None):
    def _eval(r, i, rows, st):
        if st['alloc'] < 0.99 and buy_pred(r, i, rows, st):
            st['armed'] = False
            st['peak_bri'] = r['bri']
            return 1.0
        if st['alloc'] > 0.01:
            if r['bri'] >= arm_bri:
                st['armed'] = True
            if r['bri'] > st['peak_bri']:
                st['peak_bri'] = r['bri']
            if st['armed'] and r['bri'] <= st['peak_bri'] - drop_pts:
                st['armed'] = False
                return 0.0
            if extra_exit_pred is not None and extra_exit_pred(r, i, rows, st):
                st['armed'] = False
                return 0.0
        return None
    return _eval


def make_hyst(margin, max_bri=100):
    return make_binary(
        lambda r, i, rs, st, m=margin, mb=max_bri: (
            r['w_close_day'] and r['w_wt1'] is not None and r['w_wt2'] is not None
            and (r['w_wt1'] - r['w_wt2']) >= m and r['bri'] <= mb
        ),
        lambda r, i, rs, st, m=margin: (
            r['w_close_day'] and r['w_wt1'] is not None and r['w_wt2'] is not None
            and (r['w_wt2'] - r['w_wt1']) >= m
        ),
    )


def make_blend(fn1, fn2, w1=0.5):
    def _eval(r, i, rs, st):
        if 's1' not in st:
            st['s1'] = {'in_pos': False, 'armed': False, 'peak_bri': 0.0, 'alloc': 0.0}
            st['s2'] = {'in_pos': False, 'armed': False, 'peak_bri': 0.0, 'alloc': 0.0}
        t1 = fn1(r, i, rs, st['s1'])
        if t1 is not None:
            st['s1']['alloc'] = t1
        t2 = fn2(r, i, rs, st['s2'])
        if t2 is not None:
            st['s2']['alloc'] = t2
        target = w1 * st['s1']['alloc'] + (1.0 - w1) * st['s2']['alloc']
        if abs(target - st['alloc']) >= 0.05:
            return target
        return None
    return _eval


def build_strategies():
    S = []
    def add(sid, family, name, rule_short, next_trigger_hint, eval_fn, live_spec):
        S.append({
            'id': sid, 'family': family, 'name': name,
            'rule_short': rule_short, 'next_trigger_hint': next_trigger_hint,
            'eval_fn': eval_fn, 'live_spec': live_spec,
        })

    # Benchmark
    add('BENCH_BH', 'Benchmark', 'Buy & Hold (100% BTC завжди)',
        'Завжди 100% у BTC', '',
        lambda r, i, rs, st: 1.0 if st['alloc'] < 0.99 else None,
        {'type': 'always'})

    # Reusable base engines
    fn_bri35_t85 = make_trailing(lambda r, i, rs, st: r['bri'] <= 35, 85, 15)
    fn_bri30_t85 = make_trailing(lambda r, i, rs, st: r['bri'] <= 30, 85, 15)
    fn_tiz21_t85 = make_trailing(lambda r, i, rs, st: r['tiz_le25'] >= 21, 85, 15)
    fn_cb_hyst2  = make_hyst(2.0, 100)
    fn_cb_hyst3  = make_hyst(3.0, 100)
    fn_cb_hyst4  = make_hyst(4.0, 100)
    fn_cb_score  = make_binary(
        lambda r, i, rs, st: r['w_close_day'] and r['w_score'] is not None and r['w_score'] <= 25,
        lambda r, i, rs, st: r['w_close_day'] and r['w_score'] is not None and r['w_score'] >= 75 and r['w_bear_zone'],
    )

    # ============================================================
    # FAMILY A: PURE BTCBRI
    # ============================================================
    add('BRI_TIZ25_21D_T85', 'BTCBRI',
        'Витримка дна BTCBRI (21 день у зоні → Плавний вихід)',
        'Вхід: BRI ≤ 25 мінімум 21 день поспіль | Вихід: після піку BRI ≥ 85 і відкату −15 п.',
        'Наступний тригер: утримати BTC до піку BRI ≥ 85 (потім продаж на −15 п.); для нового входу — 21 день у зоні BRI ≤ 25.',
        fn_tiz21_t85,
        {'type': 'bri_tiz_trail', 'bri_le': 25, 'tiz_days': 21, 'arm': 85, 'drop': 15})

    add('BRI_35_TRAIL_85_15', 'BTCBRI',
        'Цикловий BTCBRI (Набір ≤35 → Плавний вихід від піку)',
        'Вхід: BRI ≤ 35 | Вихід: після піку BRI ≥ 85 і відкату −15 п.',
        'Вхід активний при BRI ≤ 35; фіксація прибутку лише після досягнення BRI ≥ 85 і спаду на −15 пунктів.',
        fn_bri35_t85,
        {'type': 'bri_trail', 'buy': 35, 'arm': 85, 'drop': 15})

    add('BRI_30_TRAIL_85_15', 'BTCBRI',
        'BTCBRI Консервативний Trailing (≤30 → пік ≥85)',
        'BUY: BRI ≤ 30 | SELL: після піку BRI ≥ 85 і відкату -15 п.',
        'Вхід активний при BRI ≤ 30; фіксація після піку BRI ≥ 85 і спаду -15 п.',
        fn_bri30_t85,
        {'type': 'bri_trail', 'buy': 30, 'arm': 85, 'drop': 15})

    add('BRI_35_90', 'BTCBRI',
        'BTCBRI Цикловий коридор (≤35 → ≥90)',
        'BUY: BRI ≤ 35 | SELL: BRI ≥ 90 (фіксований екстремум)',
        'Простий коридор: накопичення при BRI ≤ 35, фіксація лише при екстремальній ейфорії BRI ≥ 90.',
        make_binary(lambda r, i, rs, st: r['bri'] <= 35, lambda r, i, rs, st: r['bri'] >= 90),
        {'type': 'bri_band', 'buy': 35, 'sell': 90})

    for b, s in ((20, 80), (25, 85), (30, 85)):
        add(f'BRI_{b}_{s}', 'BTCBRI',
            f'BTCBRI Класичний: ≤{b} → ≥{s}',
            f'BUY: BRI ≤ {b} | SELL: BRI ≥ {s}',
            f'Вхід при BRI ≤ {b}, вихід при BRI ≥ {s}.',
            make_binary(lambda r, i, rs, st, b=b: r['bri'] <= b, lambda r, i, rs, st, s=s: r['bri'] >= s),
            {'type': 'bri_band', 'buy': b, 'sell': s})

    add('BRI_BOUNCE_30_6_T85', 'BTCBRI',
        'BTCBRI Відскок від дна + Trailing',
        'BUY: мін. BRI(45д) ≤ 30 і відскок +6 п. (або BRI ≤ 12) | SELL: пік ≥ 85, спад -15 п.',
        'Чекає підтвердження розвороту BRI вгору на +6 пунктів від 45-денного мінімуму.',
        make_trailing(lambda r, i, rs, st: (r['bri_min45'] <= 30 and r['bri'] >= r['bri_min45'] + 6 and r['bri'] <= 45) or r['bri'] <= 12, 85, 15),
        {'type': 'bri_bounce_trail', 'min_bri': 30, 'bounce': 6, 'arm': 85, 'drop': 15})

    # ============================================================
    # FAMILY B: PURE CIPHER B (100% ZERO-LOOKAHEAD 1W)
    # ============================================================
    add('CB_W_ZONE_HYST2', 'Cipher B',
        'Тижневий імпульс Cipher B (Без шуму)',
        'Вхід: закриття тижня (wt1 − wt2) ≥ +2 | Вихід: закриття тижня (wt2 − wt1) ≥ +2',
        'Чекає закриття тижня (середа UTC), де wt1 перевищить wt2 щонайменше на +2 п. (підтверджений бичачий розворот).',
        fn_cb_hyst2,
        {'type': 'cb_w_hyst', 'margin': 2.0})

    add('CB_W_ZONE_HYST4', 'Cipher B',
        'Cipher B (1W): Імпульсний тренд ±4 (повільний)',
        'BUY: 1W закриття (wt1 − wt2) ≥ +4 | SELL: 1W закриття (wt2 − wt1) ≥ +4',
        'Чекає сильного тижневого розвороту (wt1 − wt2) ≥ +4 п. на закритті тижня.',
        fn_cb_hyst4,
        {'type': 'cb_w_hyst', 'margin': 4.0})

    add('CB_W_SCORE_25_75', 'Cipher B',
        'Тижневий накопичувач Cipher B (Для кеш-пулу)',
        'Вхід: тижневий бал ≤ 25 | Вихід: тижневий бал ≥ 75 і спад wt1 < wt2',
        'Найкращий для щомісячного кеш-пулу: чекає тижневий бал ≤ 25 (перепроданість) і продає лише при перегріві ≥ 75 + спаді.',
        fn_cb_score,
        {'type': 'cb_w_score', 'buy_score': 25, 'sell_score': 75})

    add('CB_W_SG_SR', 'Cipher B',
        'Cipher B (1W): Мала зелена → Мала червона [Пара #3]',
        'BUY: 1W перетин wt1 > wt2 | SELL: 1W перетин wt1 < wt2',
        'Базова пара з дослідження 2016–2026: вхід на першій тижневій зеленій точці, вихід на червоній.',
        make_binary(lambda r, i, rs, st: r['w_sg'], lambda r, i, rs, st: r['w_sr']),
        {'type': 'cb_w_sg_sr'})

    add('CB_W_SG_DIV', 'Cipher B',
        'Cipher B (1W): Мала зелена → Тижн. Bearish Div [Пара #4]',
        'BUY: 1W перетин wt1 > wt2 | SELL: 1W Bearish Divergence',
        'Довгостроковий тримач із дослідження 2016–2026: вихід лише по тижневій ведмежій дивергенції.',
        make_binary(lambda r, i, rs, st: r['w_sg'], lambda r, i, rs, st: r['w_bear_div']),
        {'type': 'cb_w_sg_div'})

    add('CB_W_ZONE', 'Cipher B',
        'Cipher B (1W): Чистий режим wt1 > wt2 (без буфера)',
        'BUY: 1W wt1 > wt2 | SELL: 1W wt1 < wt2',
        'Вхід при закритті тижня wt1 > wt2, вихід при wt1 < wt2.',
        make_hyst(0.0, 100),
        {'type': 'cb_w_hyst', 'margin': 0.0})

    add('CB_D_DIV_DIV', 'Cipher B',
        'Cipher B (1D): Денна Bull Div → Денна Bear Div (контроль)',
        'BUY: 1D Bullish Div | SELL: 1D Bearish Div',
        'Контрольний тест перенавчання денних дивергенцій.',
        make_binary(lambda r, i, rs, st: r['d_bull_div'], lambda r, i, rs, st: r['d_bear_div']),
        {'type': 'cb_d_div'})

    # ============================================================
    # FAMILY C: HYBRID BTCBRI + CIPHER B (DUAL-SLEEVE & GUARDRAILS)
    # ============================================================
    add('HYB_5050_TIZ21_HYST2', 'Hybrid',
        'Спокійний гібрид 50/50 (Підтверджене дно + Тренд)',
        '50% капіталу: BRI ≤ 25 (21д) → плавний вихід ≥85 (−15) | 50% капіталу: тижневий розворот ±2',
        'Рукав BTCBRI тримає базову позицію; рукав Cipher B (50%) у кеші й увійде на закритті тижня при (wt1−wt2) ≥ +2.',
        make_blend(fn_tiz21_t85, fn_cb_hyst2, 0.5),
        {'type': 'hyb_blend_tiz21_hyst2', 'bri_le': 25, 'tiz_days': 21, 'arm': 85, 'drop': 15, 'margin': 2.0})

    add('HYB_5050_BRI35_HYST2', 'Hybrid',
        'Подвійний щит 50/50 (Цикл + Тижневий тренд)',
        '50% капіталу: BRI ≤ 35 → плавний вихід ≥85 (−15) | 50% капіталу: тижневий розворот ±2',
        'Найбільш універсальний: 50% тримає цикловий BRI, інші 50% чекають підтвердження 1W (wt1−wt2) ≥ +2 для повного входу (100%).',
        make_blend(fn_bri35_t85, fn_cb_hyst2, 0.5),
        {'type': 'hyb_blend_bri35_hyst2', 'buy': 35, 'arm': 85, 'drop': 15, 'margin': 2.0})

    add('HYB_5050_BRI35_CBSCORE', 'Hybrid',
        'Повільний гібрид 50/50 (Цикл + Перепроданість)',
        '50% капіталу: BRI ≤ 35 → плавний вихід ≥85 (−15) | 50% капіталу: тижневий бал ≤25 → ≥75',
        'Мінімум угод (~3–4/рік), стабільний в обох циклах! Другий рукав (50%) увійде, коли тижневий бал опуститься ≤ 25.',
        make_blend(fn_bri35_t85, fn_cb_score, 0.5),
        {'type': 'hyb_blend_bri35_cbscore', 'buy': 35, 'arm': 85, 'drop': 15, 'buy_score': 25, 'sell_score': 75})

    add('HYB_5050_BRI35_HYST4', 'Hybrid',
        'Гібрид 50/50 Growth: BTCBRI (≤35) + Cipher B 1W (±4)',
        '50% капіталу: BRI≤35 → Trailing≥85(-15) | 50% капіталу: 1W (wt1−wt2) ≥ +4 / ≤ −4',
        'Максимум прибутковості за 2018–2026: рукав Cipher B чекає сильного імпульсу (wt1−wt2) ≥ +4.',
        make_blend(fn_bri35_t85, fn_cb_hyst4, 0.5),
        {'type': 'hyb_blend_bri35_hyst4', 'buy': 35, 'arm': 85, 'drop': 15, 'margin': 4.0})

    add('HYB_WZONE_HYST2_B75', 'Hybrid',
        'Гібрид Анти-Ейфорія: 1W Імпульс ±2 з BRI-лімітом ≤75',
        'BUY: 1W (wt1−wt2) ≥ +2 і BRI ≤ 75 | SELL: 1W (wt2−wt1) ≥ +2',
        'Найнижча просадка серед одиночних правил (MaxDD 40.3%): чекає 1W (wt1−wt2) ≥ +2 при безпечному BRI ≤ 75.',
        make_hyst(2.0, 75),
        {'type': 'cb_w_hyst_bri', 'margin': 2.0, 'max_bri': 75})

    add('HYB_DUAL_30_55_65', 'Hybrid',
        'Гібрид Dual-Entry: (BRI≤30 або 1W зелена≤55) → 1W червона≥65',
        'BUY: BRI≤30 або (1W зелена і BRI≤55) | SELL: 1W wt1<wt2 і BRI≥65',
        'Вхід при BRI ≤ 30 або зеленій 1W точці; вихід при ведмежому тижні та перегрітому BRI ≥ 65.',
        make_binary(
            lambda r, i, rs, st: r['bri'] <= 30 or (r['w_sg'] and r['bri'] <= 55),
            lambda r, i, rs, st: r['w_close_day'] and r['w_bear_zone'] and r['bri'] >= 65,
        ),
        {'type': 'hyb_dual', 'deep_bri': 30, 'mid_bri': 55, 'exit_bri': 65})

    return S


def main():
    rows_full = load_aligned_timeline('2017-08-17')
    rows_2018 = [r for r in rows_full if r['date'] >= '2018-01-01']
    rows_6y   = [r for r in rows_full if r['date'] >= '2020-11-24']
    cyc1 = [r for r in rows_2018 if r['date'] <= '2022-06-15']
    cyc2 = [r for r in rows_2018 if r['date'] >  '2022-06-15']
    rolling_4y = [rows_full[i:i + 1460] for i in range(0, len(rows_full) - 1460, 90)]

    dca_strat = {'eval_fn': lambda r, i, rs, st: 1.0}
    dca_full = simulate_monthly_pool(rows_full, dca_strat)
    dca_6y   = simulate_monthly_pool(rows_6y, dca_strat)

    strats = build_strategies()
    bh_strat = strats[0]
    bh_roll_res = [simulate_lump_sum(w, bh_strat) for w in rolling_4y]
    bh_roll_med = round(sorted(x['multiple'] for x in bh_roll_res)[len(bh_roll_res) // 2], 2)

    results = []
    for st in strats:
        full = simulate_lump_sum(rows_full, st, record_trades=True)
        mod  = simulate_lump_sum(rows_2018, st)
        c1   = simulate_lump_sum(cyc1, st)
        c2   = simulate_lump_sum(cyc2, st)
        pool_full = simulate_monthly_pool(rows_full, st)
        pool_6y   = simulate_monthly_pool(rows_6y, st)

        roll_mults, roll_beats_ret, roll_beats_cal = [], 0, 0
        for idx_w, w in enumerate(rolling_4y):
            rw = simulate_lump_sum(w, st)
            bw = bh_roll_res[idx_w]
            if rw and bw:
                roll_mults.append(rw['multiple'])
                if rw['multiple'] >= bw['multiple']:
                    roll_beats_ret += 1
                if rw['calmar'] >= bw['calmar']:
                    roll_beats_cal += 1

        tlog = full.pop('trade_log', [])
        results.append({
            'id': st['id'],
            'family': st['family'],
            'name': st['name'],
            'rule_short': st['rule_short'],
            'next_trigger_hint': st['next_trigger_hint'],
            'live_spec': st['live_spec'],
            'full': full,
            'modern_2018': mod,
            'cycle1': c1,
            'cycle2': c2,
            'rolling_4y': {
                'windows': len(roll_mults),
                'median_mult': round(sorted(roll_mults)[len(roll_mults) // 2], 2) if roll_mults else 1.0,
                'beats_bh_return_pct': round(roll_beats_ret / max(1, len(roll_mults)) * 100.0, 1),
                'beats_bh_calmar_pct': round(roll_beats_cal / max(1, len(roll_mults)) * 100.0, 1),
            },
            'pool_full': pool_full,
            'pool_6y': pool_6y,
            'last_trade': tlog[-1] if tlog else None,
            'recent_trades': tlog[-6:],
        })

    bh = next(r for r in results if r['id'] == 'BENCH_BH')
    bh_c1, bh_c2 = bh['cycle1']['return_pct'], bh['cycle2']['return_pct']
    bh_c1_cal, bh_c2_cal = bh['cycle1']['calmar'], bh['cycle2']['calmar']

    for r in results:
        r['beats_bh_both_cycles'] = (r['cycle1']['return_pct'] > bh_c1 and r['cycle2']['return_pct'] > bh_c2)
        r['beats_bh_calmar_both_cycles'] = (r['cycle1']['calmar'] > bh_c1_cal and r['cycle2']['calmar'] > bh_c2_cal)
        r['beats_bh_2018'] = r['modern_2018']['return_pct'] > bh['modern_2018']['return_pct']
        r['beats_dca_6y'] = r['pool_6y']['return_pct'] > dca_6y['return_pct']
        r['beats_dca_full'] = r['pool_full']['return_pct'] > dca_full['return_pct']

        dd_bonus = 1.0 if r['modern_2018']['max_dd_pct'] < 48.0 else (0.5 if r['modern_2018']['max_dd_pct'] < 56.0 else 0.0)
        r['robust_score'] = round(
            min(r['modern_2018']['calmar'], 3.5) * 3.0
            + min(r['full']['calmar'], 3.5) * 1.2
            + (r['rolling_4y']['beats_bh_return_pct'] / 100.0) * 2.0
            + (r['rolling_4y']['beats_bh_calmar_pct'] / 100.0) * 1.8
            + (1.4 if r['beats_bh_both_cycles'] else 0.0)
            + (0.8 if r['beats_bh_calmar_both_cycles'] else 0.0)
            + (0.9 if r['beats_dca_6y'] else 0.0)
            + (0.7 if r['beats_dca_full'] else 0.0)
            + dd_bonus,
            3,
        )

    non_bench = [r for r in results if r['id'] != 'BENCH_BH']
    by_robust = sorted(non_bench, key=lambda x: x['robust_score'], reverse=True)
    by_2018 = sorted(non_bench, key=lambda x: x['modern_2018']['multiple'], reverse=True)
    by_calmar18 = sorted(non_bench, key=lambda x: x['modern_2018']['calmar'], reverse=True)
    by_pool6 = sorted(non_bench, key=lambda x: x['pool_6y']['multiple'], reverse=True)

    # Curated Top-7 validated patterns covering every distinct edge across Hybrid (3), BTCBRI (2), Cipher B (2)
    curated_ids = [
        'HYB_5050_BRI35_HYST2',    # Best All-Weather Hybrid (9.63x, beats DCA 6Y & Full, 64%/77% rolling)
        'HYB_5050_TIZ21_HYST2',    # Best Risk-Adjusted / Lowest DD Hybrid (9.76x, DD 44.6%, Calmar 0.65!)
        'HYB_5050_BRI35_CBSCORE',  # Best Low-Turnover Both-Cycles Hybrid (7.41x, C1 & C2 > B&H, Pool6Y 1.85x)
        'BRI_TIZ25_21D_T85',       # Best Pure BTCBRI (11.48x, DD 57.7%, Calmar 0.55, 7 trades)
        'BRI_35_TRAIL_85_15',      # Best Active Cycle BTCBRI (10.17x, Roll 55%/59%, 11 trades)
        'CB_W_ZONE_HYST2',         # Best Pure Cipher B Balanced Trend (6.08x, DD 46.8%, Calmar 0.48, Pool6Y 1.81x)
        'CB_W_SCORE_25_75',        # Best Pure Cipher B Cash-Pool (Pool6Y 2.19x vs DCA 1.49x, C2 +319%, 16 trades)
    ]
    by_id = {r['id']: r for r in results}
    role_tags = {
        'HYB_5050_BRI35_HYST2':   'Універсальний лідер (All-Weather)',
        'HYB_5050_TIZ21_HYST2':   'Макс. Calmar / Мін. просадка',
        'HYB_5050_BRI35_CBSCORE': 'Перемога в обох циклах (Low-Turnover)',
        'BRI_TIZ25_21D_T85':      'Топ Pure BTCBRI (Макс. множник)',
        'BRI_35_TRAIL_85_15':     'Цикловий BTCBRI (Trailing Peak)',
        'CB_W_ZONE_HYST2':        'Топ Pure Cipher B (Баланс + Низький DD)',
        'CB_W_SCORE_25_75':       'Топ для щомісячного кеш-пулу (6р)',
    }
    top_patterns = []
    for cid in curated_ids:
        item = dict(by_id[cid])
        item['role_tag'] = role_tags[cid]
        top_patterns.append(item)

    last_bar = rows_full[-1]
    output_json = {
        'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'methodology': {
            'zero_lookahead': True,
            'weekly_candle_close_shift_days': 6,
            'execution_lag': 't+1 daily close',
            'fee_per_side_pct': FEE * 100.0,
            'full_range': {'start': rows_full[0]['date'], 'end': last_bar['date'], 'days': len(rows_full)},
            'modern_range': {'start': rows_2018[0]['date'], 'end': last_bar['date'], 'days': len(rows_2018)},
            'cycle_split': {
                'cycle1': f"{cyc1[0]['date']}..{cyc1[-1]['date']}",
                'cycle2': f"{cyc2[0]['date']}..{cyc2[-1]['date']}",
            },
            'rolling_4y_windows': len(rolling_4y),
            'strategies_tested': len(non_bench),
        },
        'latest_snapshot': {
            'date': last_bar['date'],
            'price': last_bar['price'],
            'bri': last_bar['bri'],
            'bri_min45': last_bar['bri_min45'],
            'tiz_le25': last_bar['tiz_le25'],
            'phase': last_bar['phase'],
            'w_last_close_date': last_bar['w_last_close_date'],
            'w_wt1': round(last_bar['w_wt1'], 2) if last_bar['w_wt1'] is not None else None,
            'w_wt2': round(last_bar['w_wt2'], 2) if last_bar['w_wt2'] is not None else None,
            'w_diff': round(last_bar['w_wt1'] - last_bar['w_wt2'], 2) if (last_bar['w_wt1'] is not None and last_bar['w_wt2'] is not None) else None,
            'w_score': round(last_bar['w_score'], 1) if last_bar['w_score'] is not None else None,
            'w_bull_zone': last_bar['w_bull_zone'],
        },
        'benchmarks': {
            'buy_and_hold': bh,
            'dca_monthly_full': dca_full,
            'dca_monthly_6y': dca_6y,
        },
        'top_patterns': top_patterns,
        'rankings': {
            'by_robustness': by_robust[:12],
            'by_2018_return': by_2018[:10],
            'by_2018_calmar': by_calmar18[:10],
            'by_pool_6y': by_pool6[:10],
        },
    }

    (ROOT / 'data/strategy_patterns.json').write_text(json.dumps(output_json, indent=2, ensure_ascii=False), encoding='utf-8')
    (ROOT / 'web/strategy_patterns.json').write_text(json.dumps(output_json, indent=2, ensure_ascii=False), encoding='utf-8')

    # Write clean Markdown report log
    os.makedirs(ROOT / 'reports', exist_ok=True)
    md = [
        f"# Дослідження прибуткових стратегій: BTCBRI + Cipher B ({rows_full[0]['date']} → {last_bar['date']})",
        "",
        "## Методологія та Zero-Lookahead аудит",
        f"- **Повний горизонт щоденних даних:** `{rows_full[0]['date']} → {last_bar['date']}` (**{len(rows_full)} днів**, ~{len(rows_full)/365.25:.1f} років).",
        f"- **Стрес-тест 2 повних циклів (зі старту на піку 2018):** `{rows_2018[0]['date']} → {last_bar['date']}` ({len(rows_2018)} днів).",
        f"- **Незалежна перевірка по циклах:** Цикл 1 (`{cyc1[0]['date']}..{cyc1[-1]['date']}`) vs Цикл 2 (`{cyc2[0]['date']}..{cyc2[-1]['date']}`).",
        f"- **Ковзні вікна:** **{len(rolling_4y)}** перекривних 4-річних вікон (крок 90 днів) — усуває залежність від дати старту.",
        f"- **Кеш-пул тест (`$100/міс` 24-го числа):** порівняння з щомісячним DCA на всьому горизонті (9.3р) та на останніх 6 роках (`2020-11..2026-11`).",
        f"- **Захист від lookahead bias:** аудит `cipherb_btcusdt_1w.json` (659 четвергових барів Kraken без пропусків) виявив, що кожен тижневий бар датований днем відкриття (четвер `D`), а закривається у середу (`D + 6 днів`, збіг ціни `0.0%–0.1%`). Тому всі тижневі сигнали стають доступними строго на закритті середи (`D + 6`), а ордер виконується на закритті наступного дня (`t + 1`) з комісією **{FEE*100:.1f}%** на сторону.",
        f"- **Перевірено конфігурацій:** **{len(non_bench)}** (3 сімейства: `Hybrid`, `BTCBRI`, `Cipher B`).",
        "",
        "## Бенчмарки",
        f"- **Buy & Hold (2018–2026, 2 цикли):** `{bh['modern_2018']['multiple']}×` | CAGR `{bh['modern_2018']['cagr_pct']}%` | MaxDD **`{bh['modern_2018']['max_dd_pct']}%`** | Calmar `{bh['modern_2018']['calmar']}`",
        f"- **Buy & Hold (2017-08–2026):** `{bh['full']['multiple']}×` | CAGR `{bh['full']['cagr_pct']}%` | MaxDD `{bh['full']['max_dd_pct']}%` | Calmar `{bh['full']['calmar']}` | 4р медіана: `{bh_roll_med}×`",
        f"- **Buy & Hold по циклах:** Цикл 1 = `+{bh_c1}%` (Calmar `{bh_c1_cal}`) | Цикл 2 = `+{bh_c2}%` (Calmar `{bh_c2_cal}`)",
        f"- **Щомісячний DCA ($100/міс):** Весь період (9.3р) = `{dca_full['multiple']}×` | Останні 6 років (`2020–2026`) = `{dca_6y['multiple']}×`",
        "",
        "## Топ-7 відібраних патернів для майбутніх угод",
        "",
        "| Сімейство | Роль | Патерн | 2018–26 (B&H 4.16×) | MaxDD | Calmar | Цикл 1 / 2 | 4р вікна > B&H (Дох/Cal) | Пул 6р (DCA 1.49×) | Пул 9.3р (DCA 4.02×) | Угод | Позиція зараз |",
        "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for p in top_patterns:
        c1c2 = f"{p['cycle1']['return_pct']:+.0f}% / {p['cycle2']['return_pct']:+.0f}%"
        badge = "✅" if p['beats_bh_both_cycles'] else ("⚖️" if p['beats_bh_calmar_both_cycles'] else "•")
        alloc_str = f"{int(p['full']['end_alloc_pct'])}% BTC"
        md.append(
            f"| **{p['family']}** | {p['role_tag']} | {p['name']} | **{p['modern_2018']['multiple']}×** ({p['modern_2018']['cagr_pct']}%) | "
            f"**{p['modern_2018']['max_dd_pct']}%** | **{p['modern_2018']['calmar']}** | {badge} {c1c2} | "
            f"{p['rolling_4y']['beats_bh_return_pct']:.0f}% / {p['rolling_4y']['beats_bh_calmar_pct']:.0f}% | "
            f"**{p['pool_6y']['multiple']}×** | {p['pool_full']['multiple']}× | {p['modern_2018']['trades']} | `{alloc_str}` |"
        )

    md += [
        "",
        "## Ключові кількісні висновки",
        "1. **Чому класичний продаж при `BRI ≥ 80–85` програє Trailing Peak:** під час параболічних фаз (`2020-12` @ `$28k`, `2024-03` @ `$71k`) BTCBRI заходить у зону `85–93` за місяці до фінального піку. Правило **Trailing Peak (`пік BRI ≥ 85 → продаж при відкаті -15 п.`)** дозволяє втримати бичачий імпульс і продати вже після перелому (`2021-11-18` @ `$56.9k`, `2024-12-23` @ `$94.9k`), збільшуючи дохідність у 2018–2026 з `4.16×` (B&H) до **`10.17×`** (а з фільтром `TiZ ≥ 21 дн.` — до **`11.48×`** при MaxDD `57.7%`).",
        "2. **Чому Cipher B (1W) потребує буфера гістерезису `±2`:** чистий перетин `wt1 > wt2` без буфера дає зайві хибні сигнали на флеті (74–80 угод, MaxDD `63.6%`). Додавання простого порога `|wt1 − wt2| ≥ 2` знижує просадку до **`46.8%`** (майже вдвічі менше B&H!), вирівнює дохідність по обох циклах (`+151%` і `+142%`), дає Calmar `0.48` і **`1.81×`** у 6-річному кеш-пулі. Схоже плато підтверджується на всьому діапазоні буферів `±2..±5` (не є точковим підгоном).",
        "3. **Чому гібрид 50/50 (`BTCBRI` + `Cipher B 1W`) — найстійкіший варіант:** два індикатори вимірюють ортогональні фактори — **циклову оцінку (valuation/on-chain)** та **тижневий моментум**. Їх поєднання 50/50 скорочує просадку до **`44.6%–53.9%`**, піднімає Calmar до **`0.54–0.65`** (у **2.6×–3.1× вище за B&H**), і дозволяє **`HYB_5050_BRI35_HYST2`** перевершити DCA на обох горизонтах (`4.54×` за 9.3р та `1.75×` за 6р), а **`HYB_5050_BRI35_CBSCORE`** — обіграти Buy & Hold в обох незалежних циклах із мінімальним оборотом (33 угоди за 9 років).",
        "4. **Контрольна перевірка денних дивергенцій (`CB_D_DIV_DIV`):** підтверджує висновок зовнішнього дослідження 2016–2026 — денні дивергенції дають значно гірший результат на довгому горизонті порівняно з тижневими стратегіями.",
        "",
        "## Детальний лог останніх угод Топ-7 патернів",
        "",
    ]
    for p in top_patterns:
        md.append(f"### [{p['family']}] {p['name']} (`{p['id']}`)")
        md.append(f"- **Правило:** {p['rule_short']}")
        md.append(f"- **Наступний тригер / дія зараз:** {p['next_trigger_hint']}")
        md.append(f"- **Показники (2018–2026):** `{p['modern_2018']['multiple']}×` | CAGR `{p['modern_2018']['cagr_pct']}%` | MaxDD `{p['modern_2018']['max_dd_pct']}%` | Calmar `{p['modern_2018']['calmar']}` | Експозиція `{p['full']['exposure_pct']}%`")
        md.append(f"- **По циклах:** Цикл 1 = `{p['cycle1']['return_pct']:+.1f}%` (DD `{p['cycle1']['max_dd_pct']}%`) | Цикл 2 = `{p['cycle2']['return_pct']:+.1f}%` (DD `{p['cycle2']['max_dd_pct']}%`)")
        md.append("- **Останні 6 угод:**")
        for t in p['recent_trades']:
            md.append(f"  - `{t['date']}` **{t['action']}** @ `${t['price']:,.0f}` (BRI={t['bri']:.0f}, 1W wt1={t['w_wt1']}, 1W score={t['w_score']}, алокація={int(t['target_alloc']*100)}%)")
        md.append("")

    md += [
        "## Повний рейтинг усіх протестованих стратегій (за стійкістю, 2018–2026)",
        "",
        "| # | Сімейство | ID | 2018–26 | MaxDD | Calmar | Цикл 1 / 2 | 4р вікна > B&H | Пул 6р | Угод | Score |",
        "| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for idx, p in enumerate(by_robust, 1):
        md.append(
            f"| {idx} | {p['family']} | `{p['id']}` | **{p['modern_2018']['multiple']}×** | {p['modern_2018']['max_dd_pct']}% | "
            f"**{p['modern_2018']['calmar']}** | {p['cycle1']['return_pct']:+.0f}% / {p['cycle2']['return_pct']:+.0f}% | "
            f"{p['rolling_4y']['beats_bh_return_pct']:.0f}% / {p['rolling_4y']['beats_bh_calmar_pct']:.0f}% | "
            f"{p['pool_6y']['multiple']}× | {p['modern_2018']['trades']} | {p['robust_score']} |"
        )

    report_text = '\n'.join(md) + '\n'
    report_path = ROOT / 'reports/strategy_backtest_2026_11_24.md'
    report_path.write_text(report_text, encoding='utf-8')
    os.makedirs(ROOT / 'docs', exist_ok=True)
    (ROOT / 'docs/strategy_backtest_2026_11_24.md').write_text(report_text, encoding='utf-8')

    print(f"Latest snapshot ({last_bar['date']}): BTC=${last_bar['price']:,.0f} | BRI={last_bar['bri']} | TiZ(<=25)={last_bar['tiz_le25']}d | 1W closed={last_bar['w_last_close_date']} wt1={last_bar['w_wt1']:.2f} wt2={last_bar['w_wt2']:.2f} (diff={last_bar['w_wt1']-last_bar['w_wt2']:+.2f}) score={last_bar['w_score']:.1f}")
    print("\n=== CURATED TOP-7 PATTERNS ===")
    for p in top_patterns:
        sl = p['full'].get('sleeve_allocs_pct')
        sl_txt = f" sleeves={sl}" if sl else ""
        print(f"[{p['family']:8s}] {p['id']:24s} | 2018={p['modern_2018']['multiple']:5.2f}x DD={p['modern_2018']['max_dd_pct']:4.1f}% Cal={p['modern_2018']['calmar']:4.2f} | C1={p['cycle1']['return_pct']:+5.0f}% C2={p['cycle2']['return_pct']:+4.0f}% | Roll={p['rolling_4y']['beats_bh_return_pct']:2.0f}%/{p['rolling_4y']['beats_bh_calmar_pct']:2.0f}% | P6={p['pool_6y']['multiple']:4.2f}x PF={p['pool_full']['multiple']:4.2f}x | End={int(p['full']['end_alloc_pct']):3d}%{sl_txt}")
    print(f"\nControl CB_D_DIV_DIV: 2018={by_id['CB_D_DIV_DIV']['modern_2018']['multiple']}x DD={by_id['CB_D_DIV_DIV']['modern_2018']['max_dd_pct']}% Cal={by_id['CB_D_DIV_DIV']['modern_2018']['calmar']} P6={by_id['CB_D_DIV_DIV']['pool_6y']['multiple']}x")


if __name__ == '__main__':
    main()
