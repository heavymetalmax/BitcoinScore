"""
Generate a short human-readable interpretation of the current index via OpenAI o3-mini.
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
    return f"""Today's Bitcoin indicators and readings:
- BTC Price: ${price:,}
- Overall Risk Index Score: {score}/100 (where 0 is historically cheap/undervalued, 100 is historically expensive/overvalued)
- On-chain sub-score: {oc}/100
- Tech/Macro sub-score: {tech}/100

Detailed Metric Values:
{_metrics_block(payload, slider_map)}"""


def _call_openai(prompt: str, api_key: str) -> Optional[str]:
    url = 'https://api.openai.com/v1/chat/completions'
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    
    developer_instruction = (
        "You are a senior macroeconomic and on-chain analyst specializing in Bitcoin. "
        "Your task is to analyze the daily index score and metrics to output a concise 2-3 sentence market commentary in English. "
        "Synthesize the metrics using the following analytical framework:\n\n"
        
        "1. LONG-TERM STRUCTURAL VALUE (On-chain):\n"
        "   - Evaluate NUPL (>0.5 means high unrealized profit / potential top; <0 means unrealized loss / bottom).\n"
        "   - Evaluate MVRV Z-score (>4.0 indicates historical overvaluation; negative or near 0 indicates undervaluation).\n"
        "   - Check RHODL Ratio (high values show wealth shift to retail FOMO; low values show smart money accumulation).\n"
        "   - Check aSOPR (<1.0 shows retail panic selling at a loss / capitulation).\n\n"
        
        "2. SHORT-TERM SPECULATIVE PRESSURE & MOMENTUM (Technicals/Sentiment):\n"
        "   - Check Funding Rate (high positive rate >0.015% shows excessive long leverage with long-squeeze risk; flat or negative shows healthy or fearful positioning).\n"
        "   - Check Fear & Greed Index (>75 is extreme greed / local top warning; <25 is extreme fear / accumulation).\n"
        "   - Check CipherB weekly divergences (weekly bearish/bullish divergences are powerful momentum shift warnings).\n\n"
        
        "3. MACRO LIQUIDITY & INSTITUTIONAL DEMAND (Flows/Macro):\n"
        "   - Check ETF Flows (positive flows show institutional backing; sustained negative flows show cooling interest).\n"
        "   - Check M2 Money Supply YoY (rising growth fuels risk assets; flat or contracting growth acts as a macro drag).\n"
        "   - Check Yield Curve Spread (inverted spread <0% is a structural recession warning / macroeconomic headwind).\n\n"
        
        "ORCHESTRATION REGIMES to detect:\n"
        "   - Leverage Squeeze Risk: High Funding Rate + High Fear & Greed, but flat/negative ETF flows & flat M2 growth (dangerous speculation without capital backstop).\n"
        "   - Institutional Accumulation: High Fear & Greed / retail panic (aSOPR < 1), but strongly positive ETF flows and stable macro liquidity.\n"
        "   - Liquidity-Driven Run: Growing M2 + positive ETF flows + moderate funding rates (healthy sustainable run).\n"
        "   - Bear Market Bottom: MVRV Z-score near 0/negative + extreme fear + NUPL < 0 (high value accumulation).\n\n"
        
        "RESPONSE STRUCTURE:\n"
        "   - Sentence 1: Summarize the long-term structural valuation (on-chain metrics status).\n"
        "   - Sentence 2: Analyze the short-term speculative pressure (leverage/sentiment) and macro liquidity conditions.\n"
        "   - Sentence 3: State the net risk profile (e.g., potential long-squeeze warning, institutional support, or macro headwinds).\n\n"
        
        "STRICT RULES:\n"
        "   - Output exactly 2-3 sentences. Factual, professional, objective tone only.\n"
        "   - Do NOT suggest any actions, buy/sell recommendations, or investment advice.\n"
        "   - Do NOT use absolute words like 'safe', 'stable', 'secure', or 'guaranteed'.\n"
        "   - Do NOT invent or extrapolate numbers. Do NOT use markdown bolding, italics, or bullet points."
    )

    body = {
        'model': 'o3-mini',
        'messages': [
            {
                'role': 'developer',
                'content': developer_instruction
            },
            {
                'role': 'user',
                'content': prompt
            }
        ],
        'max_completion_tokens': 600,
    }
    last_exc = None
    for attempt in range(3):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=45)
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content'].strip()
        except Exception as exc:
            last_exc = exc
            print(f'OpenAI o3-mini attempt {attempt + 1} failed: {exc}')
    print(f'OpenAI o3-mini call failed after 3 attempts: {last_exc}')
    return None


def _translate_via_gemini(text: str, api_key: str) -> Optional[str]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    prompt = (
        "Translate the following Bitcoin market commentary into Ukrainian. "
        "Keep it factual and natural, matching the style, tone, and precise meaning of the original text exactly. "
        "Do not add any extra commentary, explanations, or introductory/concluding phrases:\n\n"
        f"{text}"
    )
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
