"""
Generate a short human-readable interpretation of the current index via OpenAI GPT.
Translates it to Ukrainian via Google Gemini API.
Returns {'en': str, 'ua': str} or None if the API keys are absent / call fails.
"""
import os
import time
from typing import Dict, Any, Optional

import requests


def _risk_label(score: Optional[int]) -> str:
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
    """Build a human-readable metrics block with raw values + risk labels.

    For NUPL, MVRV, and Mayer (adaptive metrics) we use the 'fixed' pre-blend
    score from adaptive_calibration — that is the value the dashboard displays.
    """
    m = payload.get('metrics', {})
    ac = payload.get('adaptive_calibration', {})

    def mv(key):
        obj = m.get(key)
        if obj is None:
            return None
        if isinstance(obj, dict) and 'value' in obj:
            return obj['value']
        return obj

    def fixed_or_slider(ac_key, slider_key):
        """Return fixed (dashboard-displayed) score for adaptive metrics."""
        entry = ac.get(ac_key)
        if isinstance(entry, dict) and entry.get('fixed') is not None:
            return entry['fixed']
        return slider_map.get(slider_key)

    lines = []

    nupl = payload.get('nupl')
    if nupl is not None:
        lines.append(f'  NUPL: {nupl:.2f}  [{_risk_label(fixed_or_slider("nupl", "nupl"))}]'
                     '  (above 0 = unrealised profit; <0 = loss)')

    mvrv = payload.get('mvrv_z_score')
    if mvrv is not None:
        lines.append(f'  MVRV Z-score: {mvrv:.2f}  [{_risk_label(fixed_or_slider("mvrv", "mvrv_z_score"))}]'
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
        lines.append(f'  Mayer Multiple: {mm.get("value", "?")}  [{_risk_label(fixed_or_slider("mayer", "mayer_multiple"))}]'
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


def _build_prompt(score: int, oc: int, tech: int, price: float, payload: dict, slider_map: dict) -> str:
    return f"""You are a Bitcoin data analyst writing a factual summary of today's market indicator readings.

IMPORTANT DEFINITIONS:
- The Risk Score (0–100) measures how HISTORICALLY EXPENSIVE Bitcoin is based on on-chain and macro data. Low score = historically cheap territory. High score = historically expensive. It does NOT measure momentum, trend, or whether price will go up or down.
- On-chain metrics (NUPL, MVRV, RHODL, CVDD, aSOPR) reflect where long-term holders stand — they move slowly and lag price action.
- Tech/Macro metrics (CipherB, Mayer Multiple, ETF Flows, Fear & Greed, Yield Curve, M2) reflect current momentum, sentiment, and macro conditions.

Today's readings:
- BTC price: ${price:,}
- Overall Risk Score: {score}/100
- On-chain sub-score: {oc}/100
- Tech/Macro sub-score: {tech}/100

{_metrics_block(payload, slider_map)}

Write your response in English.
Write exactly 2–3 sentences. Describe:
1. What the on-chain picture shows (are long-term holders in profit or stress? Is the market historically cheap or expensive by these metrics?)
2. What the tech/macro picture shows (sentiment, momentum, institutional flows — what is the current dynamic?)
3. If on-chain and tech/macro tell different stories, note the divergence plainly.

STRICT RULES — violation means failure:
- Do NOT say the market is "safe", "stable", "good for investors", or recommend any action.
- Do NOT say a low risk score means "safe" — it means historically undervalued, not direction.
- Do NOT invent numbers. Use only the values given above.
- Plain factual sentences only — no bullet points, no markdown, no conclusion about what to do."""


def _call_openai(prompt: str, api_key: str) -> Optional[str]:
    url = 'https://api.openai.com/v1/chat/completions'
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    body = {
        'model': 'gpt-4o-mini',
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.3,
        'max_tokens': 300,
    }
    last_exc = None
    for attempt in range(3):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content'].strip()
        except Exception as exc:
            last_exc = exc
            print(f'OpenAI attempt {attempt + 1} failed: {exc}')
    print(f'OpenAI call failed after 3 attempts: {last_exc}')
    return None


def _translate_via_gemini(text: str, api_key: str) -> Optional[str]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    prompt = f"Translate the following Bitcoin market commentary into Ukrainian. Keep it factual and natural, matching the style and meaning of the original text exactly. Do not add any extra commentary or explanations:\n\n{text}"
    body = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": 0.2
        }
    }
    last_exc = None
    for attempt in range(3):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            resp.raise_for_status()
            res_json = resp.json()
            translated = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
            return translated
        except Exception as exc:
            last_exc = exc
            print(f'Gemini translation attempt {attempt + 1} failed: {exc}')
    print(f'Gemini translation failed after 3 attempts: {last_exc}')
    return None


def generate_commentary(payload: dict, slider_map: dict) -> Optional[dict]:
    openai_key = os.environ.get('OPENAI_API_KEY')
    if not openai_key:
        print('OPENAI_API_KEY not set — skipping commentary')
        return None

    score = payload.get('final_score')
    oc    = payload.get('onchain_score')
    tech  = payload.get('tech_score')
    price = payload.get('btc_price')

    prompt = _build_prompt(score, oc, tech, price, payload, slider_map)
    en_text = _call_openai(prompt, openai_key)
    if not en_text:
        return None
    print(f'OpenAI commentary (en): {en_text[:100]}…')

    gemini_key = os.environ.get('GEMINI_API_KEY')
    ua_text = None
    if gemini_key:
        ua_text = _translate_via_gemini(en_text, gemini_key)
    
    if not ua_text:
        ua_text = en_text
        print('Gemini translation unavailable — using English fallback for UA')
    else:
        print(f'Gemini commentary (ua): {ua_text[:100]}…')

    return {'en': en_text, 'ua': ua_text}
