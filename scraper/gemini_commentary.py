"""
Generate a structured Bitcoin market commentary via OpenAI o3-mini.
Returns {'en': {'summary': str, 'analysis': str}, 'ua': {'summary': str, 'analysis': str}}
or None if the API key is absent or calls fail.
"""
import json
import os
import time
from typing import Optional

import requests


def _build_context_block(payload: dict, slider_map: dict) -> str:
    """Compact trend context — trajectory and key signals, not a metric dump."""
    lines = []

    score_now = payload.get('final_score')
    oc        = payload.get('onchain_score')
    tech      = payload.get('tech_score')
    price_now = payload.get('btc_price')

    prev_w    = (payload.get('prev_week') or {})
    score_7d  = prev_w.get('final_score')

    # 30-day lookback from score_history
    sh = payload.get('score_history') or []
    score_30d, price_30d = None, None
    if len(sh) >= 30:
        score_30d = sh[-30].get('score')
        price_30d = sh[-30].get('price')
    elif sh:
        score_30d = sh[0].get('score')
        price_30d = sh[0].get('price')

    # Score trajectory
    parts = []
    if score_30d is not None: parts.append(f'{score_30d} (30d ago)')
    if score_7d  is not None: parts.append(f'{score_7d} (7d ago)')
    if score_now is not None: parts.append(f'{score_now} (today)')
    if parts:
        lines.append('Risk index: ' + ' → '.join(parts))

    # Price trajectory
    if price_now and price_30d:
        pct = (price_now - price_30d) / price_30d * 100
        lines.append(f'BTC: ${price_30d:,.0f} → ${price_now:,.0f}  ({pct:+.1f}% in 30 days)')
    elif price_now:
        lines.append(f'BTC price: ${price_now:,.0f}')

    # Signal alignment
    if oc is not None and tech is not None:
        gap  = abs(oc - tech)
        note = 'diverging' if gap > 15 else 'aligned'
        lines.append(f'On-chain {oc}/100  ·  Technical/macro {tech}/100  ({note})')

    # Key contextual signals — only what's extreme or notable
    m = payload.get('metrics', {})
    def mv(key):
        obj = m.get(key)
        if obj is None: return None
        return obj.get('value') if isinstance(obj, dict) and 'value' in obj else obj

    flags = []

    etf = payload.get('etf_flows')
    if isinstance(etf, dict):
        v = etf.get('value', 0) or 0
        if   v < -2000: flags.append(f'heavy ETF outflows (${v:,.0f}M / 14d) — institutional money leaving')
        elif v < -500:  flags.append(f'negative ETF flows (${v:,.0f}M / 14d) — mild institutional caution')
        elif v >  2000: flags.append(f'strong ETF inflows (${v:,.0f}M / 14d) — institutional demand')

    cb = mv('cipherb')
    if isinstance(cb, dict):
        if   cb.get('fast_bearish_div'): flags.append('momentum divergence: bearish (early top warning)')
        elif cb.get('fast_bullish_div'): flags.append('momentum divergence: bullish (recovery signal)')

    fr = mv('funding_rate')
    if isinstance(fr, dict):
        avg7 = fr.get('avg_7d') or fr.get('latest')
        if avg7 is not None and avg7 > 0.05:
            flags.append(f'leverage elevated (funding {avg7:.3f}%) — cascading-liquidation risk')

    ac = payload.get('adaptive_calibration') or {}
    nupl_ac = ac.get('nupl') or {}
    if isinstance(nupl_ac, dict):
        pct = nupl_ac.get('adaptive')
        if   pct is not None and pct < 20: flags.append('holders in low-profit phase — historically patient accumulation')
        elif pct is not None and pct > 80: flags.append('holders sitting on large gains — elevated distribution risk')

    yc = mv('yield_curve')
    if yc is not None and yc > 0.3:
        flags.append(f'yield curve re-steepening ({yc:+.2f}%) — macro normalization underway')

    if flags:
        lines.append('Notable: ' + '; '.join(flags))

    return '\n'.join(lines)


_DEVELOPER_INSTRUCTION = (
    "You are a seasoned Bitcoin market analyst writing a brief daily note.\n\n"

    "CRITICAL: The user can already see ALL the raw indicators on screen. "
    "Do NOT list, restate, or describe individual metrics. "
    "Your entire job is to synthesize what the combination means "
    "and where the market is likely heading — like a sharp analyst talking to a colleague.\n\n"

    "OUTPUT FORMAT: valid JSON only, no markdown, no code fences.\n"
    "  \"summary\": ONE sentence. "
    "The bottom line right now — what kind of market environment is this? "
    "Be direct and opinionated. Avoid clichés. Examples of the right tone:\n"
    "    'The foundation is quietly strengthening while price stagnates — a classic late-accumulation setup.'\n"
    "    'Risk is cooling after an overextended run, but macro pressure keeps the ceiling low.'\n"
    "    'On-chain says cheap; institutions say not yet — and institutions are winning short-term.'\n\n"
    "  \"analysis\": 2–3 sentences. "
    "First: what the trajectory tells you (not the metrics, the STORY behind them). "
    "Second: the main tension or risk to watch. "
    "Third (optional): what would change the picture — one specific catalyst. "
    "Write in plain, confident language. No hedging phrases like 'it could potentially suggest'. "
    "End on something concrete.\n\n"

    "ACCURACY GUARDRAILS:\n"
    "  - Low RHODL + low NUPL = holders accumulating quietly, NOT distributing.\n"
    "  - Only claim distribution if NUPL >50% AND RHODL is elevated.\n"
    "  - Negative ETF flows = primary near-term price drag, name it directly if heavy.\n"
    "  - If leverage is high (funding >0.05%), say it amplifies downside — don't soften it.\n\n"

    "STRICT:\n"
    "  - Valid JSON only: {\"summary\": \"...\", \"analysis\": \"...\"}\n"
    "  - No buy/sell suggestions. No 'safe', 'guaranteed', 'certain'.\n"
    "  - No invented numbers — only use what's in the context block.\n"
    "  - Do not start either field with a metric name."
)


def _build_prompt(score: int, oc: int, tech: int, price: float, payload: dict, slider_map: dict) -> str:
    context = _build_context_block(payload, slider_map)
    zone = (
        'historically strong buy zone' if score < 25 else
        'discount / accumulation zone'  if score < 40 else
        'neutral zone'                   if score < 55 else
        'caution / overheating zone'     if score < 75 else
        'extreme risk zone'
    )
    return (
        f"BTCBRI = {score}/100  ({zone})\n\n"
        f"{context}\n\n"
        "Write your analyst note."
    )


def _parse_json_response(content: str) -> Optional[dict]:
    content = content.strip()
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
    url     = 'https://api.openai.com/v1/chat/completions'
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    print(f'OpenAI: initiating call, key_prefix={api_key[:8]}…, prompt_len={len(prompt)}', flush=True)

    body = {
        'model': 'o3-mini',
        'reasoning_effort': 'low',
        'messages': [
            {'role': 'developer', 'content': _DEVELOPER_INSTRUCTION},
            {'role': 'user',      'content': prompt},
        ],
        'max_completion_tokens': 4000,
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
            data   = resp.json()
            choice = data['choices'][0]
            content = choice['message'].get('content')
            print(f'OpenAI: finish_reason={choice.get("finish_reason")}, '
                  f'content_len={len(content) if content else 0}', flush=True)
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
    url     = 'https://api.openai.com/v1/chat/completions'
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
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
                print(f'OpenAI translation attempt {attempt+1} failed: '
                      f'{resp.status_code} — {resp.text[:200]}', flush=True)
                last_exc = resp.text
                continue
            content = resp.json()['choices'][0]['message']['content'].strip()
            result  = _parse_json_response(content)
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

    prompt  = _build_prompt(score, oc, tech, price, payload, slider_map)
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
