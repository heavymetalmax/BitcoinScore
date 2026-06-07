"""
Generate a short human-readable interpretation of the current index via Gemini.
Returns {'en': str, 'ua': str} or None if the API key is absent / call fails.
"""
import json
import os

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

    prompt = f"""You are interpreting a Bitcoin risk index called the Bitcoin Buy Risk Index.
Score range 0–100: low score = low risk / historically good time to accumulate; high score = high risk / historically expensive territory. This is NOT a price prediction.

Today's snapshot:
- BTC price: ${price:,}
- Final Risk Score: {score}/100
- On-chain Score: {oc}/100
- Tech / Macro Score: {tech}/100

Individual metric risk scores (0 = minimal risk, 100 = maximum risk):
{_metrics_text(slider_map)}

Write a concise 2–3 sentence interpretation. Name the 1–2 most significant drivers. Do NOT give financial advice. Do NOT name the index.

Reply ONLY with valid JSON — no markdown, no code fences, no extra text:
{{"en": "English text.", "ua": "Ukrainian text."}}"""

    try:
        resp = requests.post(
            f'{_URL}?key={api_key}',
            json={
                'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {'temperature': 0.3, 'maxOutputTokens': 512},
            },
            timeout=30,
        )
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
        print(f'Gemini commentary failed: {exc}')
        return None
