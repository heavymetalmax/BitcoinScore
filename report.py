import json, math
from scraper.scoring import compute_scores, OC_WEIGHTS, TECH_WEIGHTS

with open('data/data.json') as f:
    d = json.load(f)

def map_nupl(v):
    if v is None: return 50
    v = max(-50, min(100, v))
    return round(((v + 50) / 150) * 100)

def map_mvrv(v):
    if v is None: return 50
    v = max(-2, min(10, v))
    return round(((v + 2) / 12) * 100)

def map_profit(v):
    if v is None: return 50
    # SOPR (adjusted): -0.05=capitulation (slider 0/bullish) … +0.10=distribution (slider 100/bearish)
    v = max(-0.05, min(0.10, v))
    return round((v + 0.05) / 0.15 * 100)

def map_addr_profit(v):
    if v is None: return 50
    # addresses_in_profit: 0% (all in loss) → slider 0, 100% (all in profit) → slider 100
    return round(max(0, min(100, v)))

def map_fear_greed(v):
    if v is None: return 50
    return round(max(0, min(100, v)))

def map_m2(v):
    if v is None: return 50
    v = max(-2, min(6, v))
    return round(((v + 2) / 8) * 100)

def map_dxy(v):
    if v is None: return 50
    v = max(80, min(160, v))
    return round(((160 - v) / 80) * 100)

def map_georisk(v):
    # 0 = absolute stability (100/100 bullish), ~350 = extreme risk (0/100 bearish)
    if v is None: return 50
    v = max(0, min(350, v))
    return round(((350 - v) / 350) * 100)

def map_cvdd(v):
    if v is None: return 50
    v = max(1, min(5, v))
    return round(((v - 1) / 4) * 100)

def map_rhodl(v):
    if v is None: return 50
    v = max(100, min(100000, v))
    return round((math.log10(v) - math.log10(100)) / (math.log10(100000) - math.log10(100)) * 100)

m = d.get('metrics', {})
def mv(key): return m.get(key, {}).get('value')

nupl        = mv('nupl')
mvrv        = mv('mvrv')
sopr        = mv('sopr')
addr_profit = mv('addresses_in_profit')
fear_greed  = mv('fear_greed')
m2          = mv('m2')
dxy_raw     = mv('dxy')
georisk_raw = mv('geopolitical_risk')
cvdd        = mv('cvdd_ratio')
rhodl       = mv('rhodl_ratio')
rainbow     = mv('rainbow_band')
cipherb     = mv('cipherb')
smc_val     = mv('smc')

dxy_val     = dxy_raw[0] if isinstance(dxy_raw, (list, tuple)) else (dxy_raw.get('current') if isinstance(dxy_raw, dict) else dxy_raw)
georisk_val = georisk_raw[0] if isinstance(georisk_raw, (list, tuple)) else (georisk_raw.get('current') if isinstance(georisk_raw, dict) else georisk_raw)

rows = [
    ('nupl',              f'{nupl}%' if nupl is not None else 'null',                                         map_nupl(nupl)),
    ('mvrv_z_score',      f'{mvrv}' if mvrv is not None else 'null',                                         map_mvrv(mvrv)),
    ('sopr',              f'sopr={sopr:.5f}' if sopr is not None else 'null',                                map_profit(sopr)),
    ('addresses_in_profit', f'{addr_profit:.1f}%' if addr_profit is not None else 'null',               map_addr_profit(addr_profit)),
    ('fear_greed',        f'{fear_greed} ({d.get("fear_greed_label","")})' if fear_greed is not None else 'null', map_fear_greed(fear_greed)),
    ('m2_mom',            f'{m2}%' if m2 is not None else 'null',                                            map_m2(m2)),
    ('dxy',               f'{dxy_val:.4f}' if dxy_val is not None else 'null',                              map_dxy(dxy_val)),
    ('geopolitical_risk', f'{georisk_val:.2f}' if georisk_val is not None else 'null',                       map_georisk(georisk_val)),
    ('cvdd_ratio',        f'{cvdd}' if cvdd is not None else 'null',                                         map_cvdd(cvdd)),
    ('rhodl_ratio',       f'{rhodl:.2f}' if rhodl is not None else 'null',                                   map_rhodl(rhodl)),
    ('cipherb',           f'wt1={cipherb["wt1"]:.1f}  score={cipherb["weekly_score"]:.1f}' if isinstance(cipherb, dict) else 'null',
                          round(cipherb['weekly_score']) if isinstance(cipherb, dict) and cipherb.get('weekly_score') is not None else 50),
    ('smc',               f'support={smc_val["swing_low"]}  resist={smc_val["swing_high"]}  pos={smc_val["position"]:.1f}%' if isinstance(smc_val, dict) and smc_val.get('position') is not None else 'null',
                          round(smc_val['position']) if isinstance(smc_val, dict) and smc_val.get('position') is not None else 50),
]

# ── Decision matrix ─────────────────────────────────────────────────────────
scores   = compute_scores(m)
oc_avg   = scores['onchain_avg']
tech_avg = scores['tech_avg']
onchain_score = scores['onchain_score']
tech_score    = scores['tech_score']
final_score   = scores['final_score']

print(f"\n{'Metric':<22} {'Value':<30} {'Slider (0-100)'}")
print('-' * 65)
for name, val, slider in rows:
    print(f'{name:<22} {val:<30} {slider}')

print(f'\n{"─"*65}')
print(f'{"GROUP":22} {"score":>14}   {"weight breakdown"}')
print(f'{"─"*65}')
print(f'{"on-chain avg":<22} {oc_avg if oc_avg is not None else "n/a":>14}   nupl×25 mvrv×25 cvdd×18 rhodl×17 sopr×10 addr×5')
print(f'{"tech/macro avg":<22} {tech_avg if tech_avg is not None else "n/a":>14}   cipherb×35 smc×20 m2×18 fg×15 dxy×8 gpr×4')
print(f'{"─"*65}')
print(f'{"INDEX 1 (OC 60%)":<22} {onchain_score if onchain_score is not None else "n/a":>14}   60% on-chain + 40% tech')
print(f'{"INDEX 2 (Tech 60%)":<22} {tech_score if tech_score is not None else "n/a":>14}   40% on-chain + 60% tech')
print(f'{"FINAL SCORE":<22} {final_score if final_score is not None else "n/a":>14}   50% on-chain + 50% tech')

if isinstance(rainbow, dict):
    print(f'\nrainbow_band details:')
    print(f'  band:       {rainbow["band"]}')
    print(f'  color:      {rainbow["color"]}')
    print(f'  price:      {rainbow["price"]}')
    print(f'  band_upper: {rainbow["band_upper"]}')
    print(f'  date:       {rainbow["date"]}')
