"""
"At what BTC price does the index enter the Buy zone (<=20)?" solver.

The index is mostly a monotone function of price: drop the price and the
valuation metrics fall, so the score falls. We can therefore invert it, with
honest caveats, by re-running the REAL scoring pipeline (scraper.scoring.
compute_scores) under a hypothetical price and binary-searching for the price
at which final_score crosses a threshold.

What scales cleanly with an (instantaneous) price move, holding the slow
on-chain baselines (realized cap, supply, 200d MA) fixed at today's values:

  NUPL    = 1 - RC/MC.  With RC, supply fixed:  NUPL' = 1 - (1-NUPL)/k
  MVRV-Z  linear in market cap.  Implied sigma is pinned from today's
          (NUPL, Z) pair, giving  Z' = (k - (1-NUPL)) / (NUPL / Z)
  Mayer   = price / 200dMA.  Mayer' = price' / dma200
  CVDD    = price / slow floor.  CVDD' = CVDD * k

What we do NOT rescale (held at today's values in the "structural" run):
  RHODL, aSOPR        (on-chain, not a clean function of spot price level)
  Cipher B, Fear&Greed, ETF flows, yield curve, M2  (momentum/sentiment/macro)

Because a real crash ALSO collapses momentum (Cipher B is 40% of the Tech
group) and sentiment, the structural run is a conservative LOWER bound on the
trigger price. The "risk-off" run brackets the other side by also breaking
those. The real trigger price sits between, closer to the risk-off side.

    python tools/buyzone_price_solver.py [threshold]   # default 20
"""
import sys, os, copy, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scraper import scoring as S

DATA = 'web/data.json'


def rescale(metrics, k, price0, risk_off=False):
    """Return a metrics dict for hypothetical price = price0 * k."""
    m = copy.deepcopy(metrics)

    def setval(key, val):
        if isinstance(m.get(key), dict) and 'value' in m[key]:
            m[key]['value'] = val
        else:
            m[key] = val

    def getval(key):
        o = metrics.get(key)
        return o['value'] if isinstance(o, dict) and 'value' in o else o

    nupl0 = getval('nupl')              # NUPL in percent units (e.g. 19.23)
    z0    = getval('mvrv')              # MVRV Z-score
    cvdd0 = getval('cvdd_ratio')

    nf0 = nupl0 / 100.0                 # NUPL as a fraction
    # NUPL: 1 - RC/MC,  RC & supply held constant
    nf_new = 1.0 - (1.0 - nf0) / k
    setval('nupl', round(nf_new * 100.0, 4))

    # MVRV-Z: linear in MC; sigma pinned from today's (NUPL, Z)
    if z0 and nf0:
        z_new = (k - (1.0 - nf0)) / (nf0 / z0)
        setval('mvrv', round(z_new, 4))

    # CVDD ratio scales linearly with price (slow floor held constant)
    if cvdd0 is not None:
        setval('cvdd_ratio', round(cvdd0 * k, 4))

    # Mayer = price / 200dMA  (held constant), update both ratio and its score
    mm = m.get('mayer_multiple')
    mm = mm.get('value') if isinstance(mm, dict) and 'value' in mm else mm
    if isinstance(mm, dict) and mm.get('dma_200'):
        new_price = price0 * k
        ratio = new_price / mm['dma_200']
        mm['value'] = round(ratio, 4)
        rr = max(0.5, min(2.1, ratio))
        mm['score'] = round((rr - 0.5) / 1.6 * 100)
        mm['current_price'] = round(new_price, 2)

    if risk_off:
        # a real drawdown drags momentum + sentiment + profit-taking down WITH
        # price, modelled as a function of the drawdown dd (not forced extremes).
        dd = max(0.0, 1.0 - k)
        cb = m.get('cipherb')
        cb = cb.get('value') if isinstance(cb, dict) and 'value' in cb else cb
        if isinstance(cb, dict):
            f = max(0.0, 1.0 - dd / 0.35)          # WaveTrend oversold by ~-35%
            cb['weekly_score'] = (cb.get('weekly_score') or 0) * f
            cb['daily_score'] = (cb.get('daily_score') or 0) * f
            cb['fast_bearish_div'] = False
            cb['fast_bullish_div'] = False
        fg0 = 32.0
        fgv = metrics.get('fear_greed')
        if isinstance(fgv, dict):
            inner = fgv.get('value', fgv)
            if isinstance(inner, dict):
                fg0 = inner.get('avg_7d') or inner.get('latest') or fg0
        fg_new = max(8.0, fg0 - dd * 110.0)        # fear deepens with the drop
        fg = m.get('fear_greed')
        if isinstance(fg, dict) and 'value' in fg:
            fg['value'] = {'avg_7d': fg_new, 'latest': fg_new}
        else:
            m['fear_greed'] = fg_new
        asopr0 = getval('asopr') or 1.0
        setval('asopr', max(0.85, asopr0 - dd * 0.35))   # spent coins at a loss
    return m


def score_at(metrics, k, price0, risk_off):
    S._HIST_CACHE.clear()
    return S.compute_scores(rescale(metrics, k, price0, risk_off))['final_score']


def solve(metrics, price0, threshold, risk_off):
    # score is monotone increasing in k; find k where final_score == threshold
    lo, hi = 0.02, 1.0
    s_hi = score_at(metrics, hi, price0, risk_off)
    if s_hi <= threshold:
        return None, s_hi      # already at/below threshold at current price
    s_lo = score_at(metrics, lo, price0, risk_off)
    if s_lo > threshold:
        return 0.0, s_lo       # cannot reach threshold even near price 0
    for _ in range(60):
        mid = (lo + hi) / 2
        if score_at(metrics, mid, price0, risk_off) > threshold:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2, threshold


def main():
    thr = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    d = json.load(open(DATA, encoding='utf-8'))
    metrics = d['metrics']
    price0 = d['btc_price']
    cur = S.compute_scores(copy.deepcopy(metrics))['final_score']
    nupl0 = metrics['nupl']['value']
    realized = price0 * (1 - nupl0 / 100.0)

    print(f"\nCurrent BTC price : ${price0:,.0f}")
    print(f"Current index     : {cur}/100")
    print(f"Implied realized price (cost basis): ${realized:,.0f}")
    zone = 'Buy' if thr <= 20 else 'Accumulate' if thr <= 40 else f'<= {thr}'
    print(f"Target: index <= {thr} ({zone} zone)\n")
    print(f"{'scenario':<34}{'trigger price':>16}{'vs now':>10}")
    print('-' * 60)

    for label, ro in [("valuation only (momentum held)", False),
                      ("with typical capitulation", True)]:
        k, s = solve(metrics, price0, thr, ro)
        if k is None:
            print(f"{label:<34}{'already <= thr':>16}")
        elif k == 0.0:
            print(f"{label:<34}{'unreachable':>16}   (floors at ~{round(s)})")
        else:
            p = price0 * k
            print(f"{label:<34}{'$'+format(p,',.0f'):>16}{(k-1)*100:>9.0f}%")

    # show the metric breakdown at the capitulation trigger, for transparency
    k, _ = solve(metrics, price0, thr, True)
    if k and k > 0:
        S._HIST_CACHE.clear()
        sm = S.build_slider_map(rescale(metrics, k, price0, True))
        print(f"\nper-metric scores at the capitulation trigger (${price0*k:,.0f}):")
        for grp, keys in [("On-Chain", S.OC_WEIGHTS), ("Tech", S.TECH_WEIGHTS)]:
            parts = ', '.join(f"{x}={sm.get(x)}" for x in keys)
            print(f"  {grp:<9} {parts}")

    print("\nCaveats: holds realized cap, supply and the 200d MA at today's"
          " values, so it answers 'if price moved there soon'. The real Buy-zone"
          " trigger sits between the two rows, nearer the risk-off one, since a"
          " genuine drop drags momentum and sentiment down with it.")


if __name__ == '__main__':
    main()
