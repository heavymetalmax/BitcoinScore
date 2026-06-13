"""
Generate a structured Bitcoin market commentary via OpenAI o3-mini.
Returns {'en': {'summary': str, 'analysis': str}, 'ua': {'summary': str, 'analysis': str}}
or None if the API key is absent or calls fail.
"""
import json
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
    """Build a human-readable metrics block with raw values + risk labels."""
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
        entry = ac.get(ac_key)
        if isinstance(entry, dict) and entry.get('fixed') is not None:
            return entry['fixed']
        return slider_map.get(slider_key)

    def adaptive_gap(ac_key):
        """Return (fixed, adaptive) tuple if gap >12 pts, else None."""
        entry = ac.get(ac_key)
        if not isinstance(entry, dict):
            return None
        f, a = entry.get('fixed'), entry.get('adaptive')
        if f is not None and a is not None and abs(f - a) > 12:
            return (round(f), round(a))
        return None

    lines = []

    nupl = payload.get('nupl')
    if nupl is not None:
        gap = adaptive_gap('nupl')
        gap_str = f'  ADAPTIVE GAP: fixed={gap[0]}, adaptive={gap[1]}' if gap else ''
        lines.append(f'  NUPL: {nupl:.2f}%  [{_risk_label(fixed_or_slider("nupl", "nupl"))}]'
                     '  (percentage scale: >75% euphoria; 25–50% optimism; <0% capitulation)'
                     + gap_str)

    mvrv = payload.get('mvrv_z_score')
    if mvrv is not None:
        gap = adaptive_gap('mvrv')
        gap_str = f'  ADAPTIVE GAP: fixed={gap[0]}, adaptive={gap[1]}' if gap else ''
        lines.append(f'  MVRV Z-score: {mvrv:.2f}  [{_risk_label(fixed_or_slider("mvrv", "mvrv_z_score"))}]'
                     '  (<0.5 historically cheap; >3.5 elevated)'
                     + gap_str)

    rhodl = payload.get('rhodl_ratio')
    if rhodl is not None:
        lines.append(f'  RHODL Ratio: {rhodl:,.0f}  [{_risk_label(slider_map.get("rhodl_ratio"))}]'
                     '  (LOW = LTH holding/accumulating; HIGH = LTH distributing into strength)')

    cvdd = payload.get('cvdd_ratio')
    if cvdd is not None:
        lines.append(f'  CVDD Ratio: {cvdd:.3f}  [{_risk_label(slider_map.get("cvdd_ratio"))}]'
                     '  (>1 = price above CVDD support; <1 = near structural buy floor)')

    asopr = payload.get('asopr')
    if asopr is not None:
        lines.append(f'  aSOPR: {asopr:.3f}  [{_risk_label(slider_map.get("asopr"))}]'
                     '  (<1.00 = short-term holders selling at a loss = capitulation)')

    fr_raw = mv('funding_rate')
    if isinstance(fr_raw, dict):
        avg7 = fr_raw.get('avg_7d') or fr_raw.get('latest')
        if avg7 is not None:
            cascade = '  *** LEVERAGE CASCADE RISK if >0.05% ***' if avg7 > 0.05 else ''
            lines.append(f'  Funding Rate (7d avg): {avg7:.4f}%  [{_risk_label(slider_map.get("funding_rate"))}]'
                         + cascade)

    cb = mv('cipherb')
    if isinstance(cb, dict):
        ws = cb.get('weekly_score')
        if ws is not None:
            div = ' [BEARISH DIVERGENCE — +12 risk pts, early top warning]' if cb.get('fast_bearish_div') else \
                  ' [bullish divergence — −12 risk pts, recovery signal]' if cb.get('fast_bullish_div') else ''
            lines.append(f'  CipherB (WaveTrend): {ws:.1f}/100{div}  [{_risk_label(slider_map.get("cipherb"))}]')

    mm = mv('mayer_multiple')
    if isinstance(mm, dict):
        gap = adaptive_gap('mayer')
        gap_str = f'  ADAPTIVE GAP: fixed={gap[0]}, adaptive={gap[1]}' if gap else ''
        lines.append(f'  Mayer Multiple: {mm.get("value", "?")}  [{_risk_label(fixed_or_slider("mayer", "mayer_multiple"))}]'
                     f'  (<0.8 below 200-day MA; >1.5 stretched)'
                     + gap_str)

    etf = payload.get('etf_flows')
    if isinstance(etf, dict):
        lines.append(f'  ETF Flows 14d: ${etf.get("value", 0):,.0f}M  [{_risk_label(slider_map.get("etf_flows"))}]'
                     '  (negative = institutional exit; positive reversal = leading bullish signal)')

    fg = payload.get('fear_greed')
    if fg is not None:
        lines.append(f'  Fear & Greed (7d avg): {fg}  [{_risk_label(slider_map.get("fear_greed"))}]'
                     '  (<25 extreme fear; >75 extreme greed)')

    yc = mv('yield_curve')
    if yc is not None:
        lines.append(f'  Yield Curve (10y-2y): {yc:.2f}%  [{_risk_label(slider_map.get("yield_curve_spread"))}]'
                     '  (re-steepening after inversion = recession arrived)')

    m2 = payload.get('m2_mom')
    if m2 is not None:
        lines.append(f'  M2 YoY growth: {m2:.1f}%  [{_risk_label(slider_map.get("m2_yoy"))}]'
                     '  (high = liquidity tailwind; contraction hits crypto 6-12 months after peak)')

    return '\n'.join(lines)


_DEVELOPER_INSTRUCTION = (
    "You are an experienced Bitcoin analyst writing for a general audience — "
    "smart people who follow markets but are not crypto experts. "
    "Your job is to look at today's data and give your honest read in plain human language, "
    "the way you'd explain it to a friend over coffee.\n\n"

    "OUTPUT FORMAT: valid JSON only — no markdown, no code fences.\n"
    "  \"summary\": ONE sentence. What is the bottom line right now? "
    "Write it naturally, not as a template. Examples of good tone:\n"
    "    'Bitcoin looks oversold and cheap by historical standards, "
    "but institutional money keeps leaving so the path of least resistance is still down.'\n"
    "    'Everything on-chain screams accumulation zone, but sentiment is terrible — "
    "classic late-bear setup before a recovery.'\n\n"
    "  \"analysis\": 2–3 sentences. Your expert read. "
    "Lead with the most important insight. "
    "Mention specific numbers only when they add real context (not as a list). "
    "Plain words — if a metric name is unfamiliar, explain what it measures in one word. "
    "End with what to watch: the one thing that would change the picture.\n\n"

    "BTCBRI CONTEXT:\n"
    "  Index 0–100. Below 25 = historically strong buy zone. Above 65 = elevated risk. "
    "Cycle bottoms average ~19; confirmed tops average ~86.\n\n"

    "ACCURACY RULES — check before writing:\n\n"

    "RULE 1 — LONG-TERM HOLDER BEHAVIOR:\n"
    "  Before saying 'whales selling' or 'smart money distributing':\n"
    "  → RHODL LOW = long-term holders are HOLDING, not selling. "
    "Low RHODL + falling price = patient accumulation.\n"
    "  → Only claim distribution if RHODL is HIGH and NUPL >50%.\n\n"

    "RULE 2 — LEVERAGE RISK:\n"
    "  If funding_rate >0.05%: this is an active amplifier — "
    "falling prices can trigger forced liquidations that push prices further down. "
    "Name it plainly if it applies.\n\n"

    "RULE 3 — ETF FLOWS:\n"
    "  Sustained negative ETF flows = institutional selling pressure. "
    "This is the primary near-term price driver, not a background factor.\n\n"

    "RULE 4 — TREND:\n"
    "  If the dominant signals are bearish, say the base case is continued weakness — "
    "don't frame it as 'contingent on reversal'. Reversal is the exception.\n\n"

    "STRICT RULES:\n"
    "  - Valid JSON only: {\"summary\": \"...\", \"analysis\": \"...\"}\n"
    "  - Do NOT suggest buy or sell actions.\n"
    "  - Do NOT use: safe, guaranteed, certain.\n"
    "  - Do NOT invent numbers.\n"
    "  - No jargon without a one-word explanation (e.g. 'NUPL (unrealized profit metric)').\n"
    "  - No metric lists — synthesize, don't enumerate.\n"
    "  - Sound like a human expert, not a financial report."
)


def _build_prompt(score: int, oc: int, tech: int, price: float, payload: dict, slider_map: dict) -> str:
    return f"""Today's Bitcoin snapshot:
- BTC Price: ${price:,}
- Risk Index: {score}/100  (below 25 = historically cheap; above 65 = elevated risk)
- On-chain signals: {oc}/100   Technical/Macro signals: {tech}/100

Raw indicator readings (use for accuracy — do not list them verbatim in your output):
{_metrics_block(payload, slider_map)}

Write your honest, plain-language assessment."""


def _parse_json_response(content: str) -> Optional[dict]:
    """Try to parse JSON from model output; handle code-fence wrapping."""
    content = content.strip()
    # strip markdown code fences if present
    if content.startswith('```'):
        content = content.split('```', 2)[1]
        if content.startswith('json'):
            content = content[4:]
        content = content.strip()
    try:
        result = json.loads(content)
        if isinstance(result, dict) and 'summary' in result and 'analysis' in result:
            return result
    except json.JSONDecodeError:
        pass
    return None


def _call_openai(prompt: str, api_key: str) -> Optional[dict]:
    """Call o3-mini; returns {'summary': str, 'analysis': str} or None."""
    url = 'https://api.openai.com/v1/chat/completions'
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    print(f'OpenAI: initiating call, key_prefix={api_key[:8]}…, prompt_len={len(prompt)}', flush=True)

    body = {
        'model': 'o3-mini',
        'reasoning_effort': 'low',
        'messages': [
            {'role': 'developer', 'content': _DEVELOPER_INSTRUCTION},
            {'role': 'user', 'content': prompt},
        ],
        'max_completion_tokens': 5000,
        'response_format': {'type': 'json_object'},
    }
    last_exc = None
    for attempt in range(3):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=60)
            print(f'OpenAI: attempt {attempt+1} status={resp.status_code}', flush=True)
            resp.raise_for_status()
            data = resp.json()
            choice = data['choices'][0]
            content = choice['message'].get('content')
            print(f'OpenAI: finish_reason={choice.get("finish_reason")}, content_len={len(content) if content else 0}', flush=True)
            if not content:
                print(f'OpenAI: empty content — choice={choice}', flush=True)
                return None
            result = _parse_json_response(content)
            if result:
                return result
            print(f'OpenAI: JSON parse failed — content={content[:200]}', flush=True)
            return None
        except Exception as exc:
            last_exc = exc
            print(f'OpenAI o3-mini attempt {attempt + 1} failed: {exc}', flush=True)
    print(f'OpenAI o3-mini call failed after 3 attempts: {last_exc}', flush=True)
    return None


def _translate_via_openai(texts: dict, api_key: str) -> Optional[dict]:
    """Translate {'summary': str, 'analysis': str} to Ukrainian. Returns same structure or None."""
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
                    'You are a professional Ukrainian translator specialising in financial and crypto analysis. '
                    'Translate the given JSON from English to Ukrainian. '
                    'The text is written in plain, conversational expert style — preserve that tone in Ukrainian. '
                    'Do NOT make the Ukrainian more formal or technical than the original. '
                    'Preserve all numeric values exactly. '
                    'Return a JSON object with the same keys ("summary" and "analysis") translated. '
                    'Output only valid JSON — no markdown, no extra text.'
                ),
            },
            {'role': 'user', 'content': json.dumps(texts, ensure_ascii=False)},
        ],
        'response_format': {'type': 'json_object'},
        'temperature': 0.2,
        'max_tokens': 1500,
    }
    last_exc = None
    for attempt in range(3):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            if not resp.ok:
                print(f'OpenAI translation attempt {attempt+1} failed: {resp.status_code} — {resp.text[:200]}', flush=True)
                last_exc = resp.text
                continue
            content = resp.json()['choices'][0]['message']['content'].strip()
            result = _parse_json_response(content)
            if result:
                return result
            print(f'OpenAI translation: JSON parse failed — content={content[:200]}', flush=True)
        except Exception as exc:
            last_exc = exc
            print(f'OpenAI translation attempt {attempt+1} failed: {exc}', flush=True)
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
    en_data = _call_openai(prompt, openai_key)
    if not en_data:
        return None
    print(f'OpenAI (en summary): {en_data.get("summary", "")[:120]}', flush=True)
    print(f'OpenAI (en analysis): {en_data.get("analysis", "")[:120]}…', flush=True)

    ua_data = _translate_via_openai(en_data, openai_key)
    if not ua_data:
        ua_data = dict(en_data)
        print('Translation unavailable — using English fallback for UA', flush=True)
    else:
        print(f'OpenAI (ua summary): {ua_data.get("summary", "")[:120]}', flush=True)

    return {'en': en_data, 'ua': ua_data}
