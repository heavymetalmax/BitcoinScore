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
        return "Капітуляція / Дно"
    if score < 40:
        return "Дискаунт / Накопичення"
    if score < 60:
        return "Нейтральна зона"
    if score < 75:
        return "Перегрів / Обережність"
    return "Екстремальний перегрів / Продаж"


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

    entry = {
        'date':          date_str,
        'timestamp':     ts,
        'final_score':   data.get('final_score'),
        'onchain_score': data.get('onchain_score'),
        'tech_score':    data.get('tech_score'),
        'btc_price':     data.get('btc_price'),
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

        uid = f'{date_str}@bitcoin-buy-risk'

        lines += [
            'BEGIN:VEVENT',
            f'DTSTART;VALUE=DATE:{date_str}',
            f'DTEND;VALUE=DATE:{next_date}',
            f'SUMMARY:{ics_escape(summary)}',
            f'UID:{uid}',
            'END:VEVENT',
        ]

    lines.append('END:VCALENDAR')
    return '\r\n'.join(fold(l) for l in lines) + '\r\n'


def main():
    with open(DATA_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)

    entries = load_history()
    entries = update_history(entries, data)
    save_history(entries)

    ics_content = generate_ics(entries)
    os.makedirs(os.path.dirname(ICS_OUTPUT), exist_ok=True)
    with open(ICS_OUTPUT, 'w', encoding='utf-8', newline='') as f:
        f.write(ics_content)

    print(f'Generated {ICS_OUTPUT} ({len(entries)} events)')


if __name__ == '__main__':
    main()
