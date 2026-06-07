"""
Generate a short human-readable interpretation of the current index via Gemini.
Returns {'en': str, 'ua': str} or None if the API key is absent / call fails.
"""
import json
import os
import time

import requests

_MODEL = 'gemini-2.0-flash'
_URL = (
    f'https://generativelanguage.googleapis.com/v1beta/models/{_MODEL}'
    ':generateContent'
)

_METRIC_LABELS = {
    'nupl':             'NUPL',
    'mvrv_z_score':     'MVRV Z-score',
    'rhodl_ratio':      'RHODL Ratio',
    'cvdd_ratio':       'CVDD Ratio',
    'asopr':            'aSOPR',
    'cipherb':          'CipherB (momentum)',
    'mayer_multiple':   'Mayer Multiple',
    'etf_flows':        'ETF Flows 14d',
    'fear_greed':       'Fear & Greed',
    'yield_curve_spread': 'Yield Curve',
    'm2_yoy':           'M2 YoY',
}


def _metrics_text(slider_map: dict) -> str:
    lines = []
    for key, label in _METRIC_LABELS.items():
        val = slider_map.get(key)
        if val is not None:
            lines.append(f'  {label}: {val}/100')
    return '\n'.join(lines)


def generate_commentary(payload: dict, slider_map: dict) -> dict | None:
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print('GEMINI_API_KEY not set — skipping commentary')
        return None

    score = payload.get('final_score')
    oc    = payload.get('onchain_score')
    tech  = payload.get('tech_score')
    price = payload.get('btc_price')

    prompt = f"""You are a Bitcoin market analyst interpreting a composite risk index.
Score range 0–100: low = low risk (historically undervalued territory); high = high risk (historically expensive). NOT a price prediction.

Today's data:
- BTC price: ${price:,}
- Final Risk Score: {score}/100
- On-chain Score: {oc}/100  (NUPL, MVRV, RHODL, CVDD, aSOPR)
- Tech/Macro Score: {tech}/100  (CipherB momentum, Mayer Multiple, ETF flows, Fear & Greed, Yield Curve, M2)

Metric risk scores (0 = min risk, 100 = max risk):
{_metrics_text(slider_map)}

Metric context:
- NUPL / MVRV / RHODL / CVDD: on-chain valuation — measure where long-term holders stand (profit/loss depth, coin age distribution). Slow-moving, cycle-level signals.
- aSOPR: short-term spent output profit ratio — captures whether recent movers are selling at gain or loss.
- CipherB: price/momentum oscillator (WaveTrend). Fast, mean-reverting.
- Mayer Multiple: price vs 200-day MA — trend context.
- ETF Flows: 14-day institutional demand signal. Tactical.
- Fear & Greed: crowd sentiment. Contrarian.
- Yield Curve / M2: macro backdrop — liquidity and recession risk.

Your task — write 3–4 sentences that:
1. Identify whether on-chain and tech signals AGREE or DIVERGE, and what that divergence means.
2. Highlight any REINFORCING signals (multiple indicators pointing the same direction, amplifying the reading).
3. Highlight any CONTRADICTIONS (indicators pulling in opposite directions, creating ambiguity).
4. Describe the resulting market landscape in plain language — what kind of environment this combination of signals typically precedes.

Be specific. Reference actual metric scores. Do not give financial advice. Do not name the index itself.

Reply ONLY with valid JSON — no markdown, no code fences, no extra text:
{{"en": "English text.", "ua": "Ukrainian text."}}"""

    body = {
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {'temperature': 0.4, 'maxOutputTokens': 800},
    }
    last_exc = None
    for attempt in range(3):
        if attempt:
            time.sleep(2 ** attempt)  # 2s, 4s
        try:
            resp = requests.post(f'{_URL}?key={api_key}', json=body, timeout=30)
            resp.raise_for_status()
            raw = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            # Strip markdown code fences if the model adds them
            if raw.startswith('```'):
                raw = raw.split('```')[1]
                if raw.startswith('json'):
                    raw = raw[4:]
                raw = raw.strip()
            result = json.loads(raw)
            print(f"Gemini commentary (en): {result.get('en', '')[:100]}…")
            return result
        except Exception as exc:
            last_exc = exc
            print(f'Gemini attempt {attempt + 1} failed: {exc}')
    print(f'Gemini commentary failed after 3 attempts: {last_exc}')
    return None
