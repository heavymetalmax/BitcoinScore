"""
ML weight probe — honest feasibility check (NOT a production model).

Question: do the data "want" different weights than the hand-tuned ones,
and are data-driven weights *stable* given how little labeled history exists?

Approach:
  - 16 canonical cycle milestones (tops / bottoms) from tools/backtest_fast.py
  - Features assembled at each milestone from curated + local-history data:
      OC_avg (on-chain group score, curated), SMC, CipherB weekly_score,
      Fear&Greed, Global-M2 YoY
  - Target "risk" derived from the milestone's cycle role (top=high, bottom=low)
  - Ridge regression + leave-one-out CV; report weight STABILITY and skill
  - Univariate correlations (robust, overfit-proof signal of what carries info)

Run:  python3 tools/ml_weight_probe.py
"""
import json, datetime
import numpy as np

# ── 16 milestones: (date, label, btc, OC_avg, SMC) ───────────────────────────
MILESTONES = [
    ('2018-12-15','Cycle bear bottom',   3200,  18,  0),
    ('2019-06-26','Local peak',         13880,  69, 100),
    ('2020-03-13','COVID crash',         3800,  31,  0),
    ('2020-10-01','Pre-bull start',     10800,  52, 100),
    ('2021-04-14','Spring ATH',         63500,  91, 100),
    ('2021-07-20','Summer dip',         29800,  56,  52),
    ('2021-11-10','Nov 2021 ATH',       69000,  84, 100),
    ('2022-06-18','Capitulation',       17600,  29,   0),
    ('2022-11-21','FTX bottom',         15500,  22,   0),
    ('2023-01-14','Recovery start',     21000,  33,   0),
    ('2024-03-14','2024 ATH',           73500,  77, 100),
    ('2025-01-20','Jan 2025 peak',     109000,  73, 100),
    ('2025-09-26','Close ATH',         123000,  63, 100),
    ('2025-09-29','Intraday ATH',      129000,  65, 100),
    ('2025-11-10','Post-ATH dump',      94000,  60,  56),
    ('2026-04-27','Today',              77500,  47,   8),
]

# Target risk by cycle role (tops = high risk, bottoms = low risk).
# These are the canonical extremes — that is *why* they were chosen.
TARGET = {
    'Cycle bear bottom': 8, 'Local peak': 85, 'COVID crash': 10, 'Pre-bull start': 30,
    'Spring ATH': 92, 'Summer dip': 55, 'Nov 2021 ATH': 95, 'Capitulation': 8,
    'FTX bottom': 6, 'Recovery start': 28, '2024 ATH': 90, 'Jan 2025 peak': 88,
    'Close ATH': 90, 'Intraday ATH': 92, 'Post-ATH dump': 58,
    # 'Today' excluded from training (it is the current live point)
}

def parse(d): return datetime.date.fromisoformat(d)

def closest(series, target, keyd, keyv):
    """series: list of dicts; return value at date closest to target."""
    best, bd = None, 10**9
    for r in series:
        v = r.get(keyv)
        if v is None: continue
        ds = r.get(keyd)
        try:
            dt = parse(ds[:10]) if isinstance(ds, str) else datetime.date.fromtimestamp(ds)
        except Exception:
            continue
        dd = abs((dt - target).days)
        if dd < bd: bd, best = dd, v
    return best

# ── load local series ────────────────────────────────────────────────────────
cb = json.load(open('data/history/cipherb_btcusdt_1w.json'))['series']
fg = json.load(open('data/history/fear_greed.json'))['series']
m2 = json.load(open('data/history/global_m2.json'))['series']

def m2_yoy(target):
    """YoY % change of the global-M2 level around target date."""
    now  = closest(m2, target, 'date', 'value')
    prev = closest(m2, target - datetime.timedelta(days=365), 'date', 'value')
    if now is None or prev is None or prev == 0: return None
    return (now/prev - 1.0) * 100.0

# ── assemble matrix ──────────────────────────────────────────────────────────
FEATURES = ['OC_avg', 'SMC', 'CipherB', 'FearGreed', 'M2_YoY']
rows, y, labels = [], [], []
for (d, lab, btc, oc, smc) in MILESTONES:
    if lab not in TARGET:          # skip 'Today'
        continue
    td = parse(d)
    feat = [
        oc,
        smc,
        closest(cb, td, 'timestamp', 'weekly_score'),
        closest(fg, td, 'date', 'score'),
        m2_yoy(td),
    ]
    if any(v is None for v in feat):
        print(f"  ! skip {d} {lab}: missing {[FEATURES[i] for i,v in enumerate(feat) if v is None]}")
        continue
    rows.append(feat); y.append(TARGET[lab]); labels.append(lab)

X = np.array(rows, float); y = np.array(y, float)
n, p = X.shape
print(f"\nUsable labeled milestones: {n}   features: {p}  ({', '.join(FEATURES)})\n")

# standardize
mu, sd = X.mean(0), X.std(0); sd[sd == 0] = 1
Xs = (X - mu) / sd

# ── univariate correlations (robust signal of information content) ───────────
print("Univariate correlation with target risk (|r| → how much each carries):")
for i, f in enumerate(FEATURES):
    r = np.corrcoef(Xs[:, i], y)[0, 1]
    bar = '█' * int(abs(r)*20)
    print(f"  {f:10s} r = {r:+.2f}  {bar}")
print()

# ── ridge regression (closed form) ───────────────────────────────────────────
def ridge_fit(Xs, y, lam):
    Xb = np.hstack([np.ones((len(Xs),1)), Xs])
    A = Xb.T@Xb + lam*np.eye(Xb.shape[1]); A[0,0] -= lam  # don't penalize intercept
    w = np.linalg.solve(A, Xb.T@y)
    return w  # [intercept, coefs...]

LAM = 3.0  # heavy regularization given tiny n
w_full = ridge_fit(Xs, y, LAM)
print(f"Ridge (lambda={LAM}) standardized weights on full data:")
for f, c in zip(FEATURES, w_full[1:]):
    print(f"  {f:10s} {c:+.2f}")
print()

# ── leave-one-out CV: skill + weight STABILITY ───────────────────────────────
loo_pred, loo_w = [], []
for i in range(n):
    idx = [j for j in range(n) if j != i]
    w = ridge_fit(Xs[idx], y[idx], LAM)
    loo_w.append(w[1:])
    loo_pred.append((np.r_[1, Xs[i]] @ w))
loo_pred = np.array(loo_pred); loo_w = np.array(loo_w)

rmse_model = np.sqrt(np.mean((loo_pred - y)**2))
# baseline: OC_avg alone, rescaled by simple LOO linear fit
ocs = Xs[:, 0]; base_pred = []
for i in range(n):
    idx = [j for j in range(n) if j != i]
    b = np.polyfit(ocs[idx], y[idx], 1)
    base_pred.append(np.polyval(b, ocs[i]))
rmse_base = np.sqrt(np.mean((np.array(base_pred) - y)**2))
rmse_naive = np.sqrt(np.mean((y - y.mean())**2))

print("Leave-one-out skill (RMSE, lower = better):")
print(f"  predict-the-mean (naive) : {rmse_naive:5.1f}")
print(f"  OC_avg alone (baseline)  : {rmse_base:5.1f}")
print(f"  5-feature ridge          : {rmse_model:5.1f}")
print()

print("Weight STABILITY across LOO folds (mean ± std of standardized coef):")
for i, f in enumerate(FEATURES):
    m, s = loo_w[:, i].mean(), loo_w[:, i].std()
    flag = '  <-- unstable (|std| > |mean|)' if s > abs(m) else ''
    print(f"  {f:10s} {m:+.2f} ± {s:.2f}{flag}")

print("\n" + "─"*64)
print("READ-OUT")
print("─"*64)
improve = (rmse_base - rmse_model)
print(f"Ridge beats OC-alone by {improve:+.1f} RMSE points "
      f"({'meaningful' if improve > 3 else 'marginal'}).")
unstable = sum(loo_w[:, i].std() > abs(loo_w[:, i].mean()) for i in range(p))
print(f"{unstable}/{p} learned weights are unstable under leave-one-out.")
print("Interpretation: with only %d labeled cycle extremes, multivariate" % n)
print("weights are weakly identified. Univariate correlations are the")
print("trustworthy signal; full weight-learning needs the daily per-indicator")
print("matrix logged going forward (or backfilled), not this 16-point set.")
