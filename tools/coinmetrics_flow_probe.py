"""
Coin Metrics market-wide money-flow probe (free Community API, no key).

Question (the user's "for science" test): does an OBJECTIVE, market-wide
money-flow signal add anything our index doesn't already have?

We cannot get true consolidated trade volume in crypto (no consolidated tape),
but Coin Metrics' Community tier gives two honest, multi-venue/on-chain proxies
with full history (2010-2011 -> today, daily):

  volume_reported_spot_usd_1d   aggregated reported spot volume across CM's
                                vetted exchanges (the "market volume" answer,
                                far better than a single Kraken feed)
  FlowInExNtv / FlowOutExNtv    BTC moving ONTO / OFF exchanges on-chain
                                (net-in = potential sell pressure, net-out =
                                 accumulation) -- manipulation-resistant

We build:
  1. A real volume-weighted MFI (TradingView-style, 14d) from PriceUSD + volume.
  2. A net-exchange-flow signal (net BTC to exchanges, normalized by supply).

Then we check both at canonical cycle tops/bottoms and correlate against:
  - our existing fixed signals' raw drivers (price, MVRV)
to see whether they carry NEW information or merely echo what we already score.

    python tools/coinmetrics_flow_probe.py
"""
import sys, json, datetime, statistics as st
import requests

API = 'https://community-api.coinmetrics.io/v4/timeseries/asset-metrics'
HDR = {'User-Agent': 'Mozilla/5.0'}
METRICS = ['PriceUSD', 'volume_reported_spot_usd_1d',
           'FlowInExNtv', 'FlowOutExNtv', 'SplyCur', 'CapMVRVCur']


def fetch(metrics, start='2011-01-01'):
    rows = {}
    params = {'assets': 'btc', 'metrics': ','.join(metrics),
              'frequency': '1d', 'page_size': 10000, 'start_time': start}
    url = API
    while True:
        r = requests.get(url, params=params, headers=HDR, timeout=60)
        r.raise_for_status()
        d = r.json()
        for rec in d.get('data', []):
            day = rec['time'][:10]
            rows[day] = rec
        nxt = d.get('next_page_url')
        if not nxt:
            break
        url, params = nxt, None
    return rows


def f(rec, k):
    v = rec.get(k)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def rma(vals, length):
    out = [None] * len(vals)
    prev = None
    for i, v in enumerate(vals):
        if v is None:
            continue
        if prev is None:
            window = [x for x in vals[max(0, i - length + 1):i + 1] if x is not None]
            if len(window) < length:
                continue
            prev = sum(window) / len(window)
            out[i] = prev
            continue
        prev = (prev * (length - 1) + v) / length
        out[i] = prev
    return out


def main():
    print("fetching Coin Metrics community data (full daily history)...")
    rows = fetch(METRICS)
    days = sorted(rows)
    print(f"got {len(days)} days: {days[0]} -> {days[-1]}\n")

    price = [f(rows[d], 'PriceUSD') for d in days]
    vol   = [f(rows[d], 'volume_reported_spot_usd_1d') for d in days]
    fin   = [f(rows[d], 'FlowInExNtv') for d in days]
    fout  = [f(rows[d], 'FlowOutExNtv') for d in days]
    sply  = [f(rows[d], 'SplyCur') for d in days]
    mvrv  = [f(rows[d], 'CapMVRVCur') for d in days]
    n = len(days)

    # ---- 1. Volume-weighted MFI (classic 14-period, daily) ----------------
    # raw money flow = price * volume; split into positive/negative by price move
    pos = [0.0] * n
    neg = [0.0] * n
    for i in range(1, n):
        if None in (price[i], price[i - 1], vol[i]):
            continue
        mf = price[i] * vol[i]
        if price[i] > price[i - 1]:
            pos[i] = mf
        elif price[i] < price[i - 1]:
            neg[i] = mf
    L = 14
    mfi = [None] * n
    for i in range(L, n):
        p = sum(pos[i - L + 1:i + 1])
        ng = sum(neg[i - L + 1:i + 1])
        if p + ng > 0:
            mfi[i] = 100.0 * p / (p + ng)

    # ---- 2. Net exchange flow (BTC), normalized by circulating supply ------
    # netflow>0 => coins moving ONTO exchanges (sell pressure / risk-on-top)
    netflow_bps = [None] * n  # net flow as basis points of supply per day
    for i in range(n):
        if None in (fin[i], fout[i], sply[i]) or sply[i] == 0:
            continue
        netflow_bps[i] = (fin[i] - fout[i]) / sply[i] * 1e4
    # smooth over 30d to get a regime signal
    nf_sm = rma(netflow_bps, 30)

    # ---- canonical cycle extremes ----------------------------------------
    EXTREMES = [
        ('TOP 2013', '2013-12-04'), ('BOTTOM 2015', '2015-01-14'),
        ('TOP 2017', '2017-12-17'), ('BOTTOM 2018', '2018-12-15'),
        ('TOP 2021a', '2021-04-14'), ('TOP 2021b', '2021-11-09'),
        ('BOTTOM 2022', '2022-11-21'), ('TOP 2024', '2024-03-14'),
        ('LATEST', days[-1]),
    ]
    idx = {d: i for i, d in enumerate(days)}

    def closest(target):
        td = datetime.date.fromisoformat(target)
        return min(range(n), key=lambda i: abs((datetime.date.fromisoformat(days[i]) - td).days))

    print(f"{'point':<13}{'date':<12}{'price':>11}{'MVRV':>7}{'MFI14':>8}{'netFlow30d':>12}")
    print('-' * 63)
    for lab, d in EXTREMES:
        i = closest(d)
        pr = price[i]; mv = mvrv[i]; mf = mfi[i]; nf = nf_sm[i]
        print(f"{lab:<13}{days[i]:<12}{(pr or 0):>11,.0f}{(mv or 0):>7.2f}"
              f"{('  n/a' if mf is None else f'{mf:>8.1f}')}"
              f"{('   n/a' if nf is None else f'{nf:>12.2f}')}")

    # ---- does it carry NEW info? correlate signals -------------------------
    def pearson(a, b):
        pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
        if len(pairs) < 50:
            return None
        xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
        mx = st.mean(xs); my = st.mean(ys)
        num = sum((x - mx) * (y - my) for x, y in pairs)
        dx = sum((x - mx) ** 2 for x in xs) ** 0.5
        dy = sum((y - my) ** 2 for y in ys) ** 0.5
        return num / (dx * dy) if dx and dy else None

    # forward 90d price return, to test predictive value
    fwd = [None] * n
    H = 90
    for i in range(n - H):
        if price[i] and price[i + H]:
            fwd[i] = (price[i + H] / price[i] - 1) * 100
    print("\ncorrelation with FORWARD 90d price return (predictive value):")
    print(f"  MVRV            r = {pearson(mvrv, fwd):+.3f}   (our existing on-chain driver)")
    print(f"  MFI14 (volume)  r = {pearson(mfi, fwd):+.3f}")
    print(f"  netFlow30d      r = {pearson(nf_sm, fwd):+.3f}")
    print("\ncorrelation of the new signals WITH MVRV (redundancy check):")
    print(f"  MFI14   vs MVRV  r = {pearson(mfi, mvrv):+.3f}")
    print(f"  netFlow vs MVRV  r = {pearson(nf_sm, mvrv):+.3f}")
    print("\n(high |r| with MVRV = redundant; near-0 with MVRV but non-0 with"
          " fwd-return = genuinely new, useful signal)")


if __name__ == '__main__':
    main()
