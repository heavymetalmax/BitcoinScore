"""
Sweep ADAPTIVE_BLEND (0.0 → 1.0) for adaptive metrics and measure
top/bottom separation on confirmed cycle events.

Metric coverage: nupl, mvrv, cvdd_ratio, mayer, etf_flows (all ADAPTIVE_METRICS).
History: local JSON files in data/history/.
Output: table + recommended blend weight.

Usage:  python tools/adaptive_blend_sweep.py
"""
import sys, os, json, datetime, math
sys.path.insert(0, '.')
from scraper.scoring import map_nupl, map_mvrv, map_cvdd, map_mayer_multiple

WIN_YEARS = 4

# Confirmed cycle events with known tops/bottoms (BTC price for reference)
EVENTS = [
    # (date, label, is_top)
    ('2018-12-15', 'Bear bottom $3k',    False),
    ('2019-06-26', 'Local peak $13k',    True),
    ('2020-03-13', 'COVID crash $3k',    False),
    ('2021-04-14', 'Spring ATH $63k',    True),
    ('2021-07-20', 'Summer dip $29k',    False),
    ('2021-11-10', 'Nov ATH $69k',       True),
    ('2022-06-18', 'Capitulation $17k',  False),
    ('2022-11-21', 'FTX bottom $15k',    False),
    ('2024-03-14', '2024 ATH $73k',      True),
    ('2025-01-20', 'Jan 2025 ATH $109k', True),
]

# unit_scale: multiply history value before applying fixed map
# (nupl history is fraction 0..1, map_nupl expects % -50..100)
METRICS = {
    'nupl':  ('data/history/nupl_history.json',  map_nupl,  100),
    'mvrv':  ('data/history/mvrv_history.json',  map_mvrv,  1),
    'cvdd':  ('data/history/cvdd_ratio_history.json', map_cvdd, 1),
    'mayer': ('data/history/mayer_history.json', map_mayer_multiple, 1),
}

def load_history(path):
    with open(path) as f:
        raw = json.load(f)
    # flat list: [[date, value], ...]
    if isinstance(raw, list):
        return [(r[0][:10], r[1]) for r in raw if r[1] is not None]
    # dict with 'series' key
    series = raw.get('series', [])
    if not series:
        return []
    # series items are [date, value] lists
    if isinstance(series[0], list):
        return [(r[0][:10], r[1]) for r in series if r[1] is not None]
    # series items are dicts (cvdd_ratio format)
    return [(r['date'][:10], r['ratio']) for r in series if r.get('ratio') is not None]

def causal_percentile(series, target_date_str, value, win_years):
    """Rolling percentile using only data up to target_date within win_years window."""
    td = datetime.date.fromisoformat(target_date_str)
    lo = (td - datetime.timedelta(days=int(win_years * 365))).isoformat()
    window = [v for d, v in series if lo <= d <= target_date_str and v is not None]
    if len(window) < 12:
        return None
    rank = sum(1 for v in window if v <= value)
    return round(rank / len(window) * 100)

def closest_value(series, target_date_str):
    """Find value on or nearest before target date."""
    target = datetime.date.fromisoformat(target_date_str)
    best = None
    for d, v in series:
        if datetime.date.fromisoformat(d) <= target:
            best = (d, v)
        else:
            break
    return best

def blended_score(series, target_date, value, fixed_fn, blend_w, unit_scale=1, win_years=WIN_YEARS):
    fixed = fixed_fn(value * unit_scale)
    if fixed is None:
        return None
    pct = causal_percentile(series, target_date, value, win_years)
    if pct is None:
        return fixed  # fallback
    return round(blend_w * pct + (1 - blend_w) * fixed)

def separation(tops, bots):
    if not tops or not bots:
        return None
    return round(sum(tops) / len(tops) - sum(bots) / len(bots), 1)

def run_sweep():
    # Load all histories
    histories = {}
    for name, (path, fn, uscale) in METRICS.items():
        if os.path.exists(path):
            histories[name] = (load_history(path), fn, uscale)
        else:
            alt = path.replace('cvdd_ratio_history', 'cvdd_history')
            if os.path.exists(alt):
                histories[name] = (load_history(alt), fn, uscale)
            else:
                print(f"  SKIP {name}: {path} not found")

    blends = [round(b * 0.1, 1) for b in range(0, 11)]  # 0.0 → 1.0

    print("\n" + "=" * 70)
    print("ADAPTIVE_BLEND SWEEP  (fixed=0.0 → pure_percentile=1.0)")
    print("Metric coverage:", ', '.join(histories.keys()))
    print("Events: tops={}, bottoms={}".format(
        sum(1 for _, _, t in EVENTS if t),
        sum(1 for _, _, t in EVENTS if not t)
    ))
    print("=" * 70)
    print(f"\n{'Blend':>6}  {'Top avg':>8}  {'Bot avg':>8}  {'Sep':>6}  {'*'}")
    print("─" * 42)

    results = []
    for bw in blends:
        top_scores, bot_scores = [], []
        for date, label, is_top in EVENTS:
            metric_scores = []
            for name, (series, fn, uscale) in histories.items():
                hit = closest_value(series, date)
                if hit is None:
                    continue
                s = blended_score(series, hit[0], hit[1], fn, bw, uscale)
                if s is not None:
                    metric_scores.append(s)
            if metric_scores:
                avg = round(sum(metric_scores) / len(metric_scores))
                if is_top:
                    top_scores.append(avg)
                else:
                    bot_scores.append(avg)

        sep = separation(top_scores, bot_scores)
        top_avg = round(sum(top_scores) / len(top_scores), 1) if top_scores else None
        bot_avg = round(sum(bot_scores) / len(bot_scores), 1) if bot_scores else None
        results.append((bw, top_avg, bot_avg, sep))
        best_mark = ""
        if sep is not None and len(results) > 1:
            best_mark = " ◄ best so far" if sep == max(r[3] for r in results if r[3] is not None) else ""
        print(f"{bw:>6.1f}  {str(top_avg):>8}  {str(bot_avg):>8}  {str(sep):>6}{best_mark}")

    # per-metric per-event detail at key blends (0.0, 0.5, best)
    valid = [(bw, sep) for bw, _, _, sep in results if sep is not None]
    best_bw = max(valid, key=lambda x: x[1])[0] if valid else 0.5

    print(f"\nBest blend: {best_bw}  (separation {max(v[1] for v in valid):.1f} pts)")
    print(f"Current:    0.5  (separation {next(s for b,s in valid if b==0.5):.1f} pts)")

    print("\n" + "─" * 70)
    print("PER-EVENT DETAIL  (blend=0.0 fixed | blend=0.5 current | blend=best)")
    print(f"{'Event':<25} {'Top?':>4}  {'fixed':>6}  {'0.5':>6}  {f'{best_bw}':>6}")
    print("─" * 55)
    for date, label, is_top in EVENTS:
        scores = {}
        for bw in [0.0, 0.5, best_bw]:
            metric_scores = []
            for name, (series, fn, uscale) in histories.items():
                hit = closest_value(series, date)
                if hit is None:
                    continue
                s = blended_score(series, hit[0], hit[1], fn, bw, uscale)
                if s is not None:
                    metric_scores.append(s)
            scores[bw] = round(sum(metric_scores)/len(metric_scores)) if metric_scores else None
        tag = "TOP" if is_top else "bot"
        print(f"{label:<25} {tag:>4}  {str(scores.get(0.0)):>6}  {str(scores.get(0.5)):>6}  {str(scores.get(best_bw)):>6}")

if __name__ == '__main__':
    run_sweep()
