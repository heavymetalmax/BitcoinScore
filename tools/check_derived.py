#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scraper.mm_utils import get_bmp_trace

MVRV_URL = 'https://www.bitcoinmagazinepro.com/charts/mvrv-zscore/'
NUPL_URL  = 'https://www.bitcoinmagazinepro.com/charts/relative-unrealized-profit--loss/'
ADDR_URL  = 'https://www.bitcoinmagazinepro.com/charts/percent-addresses-in-loss/'

print('Fetching MVRV page for MC+RC...')
mc = get_bmp_trace(MVRV_URL, 'market cap')
rc = get_bmp_trace(MVRV_URL, 'realized cap')
print(f'MC={mc}  RC={rc}')
nupl_comp = round((mc - rc) / mc * 100, 4) if mc and rc else None
print(f'NUPL computed from MC/RC: {nupl_comp}%')

print('\nFetching NUPL page (direct)...')
nupl_direct = get_bmp_trace(NUPL_URL, 'nupl', multiply=100)
print(f'NUPL direct: {nupl_direct}%')
if nupl_comp and nupl_direct:
    print(f'Diff: {round(abs(nupl_comp - nupl_direct), 4)}%  Match: {abs(nupl_comp - nupl_direct) < 0.5}')

print('\nFetching addresses_in_loss...')
addr_frac = get_bmp_trace(ADDR_URL, 'addresses in loss')
if addr_frac is not None:
    loss_pct   = round(addr_frac * 100, 2)
    profit_pct = round((1 - addr_frac) * 100, 2)
    ratio      = round(profit_pct / loss_pct, 4) if loss_pct else None
    print(f'Loss:              {loss_pct}%')
    print(f'Profit (derived):  {profit_pct}%')
    print(f'P/L Ratio (derived): {ratio}')


MVRV_URL = 'https://www.bitcoinmagazinepro.com/charts/mvrv-zscore/'
NUPL_URL  = 'https://www.bitcoinmagazinepro.com/charts/relative-unrealized-profit--loss/'

mc = get_bmp_trace(MVRV_URL, 'market cap')
rc = get_bmp_trace(MVRV_URL, 'realized cap')

nupl_direct = get_bmp_trace(NUPL_URL, 'nupl', multiply=100)

if mc and rc:
    nupl_computed = round((mc - rc) / mc * 100, 4)
else:
    nupl_computed = None

print(f'Market Cap:      {mc}')
print(f'Realized Cap:    {rc}')
print(f'NUPL (computed): {nupl_computed}%')
print(f'NUPL (direct):   {nupl_direct}%')
print(f'Match: {nupl_computed is not None and nupl_direct is not None and abs(nupl_computed - nupl_direct) < 0.5}')

# Also derive profit/loss metrics from addresses_in_loss
ADDR_URL = 'https://www.bitcoinmagazinepro.com/charts/percent-addresses-in-loss/'
addr_loss_frac = get_bmp_trace(ADDR_URL, 'addresses in loss')
if addr_loss_frac is not None:
    loss_pct   = round(addr_loss_frac * 100, 4)
    profit_pct = round((1 - addr_loss_frac) * 100, 4)
    ratio      = round(profit_pct / loss_pct, 4) if loss_pct else None
    print(f'\nAddresses in Loss:   {loss_pct}%')
    print(f'Addresses in Profit: {profit_pct}%  (derived)')
    print(f'Profit/Loss Ratio:   {ratio}  (derived)')
