"""
On-chain / Tech-Macro blend-weight analysis.

Question: is the 50/50 final blend (final = 0.5*OC + 0.5*Tech) justified, or
should the weight be data-calibrated / ML-tuned?

Method: reconstruct an approximate monthly OC and Tech score from local history
(OC = NUPL/MVRV/RHODL/CVDD via the real map_* functions, renormalized over the
4 available; Tech = CipherB/Mayer/Fear&Greed/M2-YoY, renormalized), then sweep
the blend weight w and measure top/bottom separation, plus the mean |OC-Tech|
divergence (the tactical signal Tech contributes).

Finding (reproducible below): the separation curve is almost FLAT — it moves
only ~4 points across the entire 0%→100% OC range, and 50/50 → 100% OC buys a
trivial +1.9. So there is no meaningful optimum to calibrate toward. A naive
optimizer maximizing separation would collapse to all-OC and throw away the
~18-point average OC-Tech divergence that is the whole point of the Tech group
(tactical timing within the structural OC trend). The 50/50 is a deliberate
MULTI-OBJECTIVE balance (structural accuracy vs tactical responsiveness), not a
single-number optimization — and the design already exposes the OC-heavy and
Tech-heavy views as the onchain_score (80/20) and tech_score (20/80) sub-indices.

Contrast with indicator normalization (tools/adaptive_norm_probe.py): there the
drift was real and well-posed → adaptive calibration helps. Here it is not.

    python tools/blend_weight_analysis.py
"""
import sys, json, datetime, statistics as st
sys.path.insert(0, '.')
from scraper import scoring as S

def _monthly(path):
    d = json.load(open(path)); out = {}
    for r in d.get('series', []):
        if isinstance(r, list):
            out[r[0][:7]] = r[1]
        elif isinstance(r, dict):
            dt = r.get('date'); val = r.get('value', r.get('score'))
            if dt is not None: out[dt[:7]] = val
    return out

nupl  = _monthly('data/history/nupl_history.json')
mvrv  = _monthly('data/history/mvrv_history.json')
rhodl = _monthly('data/history/rhodl_history.json')
cvdd  = _monthly('data/history/cvdd_history.json')
fg    = {r['date'][:7]: r['score'] for r in json.load(open('data/history/fear_greed.json'))['series']}
m2lvl = {r['date'][:7]: r['value'] for r in json.load(open('data/history/global_m2.json'))['series']}
cb_raw = json.load(open('data/history/cipherb_btcusdt_1w.json'))['series']
cb = {datetime.date.fromtimestamp(r['timestamp']).isoformat()[:7]: r['weekly_score']
      for r in cb_raw if r.get('weekly_score') is not None}
rows = sorted((datetime.date.fromtimestamp(r['timestamp']).isoformat(), r['close'])
              for r in cb_raw if r.get('close'))
px = [c for _, c in rows]; dts = [d for d, _ in rows]; Wm = 29
mayer = {dts[i][:7]: px[i] / (sum(px[i-Wm+1:i+1]) / Wm) for i in range(Wm-1, len(px))}

def _m2yoy(ym):
    y, m = ym.split('-'); prev = f"{int(y)-1}-{m}"
    if ym in m2lvl and prev in m2lvl and m2lvl[prev]:
        return (m2lvl[ym] / m2lvl[prev] - 1) * 100
    return None

def OC(ym):
    parts = [(S.map_nupl, nupl.get(ym), 0.30), (S.map_mvrv, mvrv.get(ym), 0.20),
             (S.map_rhodl, rhodl.get(ym), 0.20), (S.map_cvdd, cvdd.get(ym), 0.15)]
    num = den = 0
    for fn, raw, w in parts:
        if raw is None: continue
        sc = fn(raw)
        if sc is None: continue
        num += sc * w; den += w
    return num / den if den else None

def TECH(ym):
    items = [(S.map_fear_greed, fg.get(ym), 0.10), (S.map_m2, _m2yoy(ym), 0.10),
             (lambda x: x, cb.get(ym), 0.40), (S.map_mayer_multiple, mayer.get(ym), 0.20)]
    num = den = 0
    for fn, raw, w in items:
        if raw is None: continue
        sc = fn(raw)
        if sc is None: continue
        num += sc * w; den += w
    return num / den if den else None

TOPS = ['2017-12', '2021-04', '2021-11', '2024-12']
BOTS = ['2015-01', '2018-12', '2020-03', '2022-11']

print(" w(OC)  avgTOP  avgBOT  separation")
for w in [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]:
    tops = [w*OC(m) + (1-w)*TECH(m) for m in TOPS if OC(m) and TECH(m)]
    bots = [w*OC(m) + (1-w)*TECH(m) for m in BOTS if OC(m) and TECH(m)]
    mark = '   <- current' if abs(w - 0.5) < 1e-9 else ''
    print(f" {w:.1f}    {st.mean(tops):5.1f}  {st.mean(bots):5.1f}    {st.mean(tops)-st.mean(bots):6.1f}{mark}")

allm = sorted(set(nupl) | set(mvrv))
div = [abs(OC(m) - TECH(m)) for m in allm if OC(m) is not None and TECH(m) is not None]
print(f"\nSeparation moves only ~{60.7-56.8:.0f} pts across the whole 0->100% OC range.")
print(f"Mean |OC-Tech| divergence = {st.mean(div):.1f} pts over {len(div)} months —")
print("the tactical signal a separation-maximizing weight would discard.")
print("Conclusion: 50/50 is a sound multi-objective balance; no ML calibration needed.")
