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
    print(f'OpenAI: initiating call, key_prefix={api_key[:8]}…, prompt_len={len(prompt)}', flush=True)
    
    developer_instruction = (
        "You are a senior Bitcoin macro and on-chain analyst. "
        "Given the daily BTCBRI data snapshot, write a 3-sentence market commentary in English.\n\n"

        "BTCBRI CONTEXT:\n"
        "The index runs 0–100 (0 = deep capitulation, 100 = blow-off top). "
        "Historical cycle bottoms average ~19/100; confirmed tops average ~86/100. "
        "Peak scores are compressing each cycle (2021: 91→86, 2024: 88, 2025: 74) — "
        "the sell zone now begins near 65, not 80.\n\n"

        "METRIC THRESHOLDS (use as evidence, not as a list to read out):\n"
        "  NUPL: >0.75 euphoria; 0.25–0.50 optimism; <0 capitulation.\n"
        "  MVRV Z: >3.5 elevated (mature market); <0.5 historically cheap.\n"
        "  aSOPR: <1.0 short-term holders selling at a loss (capitulation signal).\n"
        "  CipherB: score >75 overbought; <35 oversold. "
        "    fast_bearish_div adds 12 risk pts and is an early-warning top signal.\n"
        "  Mayer Multiple: <0.8 below 200-day MA (price weakness); >1.5 stretched.\n"
        "  ETF Flows 14d: negative = sustained institutional exit; "
        "    reversal from negative to positive is a leading bullish signal.\n"
        "  Fear & Greed: <25 extreme fear (historically precedes recoveries); "
        "    >75 extreme greed (local top warning).\n"
        "  Yield Curve: re-steepening after prolonged inversion is more dangerous "
        "    than the inversion itself (recession has arrived).\n"
        "  M2 YoY: high expansion = liquidity tailwind; "
        "    contraction effects hit crypto 6–12 months after peak.\n\n"

        "ACTIVE REGIME — identify ONE that best fits:\n"
        "  LEVERAGE SQUEEZE: high funding + high F&G + ETF outflows + flat M2.\n"
        "  INSTITUTIONAL ACCUMULATION: retail fear (aSOPR<1, F&G<35) + ETF inflows.\n"
        "  LIQUIDITY RUN: M2 accelerating + ETF positive + moderate funding.\n"
        "  MACRO HEADWIND: M2 decelerating + yield curve re-steepening + ETF outflows.\n"
        "  BEAR BOTTOM: MVRV Z<0.5 + NUPL<0 + extreme fear + aSOPR<0.97.\n"
        "  DISTRIBUTION: NUPL>0.60 + high RHODL + CipherB bearish div + MM>1.5.\n"
        "  DIVERGENCE TRAP: final score 35–55 but 2+ individual metrics at extremes.\n\n"

        "SENTENCE STRUCTURE:\n"
        "  S1: State the structural on-chain positioning in one concrete sentence — "
        "    use 1–2 metric values as evidence, not a list. "
        "    Lead with the most informative signal, not the composite score.\n"
        "  S2: Describe the market dynamics (momentum + sentiment + flows + macro) "
        "    and name the active regime. "
        "    If on-chain and tech/macro sub-scores diverge by >15 pts, note the tension.\n"
        "  S3: State the net risk picture. "
        "    If any hidden risk pattern is present "
        "    (CipherB bearish div in moderate zone, ETF/price decoupling, "
        "    adaptive score gap >12 pts, re-steepening yield curve), name it explicitly.\n"
        "  S4: Assess the TREND CONTINUATION probability for the current index direction. "
        "    Is the present trend (rising/falling/flat score) likely to extend, stall, or reverse?\n"
        "    Base it on: momentum alignment across OC and TM groups, "
        "    whether the regime supports continuation, and any divergence signals.\n"
        "    Use qualitative language: 'high/moderate/low probability of continuation' "
        "    and name the one factor most likely to confirm or invalidate the trend.\n\n"

        "STRICT RULES:\n"
        "  - Exactly 4 sentences. No bullet points, headers, or markdown.\n"
        "  - Use metric values as evidence; do NOT enumerate every metric.\n"
        "  - Do NOT suggest buy/sell actions or investment advice.\n"
        "  - Do NOT use: safe, stable, secure, guaranteed, certain.\n"
        "  - Do NOT invent numbers. Professional, factual, direct tone."
    )

    body = {
        'model': 'o3-mini',
        'reasoning_effort': 'low',
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
        'max_completion_tokens': 5000,
    }
    last_exc = None
    for attempt in range(3):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=45)
            print(f'OpenAI: attempt {attempt+1} status={resp.status_code}', flush=True)
            resp.raise_for_status()
            data = resp.json()
            choice = data['choices'][0]
            content = choice['message'].get('content')
            print(f'OpenAI: finish_reason={choice.get("finish_reason")}, content_len={len(content) if content else 0}', flush=True)
            if content:
                return content.strip()
            # content is None or empty (e.g. refusal / content_filter)
            print(f'OpenAI: empty content — choice={choice}', flush=True)
            return None
        except Exception as exc:
            last_exc = exc
            print(f'OpenAI o3-mini attempt {attempt + 1} failed: {exc}', flush=True)
    print(f'OpenAI o3-mini call failed after 3 attempts: {last_exc}', flush=True)
    return None


def _translate_via_openai(text: str, api_key: str) -> Optional[str]:
    url = 'https://api.openai.com/v1/chat/completions'
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    body = {
        'model': 'gpt-4o-mini',
        'messages': [
            {
                'role': 'system',
                'content': (
                    'You are a professional Ukrainian translator. '
                    'Translate the given Bitcoin market commentary into Ukrainian exactly — '
                    'preserve tone, style, and all specific numeric values. '
                    'Output only the translated text, no extra words.'
                ),
            },
            {'role': 'user', 'content': text},
        ],
        'temperature': 0.2,
        'max_tokens': 1000,
    }
    last_exc = None
    for attempt in range(3):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            if not resp.ok:
                print(f'OpenAI translation attempt {attempt + 1} failed: {resp.status_code} — {resp.text[:200]}', flush=True)
                last_exc = resp.text
                continue
            translated = resp.json()['choices'][0]['message']['content'].strip()
            return translated
        except Exception as exc:
            last_exc = exc
            print(f'OpenAI translation attempt {attempt + 1} failed: {exc}', flush=True)
    print(f'OpenAI translation failed after 3 attempts: {last_exc}', flush=True)
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

    ua_text = _translate_via_openai(en_text, openai_key)

    if not ua_text:
        ua_text = en_text
        print('OpenAI translation unavailable — using English fallback for UA', flush=True)
    else:
        print(f'OpenAI translation (ua): {ua_text[:100]}…', flush=True)

    return {'en': en_text, 'ua': ua_text}
