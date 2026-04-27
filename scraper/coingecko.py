import requests

COINGECKO_URL = 'https://api.coingecko.com/api/v3/simple/price'

def get_price():
    params = {'ids':'bitcoin','vs_currencies':'usd','include_24hr_change':'true'}
    r = requests.get(COINGECKO_URL, params=params, timeout=15)
    r.raise_for_status()
    j = r.json()
    if 'bitcoin' in j:
        return {'price': j['bitcoin']['usd'], 'change_24h': j['bitcoin'].get('usd_24h_change')}
    return None
