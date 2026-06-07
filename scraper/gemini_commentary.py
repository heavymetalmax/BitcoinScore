"""
Generate a short human-readable interpretation of the current index via Groq.
Returns {'en': str, 'ua': str} or None if the API key is absent / call fails.

Makes two separate API calls — one per language — for maximum reliability.
API key is stored in GEMINI_API_KEY secret (name kept for backward compatibility).
"""
import os
import time

import requests

_MODEL = 'llama-3.3-70b-versatile'
_URL = 'https://api.groq.com/openai/v1/chat/completions'

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


def _build_prompt(score, oc, tech, price, slider_map, language: str) -> str:
    lang_instruction = (
        'Write your response in English.'
        if language == 'en'
        else 'Напиши відповідь українською мовою.'
    )
    return f"""You are a Bitcoin market analyst interpreting a composite risk index.
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

{lang_instruction}
Write 3–4 sentences that:
1. Identify whether on-chain and tech signals AGREE or DIVERGE, and what that divergence means.
2. Highlight any REINFORCING signals (multiple indicators pointing the same direction, amplifying the reading).
3. Highlight any CONTRADICTIONS (indicators pulling in opposite directions, creating ambiguity).
4. Describe the resulting market landscape in plain language — what kind of environment this combination of signals typically precedes.

Be specific. Reference actual metric scores. Do not give financial advice. Do not name the index itself.
Reply with plain text only — no JSON, no markdown, no bullet points."""


def _call_groq(prompt: str, headers: dict) -> str | None:
    body = {
        'model': _MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.4,
        'max_tokens': 600,
    }
    last_exc = None
    for attempt in range(3):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            resp = requests.post(_URL, json=body, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content'].strip()
        except Exception as exc:
            last_exc = exc
            print(f'Groq attempt {attempt + 1} failed: {exc}')
    print(f'Groq call failed after 3 attempts: {last_exc}')
    return None


def generate_commentary(payload: dict, slider_map: dict) -> dict | None:
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print('GEMINI_API_KEY not set — skipping commentary')
        return None

    score = payload.get('final_score')
    oc    = payload.get('onchain_score')
    tech  = payload.get('tech_score')
    price = payload.get('btc_price')

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    en_text = _call_groq(_build_prompt(score, oc, tech, price, slider_map, 'en'), headers)
    if not en_text:
        return None
    print(f'Groq commentary (en): {en_text[:100]}…')

    ua_text = _call_groq(_build_prompt(score, oc, tech, price, slider_map, 'ua'), headers)
    if not ua_text:
        ua_text = en_text  # fallback to English if Ukrainian call fails
    print(f'Groq commentary (ua): {ua_text[:100]}…')

    return {'en': en_text, 'ua': ua_text}
