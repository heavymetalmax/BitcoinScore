"""
Analysis: BTC price deviation from weekly MA (various lengths) at cycle milestones.
Data source: data/history/cipherb_btcusdt_1w.json (Kraken weekly OHLCV)
"""
import sys, json, datetime
sys.path.insert(0, '.')
from tools.backtest import parse_date, closest

# ── Load weekly closes ───────────────────────────────────────────────────────
with open('data/history/cipherb_btcusdt_1w.json') as f:
    raw = json.load(f)

candles = sorted(raw['series'], key=lambda c: c['timestamp'])
weekly_prices = []
for c in candles:
    d = datetime.datetime.utcfromtimestamp(c['timestamp']).date().isoformat()
    weekly_prices.append((d, c['close']))

print(f"Weekly candles available: {weekly_prices[0][0]} → {weekly_prices[-1][0]}  ({len(weekly_prices)} candles)\n")

# ── Compute moving averages ─────────────────────────────────────────────────
def compute_ma_series(prices, length):
    """Return [(date, ma_value), ...] starting from index length-1."""
    closes = [p[1] for p in prices]
    result = []
    for i in range(length - 1, len(prices)):
        window = closes[i - length + 1 : i + 1]
        result.append((prices[i][0], sum(window) / length))
    return result

ma50_series  = compute_ma_series(weekly_prices, 50)
ma100_series = compute_ma_series(weekly_prices, 100)
ma200_series = compute_ma_series(weekly_prices, 200)

# ── Milestones ───────────────────────────────────────────────────────────────
MILESTONES = [
    ('2018-12-15', 'Cycle bear bottom',  3200,  'BOTTOM'),
    ('2019-06-26', 'Local peak',        13880,  'PEAK'),
    ('2020-03-13', 'COVID crash',        3800,  'BOTTOM'),
    ('2020-10-01', 'Pre-bull start',    10800,  'NEUTRAL'),
    ('2021-04-14', 'Spring ATH',        63500,  'ATH'),
    ('2021-07-20', 'Summer dip',        29800,  'NEUTRAL'),
    ('2021-11-10', 'Nov 2021 ATH',      69000,  'ATH'),
    ('2022-06-18', 'Capitulation',      17600,  'BOTTOM'),
    ('2022-11-21', 'FTX bottom',        15500,  'BOTTOM'),
    ('2023-01-14', 'Recovery start',    21000,  'NEUTRAL'),
    ('2024-03-14', '2024 ATH',          73500,  'ATH'),
    ('2025-01-20', 'Jan 2025 peak',    109000,  'ATH'),
    ('2025-09-26', 'Close ATH',        123000,  'ATH'),
    ('2025-09-29', 'Intraday ATH',     129000,  'ATH'),
    ('2025-11-10', 'Post-ATH dump',     94000,  'NEUTRAL'),
    ('2026-04-27', 'Today',             77500,  'NEUTRAL'),
]

# ── Data coverage check ──────────────────────────────────────────────────────
# MA needs `length` candles before it's valid. Find first valid dates.
first_dates = {
    50:  weekly_prices[49][0],
    100: weekly_prices[99][0],
    200: weekly_prices[199][0] if len(weekly_prices) >= 200 else None,
}
print(f"Data coverage:")
for l, d in first_dates.items():
    print(f"  MA{l}w first valid: {d}")
print()

def dev(price, ma):
    if ma is None or ma == 0: return None
    return round((price / ma - 1) * 100, 1)

# Score mapping: log-inspired but simple linear with capped range
# MA50w calibration: bottoms cluster ~-50 to -60%, ATHs cluster +50 to +170%
# Use range -60% to +200% → linear. Outliers (2021 spring +167%) map to ~86, not 100.
# This avoids 2021-only calibration dominating.
def score_ma50w(d):
    if d is None: return None
    d = max(-60, min(200, d))
    return round((d + 60) / 260 * 100)

# ── Print table ──────────────────────────────────────────────────────────────
# Only use MA50w (valid from 2018-07) and MA100w (valid from 2019-07).
# MA200w is only valid from 2021-07 — shown but marked when extrapolated.
print(f"{'Date':<12} {'Label':<22} {'BTC':>8}  {'Type':<8}  "
      f"{'MA50w dev':>10} {'sc':>3}  {'MA100w dev':>11} {'sc':>3}  {'MA200w dev':>11} {'note':>6}")
print('─' * 110)

by_kind = {'BOTTOM': [], 'ATH': [], 'NEUTRAL': [], 'PEAK': []}

for date_str, label, btc, kind in MILESTONES:
    td = parse_date(date_str)

    # Only use MA value if the target date >= first valid date
    ma50_val  = closest(ma50_series,  td) if date_str >= first_dates[50]  else None
    ma100_val = closest(ma100_series, td) if date_str >= first_dates[100] else None
    ma200_val = closest(ma200_series, td) if (first_dates[200] and date_str >= first_dates[200]) else None

    d50  = dev(btc, ma50_val)
    d100 = dev(btc, ma100_val)
    d200 = dev(btc, ma200_val)

    s50  = score_ma50w(d50)

    def fmt(d, s=None):
        if d is None: return f"{'n/a':>8}", '  -'
        ds = f'{d:>+.0f}%'
        ss = f'{s:3d}' if s is not None else '   '
        return f'{ds:>8}', ss

    d50s,  s50s  = fmt(d50,  s50)
    d100s, s100s = fmt(d100)
    d200s, _     = fmt(d200)
    note = '' if d200 is not None else ('(extrap)' if date_str < (first_dates[200] or '9999') else '')

    marker = ' ◄' if kind == 'ATH' else (' ▼' if kind == 'BOTTOM' else '')
    print(f"{date_str:<12} {label:<22} ${btc:>7,}  {kind:<8}  "
          f"{d50s} {s50s}  {d100s} {s100s}  {d200s} {note}{marker}")

    if d50 is not None:
        by_kind[kind].append((date_str, d50, s50))

# ── Separation summary ───────────────────────────────────────────────────────
print()
print('── MA50w separation power (score range −60%→+200% linear) ──────────────')
for kind in ('BOTTOM', 'ATH', 'NEUTRAL'):
    items = by_kind[kind]
    if not items: continue
    devs   = [x[1] for x in items]
    scores = [x[2] for x in items if x[2] is not None]
    avg_d  = sum(devs) / len(devs)
    avg_s  = sum(scores) / len(scores) if scores else None
    print(f"  {kind:<8}: avg dev {avg_d:>+6.1f}%  avg score {avg_s:>5.1f}  "
          f"values: {[f'{d:+.0f}%' for d in devs]}")

bottoms = [x[1] for x in by_kind['BOTTOM']]
aths    = [x[1] for x in by_kind['ATH']]
if bottoms and aths:
    gap = sum(aths)/len(aths) - sum(bottoms)/len(bottoms)
    print(f"\n  Gap ATH−BOTTOM (raw dev): {gap:+.1f}pp")
    b_scores = [x[2] for x in by_kind['BOTTOM'] if x[2] is not None]
    a_scores = [x[2] for x in by_kind['ATH']    if x[2] is not None]
    print(f"  Gap ATH−BOTTOM (scores):  {sum(a_scores)/len(a_scores) - sum(b_scores)/len(b_scores):+.1f}pts")
    print(f"\n  → For reference: Real Yield gap = ~4pts, M2 gap = ~44pts, F&G gap = ~63pts")
