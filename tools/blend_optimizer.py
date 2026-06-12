"""
Optimize orchestrator blend weights: meta = a*V2 + b*WR (a+b=1).

Target: meta should predict future 6-month BTC drawdown/returns.
  High meta at tops   → future returns negative → low risk score worked.
  Low  meta at bottoms → future returns positive → confirmed buying opp.

Method:
  1. Compute WR for all dates (batch, O(n log n))
  2. Load backfill_scores.json for V2
  3. Compute 6-month forward BTC return per date
  4. Map forward return → risk target [0,100]
  5. Minimize MSE(a*V2 + b*WR, target) over a ∈ [0,1], b=1-a
  6. Compare optimal blend to coherence-based blend (0.358/0.642)

NOTE: look-ahead bias — forward returns use future prices.
Research/validation use only.

Run: python tools/blend_optimizer.py
"""
import sys, json, math, datetime
sys.path.insert(0, '.')

BASE = 'data/history'

# ── 1. Load data ─────────────────────────────────────────────────────────────
print("Loading unified history...")
uh = json.load(open(f'{BASE}/unified_history.json', encoding='utf-8'))
unified = {r['date']: r for r in uh['series']}
sorted_dates = sorted(unified.keys())

print("Loading BTC price history...")
bp = json.load(open(f'{BASE}/btc_price_history.json', encoding='utf-8'))
bp_s = bp.get('series', bp) if isinstance(bp, dict) else bp
btc_price = {}
for r in bp_s:
    if isinstance(r, list): btc_price[str(r[0])[:10]] = float(r[1])
    elif isinstance(r, dict):
        d = str(r.get('date',''))[:10]
        v = r.get('close') or r.get('value') or r.get('price')
        if d and v: btc_price[d] = float(v)

print("Loading backfill scores (V2)...")
bs = json.load(open(f'{BASE}/backfill_scores.json', encoding='utf-8'))
v2_scores = {r['date']: r['v2_final'] for r in bs.get('series', [])}
print(f"  V2 available: {len(v2_scores)} dates  ({min(v2_scores)} → {max(v2_scores)})")

# ── 2. Batch WR computation ──────────────────────────────────────────────────
WINDOW_DAYS = 1095
MIN_PTS     = 30
NORM        = 0.289

_METRICS = {
    'nupl':       {'field': 'nupl',           'w': 0.32, 'inv': False},
    'mvrv':       {'field': 'mvrv',           'w': 0.24, 'inv': False},
    'puell':      {'field': 'puell',          'w': 0.14, 'inv': False},
    'cvdd_ratio': {'field': 'cvdd_ratio',     'w': 0.14, 'inv': False},
    'mayer':      {'field': 'mayer_multiple', 'w': 0.10, 'inv': False},
    'pi_gap':     {'field': 'pi_gap_pct',     'w': 0.06, 'inv': True},
}

# Build sorted arrays per metric for binary-search percentile
from bisect import bisect_right, insort

print("Computing WR for all dates (batch)...")
wr_scores = {}   # date → WR score
wr_cohs   = {}   # date → WR coherence

# sliding window per metric: keep sorted list of values
windows = {key: [] for key in _METRICS}  # sorted list of (date, value) in window
win_vals = {key: [] for key in _METRICS}  # sorted values only (for percentile)
win_dates= {key: [] for key in _METRICS}  # parallel dates list

for i, d in enumerate(sorted_dates):
    cutoff = (datetime.date.fromisoformat(d)
              - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    row = unified[d]

    # remove expired entries
    for key, cfg in _METRICS.items():
        while win_dates[key] and win_dates[key][0] < cutoff:
            old_d = win_dates[key].pop(0)
            old_v = windows[key].pop(0)
            idx = bisect_right(win_vals[key], old_v) - 1
            if idx >= 0 and win_vals[key][idx] == old_v:
                win_vals[key].pop(idx)
        # add current value
        v = row.get(cfg['field'])
        if v is not None:
            win_dates[key].append(d)
            windows[key].append(v)
            insort(win_vals[key], v)

    # compute oscillators for today
    oscs = {}
    for key, cfg in _METRICS.items():
        v = row.get(cfg['field'])
        if v is None or len(win_vals[key]) < MIN_PTS:
            continue
        rank = bisect_right(win_vals[key], v) / len(win_vals[key])
        osc  = (1.0 - rank) if cfg['inv'] else rank
        oscs[key] = osc

    if len(oscs) < 3:
        continue

    total_w = sum(_METRICS[k]['w'] for k in oscs)
    mean_v  = sum(_METRICS[k]['w'] * v for k, v in oscs.items()) / total_w
    var     = sum(_METRICS[k]['w'] * (v - mean_v)**2 for k, v in oscs.items()) / total_w
    coh     = max(0.0, 1.0 - math.sqrt(var) / NORM)
    score   = max(0, min(100, round(50.0 + 50.0 * (2*mean_v - 1) * coh)))

    wr_scores[d] = score
    wr_cohs[d]   = coh

print(f"  WR computed for {len(wr_scores)} dates")

# ── 3. Forward return → target ───────────────────────────────────────────────
FORWARD_DAYS = 180

def closest_price(date_str, offset_days):
    target = (datetime.date.fromisoformat(date_str)
              + datetime.timedelta(days=offset_days)).isoformat()
    for off in range(8):
        for sign in (0, 1, -1):
            cand = (datetime.date.fromisoformat(target)
                    + datetime.timedelta(days=off*sign)).isoformat()
            if cand in btc_price:
                return btc_price[cand]
    return None

def forward_return_to_target(fwd_ret_pct):
    """Map 6m forward return → risk target [0,100].
    +200% → target ≈ 0  (great buy, was low-risk correctly)
    -60%  → target ≈ 100 (was high-risk correctly)
    0%    → target ≈ 50
    """
    # sigmoid-like: target = 50 - clamp(fwd_ret, -100, 300) * 0.25
    return max(0.0, min(100.0, 50.0 - fwd_ret_pct * 0.25))

# Build aligned dataset: (date, V2, WR, target)
dataset = []
for d in sorted_dates:
    v2 = v2_scores.get(d)
    wr = wr_scores.get(d)
    if v2 is None or wr is None:
        continue
    p0 = btc_price.get(d) or closest_price(d, 0)
    pf = closest_price(d, FORWARD_DAYS)
    if p0 is None or pf is None or p0 == 0:
        continue
    fwd = (pf - p0) / p0 * 100
    tgt = forward_return_to_target(fwd)
    dataset.append((d, v2, wr, tgt, fwd))

print(f"\nAligned dataset: {len(dataset)} dates")

# ── 4. Optimize blend weight a (meta = a*V2 + (1-a)*WR) ─────────────────────
print("\nOptimizing blend weight a (step=0.01)...")
best_a, best_mse, best_corr = 0.0, float('inf'), -1.0
results = []

for a_int in range(0, 101):
    a = a_int / 100.0
    b = 1.0 - a
    mse  = 0.0
    n    = len(dataset)
    sx   = sy = sxx = syy = sxy = 0.0
    for _, v2, wr, tgt, _ in dataset:
        pred = a * v2 + b * wr
        mse += (pred - tgt) ** 2
        sx  += pred; sy += tgt; sxx += pred**2; syy += tgt**2; sxy += pred*tgt
    mse /= n
    # Pearson r
    denom = math.sqrt(max(0, (sxx - sx**2/n) * (syy - sy**2/n)))
    r     = (sxy - sx*sy/n) / denom if denom > 0 else 0.0
    results.append((a, mse, r))
    if mse < best_mse:
        best_mse, best_a, best_corr = mse, a, r

print(f"\n{'a (V2 weight)':>14s}  {'b (WR weight)':>13s}  {'MSE':>8s}  {'Pearson r':>9s}")
print("─" * 55)
for a, mse, r in results[::10]:
    marker = " ◄ OPTIMAL" if abs(a - best_a) < 0.015 else ""
    coh_marker = " ◄ CURRENT (coherence)" if abs(a - 0.358) < 0.015 else ""
    print(f"  {a:12.2f}   {1-a:13.2f}  {mse:8.1f}  {r:9.4f}{marker}{coh_marker}")

print(f"\nOptimal a={best_a:.2f} (V2) / b={1-best_a:.2f} (WR)  "
      f"MSE={best_mse:.1f}  r={best_corr:.4f}")
print(f"Current  a=0.36 (V2) / b=0.64 (WR)  "
      f"(coherence-based dynamic blend at today's values)")

# Show the optimal split
print("\n─── Optimal blend at key milestone dates ───")
KEY_DATES = {
    "2018-12-15": "Bear bottom $3k",
    "2021-04-14": "Spring ATH $63k",
    "2021-11-10": "Nov ATH $69k",
    "2022-11-21": "FTX bottom $15k",
    "2024-03-14": "2024 ATH $73k",
    "2025-01-20": "Jan 2025 ATH $109k",
    "2026-04-07": "Correction $71k",
}
print(f"\n{'Date':12s} {'Label':22s}  V2   WR   Coh-meta  ML-meta  Target")
print("─" * 75)
for d, lbl in KEY_DATES.items():
    row = next((r for r in dataset if r[0] == d), None)
    if row:
        _, v2, wr, tgt, fwd = row
        coh_meta = round(0.358 * v2 + 0.642 * wr)
        ml_meta  = round(best_a * v2 + (1-best_a) * wr)
        print(f"{d:12s} {lbl:22s}  {v2:3d}  {wr:3d}  {coh_meta:8d}  {ml_meta:7d}  "
              f"{tgt:6.0f}  (fwd={fwd:+.0f}%)")
