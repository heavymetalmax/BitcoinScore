#!/usr/bin/env python3
"""Build local BTC daily-close reference tables. Research only, no production imports.

Run --self-test for synthetic checks. No network access or third-party data included.
See docs/btc_extrema_reference.md for historical events, sources and limitations.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest


def load_prices(path, cutoff):
    raw = Path(path).read_bytes()
    if Path(path).suffix.lower() == '.csv':
        rows = list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
    else:
        rows = json.loads(raw)
        if isinstance(rows, dict):
            rows = rows['series']
    out = []
    seen = set()
    for row in rows:
        date = str(row['date'])
        dt.date.fromisoformat(date)
        if date in seen:
            raise ValueError('Duplicate date: ' + date)
        seen.add(date)
        close = float(row.get('close', row.get('btc_price', 0)))
        if not (0 < close < float('inf')):
            raise ValueError('Invalid close: ' + date)
        if date <= cutoff:
            out.append({'date': date, 'close': close})
    out.sort(key=lambda r: r['date'])
    if len(out) < 2:
        raise ValueError('Need at least two positive daily closes before cutoff')
    return out, hashlib.sha256(raw).hexdigest()


def swings(rows, threshold):
    if not 0 < threshold < 1:
        raise ValueError('threshold must be between zero and one')
    lo = hi = rows[0]
    direction = 0
    candidate = None
    result = []

    def emit(row, kind, confirmation=None):
        known = confirmation['date'] if confirmation else ''
        available = ((dt.date.fromisoformat(known) + dt.timedelta(days=1)).isoformat()
                     + 'T00:00:00Z') if known else ''
        return {'threshold_pct': threshold * 100, 'kind': kind,
                'extreme_date': row['date'], 'close': row['close'],
                'confirmed_on': known, 'available_after_utc': available,
                'status': 'confirmed' if confirmation else 'provisional_at_cutoff',
                'left_censored': not result}

    for row in rows[1:]:
        price = row['close']
        if direction == 0:
            if price < lo['close']:
                lo = row
            if price > hi['close']:
                hi = row
            if price >= lo['close'] * (1 + threshold):
                result.append(emit(lo, 'LOW', row))
                direction, candidate = 1, row
            elif price <= hi['close'] * (1 - threshold):
                result.append(emit(hi, 'HIGH', row))
                direction, candidate = -1, row
        elif direction == 1:
            if price > candidate['close']:
                candidate = row
            if price <= candidate['close'] * (1 - threshold):
                result.append(emit(candidate, 'HIGH', row))
                direction, candidate = -1, row
        else:
            if price < candidate['close']:
                candidate = row
            if price >= candidate['close'] * (1 + threshold):
                result.append(emit(candidate, 'LOW', row))
                direction, candidate = 1, row
    if candidate is None:
        result.append(emit(rows[-1], 'UNDETERMINED'))
    else:
        result.append(emit(candidate, 'HIGH' if direction == 1 else 'LOW'))
    return result


def annual(rows):
    result = []
    for year in sorted({r['date'][:4] for r in rows}):
        subset = [r for r in rows if r['date'].startswith(year)]
        days = (dt.date(int(year) + 1, 1, 1) - dt.date(int(year), 1, 1)).days
        complete = (len(subset) == days and subset[0]['date'] == year + '-01-01'
                    and subset[-1]['date'] == year + '-12-31')
        for kind, selector in [('LOW_CLOSE', min), ('HIGH_CLOSE', max)]:
            row = selector(subset, key=lambda r: r['close'])
            result.append({'year': year, 'kind': kind, 'date': row['date'],
                           'close': row['close'], 'complete_calendar_year': complete,
                           'retrospective': True})
    return result


def save_csv(path, rows):
    with Path(path).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class ReferenceTests(unittest.TestCase):
    @staticmethod
    def rows(values):
        return [{'date': (dt.date(2020, 1, 1) + dt.timedelta(days=i)).isoformat(),
                 'close': float(v)} for i, v in enumerate(values)]

    def test_confirmation_and_next_midnight(self):
        r = swings(self.rows([100, 120, 130, 116]), .1)
        self.assertEqual(r[1]['extreme_date'], '2020-01-03')
        self.assertEqual(r[1]['confirmed_on'], '2020-01-04')
        self.assertEqual(r[1]['available_after_utc'], '2020-01-05T00:00:00Z')

    def test_endpoint_is_not_confirmed(self):
        r = swings(self.rows([100, 120, 130, 116]), .1)
        self.assertEqual(r[-1]['status'], 'provisional_at_cutoff')
        self.assertEqual(r[-1]['confirmed_on'], '')
        self.assertTrue(r[0]['left_censored'])

    def test_no_move_is_undetermined(self):
        self.assertEqual(swings(self.rows([100, 101, 102]), .4)[0]['kind'], 'UNDETERMINED')

    def test_confirmed_prefix_stability(self):
        rows = self.rows([100, 120, 130, 116, 110, 123, 140, 115])
        for n in range(2, len(rows)):
            old = [r for r in swings(rows[:n], .1) if r['status'] == 'confirmed']
            full = [r for r in swings(rows, .1) if r['status'] == 'confirmed']
            self.assertEqual(old, full[:len(old)])

    def test_missing_calendar_dates_not_complete(self):
        self.assertFalse(annual(self.rows([100, 110]))[0]['complete_calendar_year'])

    def test_duplicate_and_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'prices.json'
            p.write_text(json.dumps(self.rows([100, 110, 120])))
            rows, digest = load_prices(p, '2020-01-02')
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(digest), 64)
            p.write_text(json.dumps(self.rows([100, 110]) * 2))
            with self.assertRaises(ValueError):
                load_prices(p, '2020-01-05')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', help='Locally supplied daily-close CSV or JSON')
    parser.add_argument('--output-dir')
    parser.add_argument('--cutoff', help='Last included UTC day, YYYY-MM-DD; use a closed day')
    parser.add_argument('--source-label', default='locally supplied series, verify venue and timezone')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReferenceTests)
        raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
    if not (args.input and args.output_dir and args.cutoff):
        parser.error('--input, --output-dir and --cutoff are required')
    dt.date.fromisoformat(args.cutoff)
    if args.cutoff >= dt.datetime.now(dt.timezone.utc).date().isoformat():
        parser.error('cutoff must be a completed UTC day')
    rows, digest = load_prices(args.input, args.cutoff)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    gaps = sum((dt.date.fromisoformat(b['date']) - dt.date.fromisoformat(a['date'])).days != 1
               for a, b in zip(rows, rows[1:]))
    if gaps:
        parser.error('input has daily gaps; do not pretend gap-spanning confirmation is first daily close')
    counts = {}
    for pct in [10, 20, 40]:
        data = swings(rows, pct / 100)
        save_csv(out / ('swings_' + str(pct) + 'pct.csv'), data)
        counts[str(pct)] = len(data)
    save_csv(out / 'annual_close_extrema.csv', annual(rows))
    manifest = {'source_label': args.source_label, 'input_sha256': digest,
                'input_rows_used': len(rows), 'first_date': rows[0]['date'],
                'last_date': rows[-1]['date'], 'requested_cutoff': args.cutoff,
                'daily_gaps': gaps, 'swing_rows_including_final_candidate': counts,
                'price_basis': 'supplied daily close, not intraday',
                'thresholds_are_not_optimized': True, 'evaluation_only': True,
                'source_rights_not_established_by_this_script': True}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
