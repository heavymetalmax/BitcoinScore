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


def _risk_label(score):
    if score is None:
        return 'n/a'
    if score < 25:
        return 'low risk'
    if score < 45:
        return 'low-moderate risk'
    if score < 55:
        return 'moderate risk'
    if score < 75:
        return 'moderate-high risk'
    return 'high risk'


def _metrics_block(payload: dict, slider_map: dict) -> str:
    """Build a human-readable metrics block with raw values + risk labels."""
    m = payload.get('metrics', {})

    def mv(key):
        obj = m.get(key)
        if obj is None:
            return None
        if isinstance(obj, dict) and 'value' in obj:
            return obj['value']
        return obj

    lines = []

    nupl = payload.get('nupl')
    if nupl is not None:
        lines.append(f'  NUPL: {nupl:.2f}  [{_risk_label(slider_map.get("nupl"))}]'
                     '  (above 0 = unrealised profit; <0 = loss)')

    mvrv = payload.get('mvrv_z_score')
    if mvrv is not None:
        lines.append(f'  MVRV Z-score: {mvrv:.2f}  [{_risk_label(slider_map.get("mvrv_z_score"))}]'
                     '  (negative = below fair value)')

    rhodl = payload.get('rhodl_ratio')
    if rhodl is not None:
        lines.append(f'  RHODL Ratio: {rhodl:,.0f}  [{_risk_label(slider_map.get("rhodl_ratio"))}]')

    cvdd = payload.get('cvdd_ratio')
    if cvdd is not None:
        lines.append(f'  CVDD Ratio: {cvdd:.3f}  [{_risk_label(slider_map.get("cvdd_ratio"))}]'
                     '  (>1 = price above CVDD support)')

    asopr = payload.get('asopr')
    if asopr is not None:
        lines.append(f'  aSOPR: {asopr:.3f}  [{_risk_label(slider_map.get("asopr"))}]'
                     '  (<1 = short-term holders selling at a loss)')

    cb = mv('cipherb')
    if isinstance(cb, dict):
        ws = cb.get('weekly_score')
        if ws is not None:
            div = ' [bearish divergence]' if cb.get('fast_bearish_div') else \
                  ' [bullish divergence]' if cb.get('fast_bullish_div') else ''
            lines.append(f'  CipherB (WaveTrend): {ws:.1f}/100{div}  [{_risk_label(slider_map.get("cipherb"))}]')

    mm = mv('mayer_multiple')
    if isinstance(mm, dict):
        lines.append(f'  Mayer Multiple: {mm.get("value", "?")}  [{_risk_label(slider_map.get("mayer_multiple"))}]'
                     f'  (price vs 200-day MA; <1 = below MA)')

    etf = payload.get('etf_flows')
    if isinstance(etf, dict):
        lines.append(f'  ETF Flows 14d: ${etf.get("value", 0):,.0f}M  [{_risk_label(slider_map.get("etf_flows"))}]'
                     '  (negative = outflows)')

    fg = payload.get('fear_greed')
    if fg is not None:
        lines.append(f'  Fear & Greed (7d avg): {fg}  [{_risk_label(slider_map.get("fear_greed"))}]'
                     '  (0=extreme fear, 100=extreme greed)')

    yc = mv('yield_curve')
    if yc is not None:
        lines.append(f'  Yield Curve (10y-2y): {yc:.2f}%  [{_risk_label(slider_map.get("yield_curve_spread"))}]'
                     '  (negative = inverted/recession signal)')

    m2 = payload.get('m2_mom')
    if m2 is not None:
        lines.append(f'  M2 YoY growth: {m2:.1f}%  [{_risk_label(slider_map.get("m2_yoy"))}]')

    return '\n'.join(lines)


def _build_prompt(score, oc, tech, price, payload, slider_map, language: str) -> str:
    lang_instruction = (
        'Write your response in English.'
        if language == 'en'
        else 'Напиши відповідь українською мовою.'
    )
    return f"""You are a Bitcoin market analyst interpreting a composite risk index.
Score range 0–100: low = historically undervalued / low risk; high = historically expensive / high risk. NOT a price prediction.

Current overview:
- BTC price: ${price:,}
- Overall Risk Score: {score}/100
- On-chain sub-score: {oc}/100  (NUPL, MVRV Z-score, RHODL, CVDD, aSOPR)
- Tech/Macro sub-score: {tech}/100  (CipherB, Mayer Multiple, ETF Flows, Fear & Greed, Yield Curve, M2)

Individual metric readings:
{_metrics_block(payload, slider_map)}

{lang_instruction}
Write 3–4 sentences that:
1. State whether on-chain and tech/macro signals AGREE or DIVERGE, and what that means.
2. Name any REINFORCING signals (pointing the same direction, amplifying the conclusion).
3. Name any CONTRADICTING signals (pulling in opposite directions, creating ambiguity).
4. Describe the resulting market environment in plain language.

Rules:
- Use the Overall Risk Score (e.g. "{score}/100") and sub-scores when summarising.
- For individual metrics reference their RAW values (e.g. "aSOPR below 1", "Mayer Multiple 0.77") and the [risk label] given above — do NOT invent other numbers.
- Do not give financial advice. Do not name the index itself.
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

    en_text = _call_groq(_build_prompt(score, oc, tech, price, payload, slider_map, 'en'), headers)
    if not en_text:
        return None
    print(f'Groq commentary (en): {en_text[:100]}…')

    ua_text = _call_groq(_build_prompt(score, oc, tech, price, payload, slider_map, 'ua'), headers)
    if not ua_text:
        ua_text = en_text
    print(f'Groq commentary (ua): {ua_text[:100]}…')

    return {'en': en_text, 'ua': ua_text}
