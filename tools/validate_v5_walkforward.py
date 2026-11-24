#!/usr/bin/env python3
"""Head-to-head V4 vs V5 zero-lookahead walk-forward validation.

Compares `/tmp/scores_v4_backup.json` (V4 baseline) against `data/history/scores.json` (V5)
using `tools/discover_strategies.py`'s zero-lookahead simulator (0.5% fee/side, t+1 close,
weekly Kraken +6d close shift).

Writes `docs/v5_walkforward_report.md`.
"""
import datetime
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import discover_strategies as ds

V4_BACKUP = Path('/tmp/scores_v4_backup.json')
V5_SCORES = ROOT / 'data/history/scores.json'


def load_timeline_from_scores(scores_path: Path, start_date='2017-08-17'):
    """Load discover_strategies timeline with BRI swapped from `scores_path`."""
    orig_path = ROOT / 'data/history/scores.json'
    base_rows = ds.load_aligned_timeline(start_date)
    if scores_path == orig_path:
        return base_rows
    alt = json.loads(scores_path.read_text(encoding='utf-8'))
    alt_map = {r['date'][:10]: r for r in alt if r.get('date') and r.get('final_score') is not None}
    out = []
    bri_hist = []
    tiz25 = 0
    tiz30 = 0
    for r in base_rows:
        ar = alt_map.get(r['date'])
        bri = float(ar['final_score']) if ar else r['bri']
        bri_hist.append(bri)
        tiz25 = (tiz25 + 1) if bri <= 25 else 0
        tiz30 = (tiz30 + 1) if bri <= 30 else 0
        nr = dict(r)
        nr['bri'] = bri
        nr['tiz_le25'] = tiz25
        nr['tiz_le30'] = tiz30
        nr['bri_min45'] = min(bri_hist[-45:])
        nr['phase'] = ar.get('phase') if ar else r['phase']
        out.append(nr)
    return out


def spearman_365d(rows):
    by_date = {r['date']: r for r in rows}
    xs, ys = [], []
    for r in rows:
        if r['date'] < '2018-01-01':
            continue
        d_fwd = (datetime.date.fromisoformat(r['date']) + datetime.timedelta(days=365)).isoformat()
        rf = by_date.get(d_fwd)
        if not rf:
            continue
        xs.append(r['bri'])
        ys.append((rf['price'] / r['price'] - 1.0) * 100.0)
    if len(xs) < 100:
        return 0.0

    def rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg_r = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg_r
            i = j + 1
        return ranks

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return round(num / den, 4) if den > 0 else 0.0


def main():
    if not V4_BACKUP.exists():
        print(f"ERROR: {V4_BACKUP} not found. Run backup first.")
        sys.exit(1)

    v4_rows = load_timeline_from_scores(V4_BACKUP, '2017-08-17')
    v5_rows = load_timeline_from_scores(V5_SCORES, '2017-08-17')

    v4_raw = {r['date'][:10]: r for r in json.loads(V4_BACKUP.read_text(encoding='utf-8'))}
    v5_raw = {r['date'][:10]: r for r in json.loads(V5_SCORES.read_text(encoding='utf-8'))}

    milestones = [
        ('2017-12-17', '2017 Top', 'TOP'),
        ('2018-12-15', '2018 Bot', 'BOTTOM'),
        ('2020-03-13', '2020 COVID Bot', 'BOTTOM'),
        ('2021-04-14', '2021 Top1', 'TOP'),
        ('2021-11-10', '2021 Top2', 'TOP'),
        ('2022-06-18', '2022 Jun Bot', 'BOTTOM'),
        ('2022-11-21', '2022 FTX Bot', 'BOTTOM'),
        ('2024-03-14', '2024 ETF Top', 'TOP'),
        ('2025-10-06', '2025 ATH Top', 'TOP'),
        ('2026-11-23', '2026 Now', 'NOW'),
    ]

    strats = {s['id']: s for s in ds.build_strategies()}
    check_ids = [
        'BRI_35_TRAIL_85_15',
        'BRI_TIZ25_21D_T85',
        'HYB_5050_BRI35_HYST2',
        'HYB_5050_TIZ21_HYST2',
        'HYB_5050_BRI35_CBSCORE',
    ]

    def eval_suite(rows):
        r18 = [x for x in rows if x['date'] >= '2018-01-01']
        c1 = [x for x in r18 if x['date'] <= '2022-06-15']
        c2 = [x for x in r18 if x['date'] > '2022-06-15']
        r6 = [x for x in rows if x['date'] >= '2020-11-24']
        roll4 = [rows[i:i + 1460] for i in range(0, len(rows) - 1460, 90)]
        bh = strats['BENCH_BH']
        bh_roll = [ds.simulate_lump_sum(w, bh) for w in roll4]
        out = {}
        for sid in check_ids:
            st = strats[sid]
            s18 = ds.simulate_lump_sum(r18, st)
            sc1 = ds.simulate_lump_sum(c1, st)
            sc2 = ds.simulate_lump_sum(c2, st)
            p6 = ds.simulate_monthly_pool(r6, st)
            rcal = sum(1 for k, w in enumerate(roll4) if ds.simulate_lump_sum(w, st)['calmar'] >= bh_roll[k]['calmar']) / len(roll4) * 100
            out[sid] = {'m18': s18, 'c1': sc1, 'c2': sc2, 'p6': p6, 'roll_cal': round(rcal, 1)}
        return out

    res_v4 = eval_suite(v4_rows)
    res_v5 = eval_suite(v5_rows)
    sp_v4 = spearman_365d(v4_rows)
    sp_v5 = spearman_365d(v5_rows)

    md = [
        "# V4 vs V5 Walk-Forward & Calibration Report",
        "",
        "## 1. Cycle Milestone Calibration (V4 vs V5)",
        "",
        "| Date | Milestone | V4 BRI (Phase) | V5 BRI (Phase) | Delta |",
        "| :--- | :--- | :---: | :---: | :---: |",
    ]
    v4_tops, v5_tops, v4_bots, v5_bots = [], [], [], []
    for d, label, kind in milestones:
        a = v4_raw.get(d, {})
        b = v5_raw.get(d, {})
        s4, p4 = a.get('final_score'), a.get('phase')
        s5, p5 = b.get('final_score'), b.get('phase')
        delta = (s5 - s4) if (s4 is not None and s5 is not None) else 0
        md.append(f"| `{d}` | {label} | `{s4}` ({p4}) | **`{s5}`** ({p5}) | `{delta:+d}` |")
        if kind == 'TOP' and s4 is not None and s5 is not None:
            v4_tops.append(s4)
            v5_tops.append(s5)
        elif kind == 'BOTTOM' and s4 is not None and s5 is not None:
            v4_bots.append(s4)
            v5_bots.append(s5)

    sep_v4 = round(sum(v4_tops) / len(v4_tops) - sum(v4_bots) / len(v4_bots), 1)
    sep_v5 = round(sum(v5_tops) / len(v5_tops) - sum(v5_bots) / len(v5_bots), 1)

    md += [
        "",
        f"- **Mean Top–Bottom Separation:** V4 = `+{sep_v4} pts` → V5 = **`+{sep_v5} pts`**",
        f"- **365d Forward Return Spearman Correlation (2018–2025):** V4 = `{sp_v4}` → V5 = **`{sp_v5}`**",
        "",
        "## 2. Zero-Lookahead Strategy Comparison (2018–2026)",
        "",
        "| Strategy | V4 2018–26 (DD / Calmar) | V5 2018–26 (DD / Calmar) | V4 C2 (2022–26) | V5 C2 (2022–26) | V4→V5 Pool 6Y |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]
    for sid in check_ids:
        a, b = res_v4[sid], res_v5[sid]
        md.append(
            f"| `{sid}` | {a['m18']['multiple']}× (-{a['m18']['max_dd_pct']}%, {a['m18']['calmar']}) | "
            f"**{b['m18']['multiple']}× (-{b['m18']['max_dd_pct']}%, {b['m18']['calmar']})** | "
            f"{a['c2']['return_pct']:+.0f}% (Cal {a['c2']['calmar']}) | "
            f"**{b['c2']['return_pct']:+.0f}% (Cal {b['c2']['calmar']})** | "
            f"{a['p6']['multiple']}× → **{b['p6']['multiple']}×** |"
        )

    out_path = ROOT / 'docs/v5_walkforward_report.md'
    out_path.write_text('\n'.join(md) + '\n', encoding='utf-8')
    print('\n'.join(md))


if __name__ == '__main__':
    main()
