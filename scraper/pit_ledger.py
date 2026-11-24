"""Append-only Point-in-Time (PiT) daily observation ledger.

Records exact raw observations, provider timestamps, and model/shadow outputs
at the moment of each run so future 2027-2028 validations have true PiT vintages.
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Any


def append_pit_record(payload: dict[str, Any], ledger_path: str = 'data/pit_ledger.jsonl') -> dict[str, Any]:
    """Append one immutable PiT snapshot line to ledger_path and return the record."""
    now_utc = (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace('+00:00', 'Z')
    )
    metrics = payload.get('metrics') or {}
    raw_snapshot: dict[str, Any] = {}
    for k, v in metrics.items():
        if isinstance(v, dict):
            raw_snapshot[k] = {
                'value': v.get('value'),
                'source': v.get('source'),
                'updated': v.get('updated'),
                'observation_date': v.get('observation_date'),
                'is_fallback': bool(v.get('is_fallback', False)),
            }
        else:
            raw_snapshot[k] = {'value': v}

    record = {
        'observed_at_utc': now_utc,
        'payload_timestamp': payload.get('timestamp'),
        'as_of_date': payload.get('date') or (str(payload.get('timestamp') or '')[:10] or now_utc[:10]),
        'btc_price': payload.get('btc_price'),
        'bri_score': payload.get('bri_score', payload.get('final_score')),
        'forward_risk_13w': payload.get('forward_risk', payload.get('v5b_score')),
        'forward_risk_validated': bool(payload.get('v5b_validated', False)),
        'forward_risk_method': payload.get('forward_risk_method'),
        'tail_risk_shadow_m2_pct': (payload.get('tail_risk_13w') or {}).get('shadow_m2_pct'),
        'market_regime': payload.get('market_regime'),
        'allocation_signal': payload.get('allocation_signal'),
        'failed_live_fetches': payload.get('failed_live_fetches') or [],
        'raw_metrics': raw_snapshot,
    }

    os.makedirs(os.path.dirname(ledger_path) or '.', exist_ok=True)
    with open(ledger_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, separators=(',', ':'), ensure_ascii=False) + '\n')
    return record
