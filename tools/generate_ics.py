"""
Generate web/bitcoin_buy_risk.ics — a subscribable iCal feed of daily index values.

Each run:
  1. Reads data/data.json for today's scores.
  2. Appends / updates an entry in data/history/scores.json.
  3. Regenerates web/bitcoin_buy_risk.ics from the full history.

Subscribe URL (after GitHub Pages deploy):
  https://<user>.github.io/<repo>/bitcoin_buy_risk.ics
"""

import json
import os
import datetime

ROOT = os.path.join(os.path.dirname(__file__), '..')
DATA_JSON      = os.path.join(ROOT, 'data', 'data.json')
SCORES_HISTORY = os.path.join(ROOT, 'data', 'history', 'scores.json')
ICS_OUTPUT     = os.path.join(ROOT, 'web', 'bitcoin_buy_risk.ics')


def zone_label(score: int) -> str:
    if score < 20:
        return "Capitulation / Bottom"
    if score < 40:
        return "Discount / Accumulation"
    if score < 60:
        return "Neutral Zone"
    if score < 75:
        return "Overheating / Caution"
    return "Extreme Overheating / Sell"


def load_history() -> list:
    if os.path.exists(SCORES_HISTORY):
        with open(SCORES_HISTORY, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_history(entries: list) -> None:
    os.makedirs(os.path.dirname(SCORES_HISTORY), exist_ok=True)
    with open(SCORES_HISTORY, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def update_history(entries: list, data: dict) -> list:
    """Append or update today's entry."""
    ts = data.get('timestamp', '')
    date_str = ts[:10] if ts else datetime.date.today().isoformat()

    phase_data = data.get('phase', {})
    phase_str = phase_data.get('phase') if isinstance(phase_data, dict) else None
    w_bot = phase_data.get('w_bot') if isinstance(phase_data, dict) else None

    entry = {
        'date':          date_str,
        'timestamp':     ts,
        'final_score':   data.get('final_score'),
        'onchain_score': data.get('onchain_score'),
        'tech_score':    data.get('tech_score'),
        'btc_price':     data.get('btc_price'),
        'phase':         phase_str,
        'w_bot':         w_bot,
        # Raw metrics — same units as scores.json (nupl in %, asopr actual value)
        'nupl':          data.get('nupl'),
        'mvrv':          data.get('mvrv_z_score') or data.get('mvrv'),
        'asopr':         data.get('asopr'),
        'mayer_multiple':data.get('mayer_multiple'),
        'cvdd_ratio':    data.get('cvdd_ratio'),
        'rhodl_ratio':   data.get('rhodl_ratio'),
        'puell':         data.get('puell'),
        'fear_greed':    data.get('fear_greed'),
        'm2_yoy':        data.get('m2_mom') or data.get('m2_yoy') or data.get('m2'),
        'dxy':           data.get('dxy'),
        'cipherb_daily': data.get('cipherb'),
        'pi_gap_pct':    data.get('pi_gap_pct'),
        'funding_rate':  data.get('funding_rate'),
    }

    # Replace existing entry for same date, otherwise append
    for i, e in enumerate(entries):
        if e.get('date') == date_str:
            entries[i] = entry
            return entries
    entries.append(entry)
    return entries


def ics_escape(text: str) -> str:
    """Escape special chars for ICS text fields."""
    # Order matters: escape backslash first, then others
    text = text.replace('\\', '\\\\')
    text = text.replace(';', '\\;')
    text = text.replace(',', '\\,')
    text = text.replace('\n', '\\n')  # real newline -> ICS newline marker
    return text


def fold(line: str) -> str:
    """Fold long ICS lines at 75 octets."""
    encoded = line.encode('utf-8')
    if len(encoded) <= 75:
        return line
    result = []
    chunk = b''
    for char in line:
        c = char.encode('utf-8')
        if len(chunk) + len(c) > 75:
            result.append(chunk.decode('utf-8'))
            chunk = b' ' + c
        else:
            chunk += c
    if chunk:
        result.append(chunk.decode('utf-8'))
    return '\r\n'.join(result)


def generate_ics(entries: list) -> str:
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//Bitcoin Buy Risk//EN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'X-WR-CALNAME:Bitcoin Buy Risk',
        'X-WR-CALDESC:Daily Bitcoin Buy Risk Index — bitcoin-buy-risk.ics',
        'X-WR-TIMEZONE:UTC',
        'REFRESH-INTERVAL;VALUE=DURATION:PT12H',
    ]

    for e in sorted(entries, key=lambda x: x['date']):
        date_str  = e['date'].replace('-', '')          # YYYYMMDD
        next_date = (datetime.date.fromisoformat(e['date']) + datetime.timedelta(days=1)).strftime('%Y%m%d')

        final   = e.get('final_score')
        oc      = e.get('onchain_score')
        tech    = e.get('tech_score')
        price   = e.get('btc_price')
        ts      = e.get('timestamp', '')

        if final is None:
            continue

        summary = f'BBR {final} [C{oc} | T{tech}]'
        description = zone_label(final)
        if price:
            description += f' · BTC ${price:,}'

        uid = f'{date_str}@bitcoin-buy-risk'

        lines += [
            'BEGIN:VEVENT',
            f'DTSTART;VALUE=DATE:{date_str}',
            f'DTEND;VALUE=DATE:{next_date}',
            f'SUMMARY:{ics_escape(summary)}',
            f'DESCRIPTION:{ics_escape(description)}',
            f'UID:{uid}',
            'END:VEVENT',
        ]

    lines.append('END:VCALENDAR')
    return '\r\n'.join(fold(l) for l in lines) + '\r\n'


def find_entry_nearest(entries, target_date):
    """Return the entry whose date is closest to (and not after) target_date, or None."""
    candidates = [e for e in entries if datetime.date.fromisoformat(e['date']) <= target_date]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e['date'])


def patch_data_json(data: dict, entries: list) -> None:
    """Add prev_day, prev_week and score_history fields to data/data.json."""
    today_str = (data.get('timestamp') or '')[:10] or datetime.date.today().isoformat()
    today = datetime.date.fromisoformat(today_str)

    others = [e for e in entries if e['date'] != today_str]

    yesterday = find_entry_nearest(others, today - datetime.timedelta(days=1))
    week_ago  = find_entry_nearest(others, today - datetime.timedelta(days=7))

    def entry_summary(e):
        if e is None:
            return None
        return {
            'date':          e['date'],
            'final_score':   e.get('final_score'),
            'onchain_score': e.get('onchain_score'),
            'tech_score':    e.get('tech_score'),
        }

    data['prev_day']  = entry_summary(yesterday)
    data['prev_week'] = entry_summary(week_ago)

    # Build full score_history from backfill + current scores (2018-present)
    score_map = {}

    backfill_path = os.path.join(ROOT, 'data', 'history', 'backfill_scores.json')
    scores_path   = os.path.join(ROOT, 'data', 'history', 'scores.json')
    if os.path.exists(backfill_path) and os.path.exists(scores_path):
        with open(backfill_path, encoding='utf-8') as f:
            backfill = json.load(f).get('series', [])
        with open(scores_path, encoding='utf-8') as f:
            price_by_date = {r['date']: r.get('btc_price') for r in json.load(f)}
        for row in backfill:
            d = row.get('date', '')
            if d < '2018-01-01':
                continue
            sc = row.get('v2_final')
            if sc is None:
                continue
            score_map[d] = {'date': d, 'score': sc, 'price': price_by_date.get(d)}

    # Current scores.json entries override backfill for recent dates
    for e in entries:
        d = e['date']
        sc = e.get('final_score')
        if sc is None:
            continue
        score_map[d] = {'date': d, 'score': sc, 'price': e.get('btc_price')}

    data['score_history'] = sorted(score_map.values(), key=lambda e: e['date'])

    # Recovery Window: was score ≤22 within last 120 days, and is current score 25-45?
    current_score = data.get('final_score')
    cutoff_120d = (today - datetime.timedelta(days=120)).isoformat()
    rw_entry = None
    for sh_entry in reversed(data['score_history']):
        d = sh_entry.get('date', '')
        if d < cutoff_120d:
            break
        if d == today_str:
            continue
        sc = sh_entry.get('score')
        if sc is not None and sc <= 22:
            rw_entry = sh_entry
            break

    if rw_entry and current_score is not None and 25 <= current_score <= 45:
        days_since = (today - datetime.date.fromisoformat(rw_entry['date'])).days
        data['recovery_window'] = {
            'active': True,
            'days_since_bottom': days_since,
            'bottom_date': rw_entry['date'],
            'bottom_score': rw_entry['score'],
        }
    else:
        data['recovery_window'] = {'active': False}

    # Calculate active trading signals from history
    try:
        from trading_signals import calculate_active_signal
        data['trading_signals'] = calculate_active_signal(data['score_history'])
        print(f"Trading Signals calculated: {data['trading_signals']}")
    except Exception as tse:
        print('Failed to calculate trading signals:', tse)

    with open(DATA_JSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Mirror data.json to web/ if web/ exists
    web_data_json = os.path.join(ROOT, 'web', 'data.json')
    if os.path.exists(os.path.join(ROOT, 'web')):
        try:
            with open(web_data_json, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def main():
    with open(DATA_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)

    entries = load_history()
    entries = update_history(entries, data)
    save_history(entries)

    patch_data_json(data, entries)

    ics_content = generate_ics(entries)
    os.makedirs(os.path.dirname(ICS_OUTPUT), exist_ok=True)
    with open(ICS_OUTPUT, 'w', encoding='utf-8', newline='') as f:
        f.write(ics_content)

    print(f'Generated {ICS_OUTPUT} ({len(entries)} events)')


if __name__ == '__main__':
    main()
