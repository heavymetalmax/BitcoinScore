"""Drift Monitor: three daily checks for model health.

1. SCORE_DRIFT  — final_score moved > 5 pts with no metric/phase change
2. PHASE_STUCK  — HMM phase unchanged for > 90 consecutive entries
3. OOD          — any mapped metric z-score > 3σ vs 2-year lookback

Writes data/drift_report.json. Prints alerts/warnings to stdout for CI.
"""
import json
import math
import os
import datetime
import sys

DATA_PATH         = 'data/data.json'
DV_PATH           = 'data/history/daily_vector.json'
PHASE_HIST_PATH   = 'data/history/phase_history.json'
REPORT_PATH       = 'data/drift_report.json'

SCORE_DRIFT_THRESHOLD = 5.0   # pts — score change without cause
METRIC_CHANGE_MIN     = 5.0   # pts — min mapped change to count as "cause"
PHASE_STUCK_DAYS      = 90    # consecutive same-phase entries → warning
OOD_Z_THRESHOLD       = 3.0   # σ — metric outside this → OOD warning
OOD_MIN_HISTORY       = 30    # entries required before OOD check fires


def _load(path):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return []


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2)


# ── Check 1: Score drift without metric/phase change ─────────────────────────

def _check_score_drift(current, prev_dv_entry, prev_phase):
    alerts = []
    curr_score = current.get('final_score')
    if prev_dv_entry is None or curr_score is None:
        return alerts

    prev_score = prev_dv_entry.get('final_score')
    if prev_score is None:
        return alerts

    delta = abs(curr_score - prev_score)
    if delta <= SCORE_DRIFT_THRESHOLD:
        return alerts

    # Check if any mapped metric moved enough to explain the change
    curr_mapped = current.get('v3_utilities') or {}
    if not curr_mapped:
        # fallback: build from metrics if v3_utilities not present
        curr_mapped = {}
    # daily_vector 'mapped' dict is the canonical source
    prev_mapped = prev_dv_entry.get('mapped', {})

    # Use data.json metrics mapped field if available
    metric_changed = False
    for key, prev_val in prev_mapped.items():
        # curr mapped not in data.json directly — use prev vs score delta heuristic
        # We compare with the most recent daily_vector mapped (current run writes it AFTER scoring)
        # so prev_mapped is yesterday's, and we infer today's from data.json signals
        _ = prev_val  # comparison below uses phase + score pattern instead
        metric_changed = metric_changed  # will use phase + raw approach

    # Simpler: check if any raw metric changed significantly
    curr_raw_m = {k: v.get('value') if isinstance(v, dict) else v
                  for k, v in (current.get('metrics') or {}).items()}
    prev_raw_m = prev_dv_entry.get('raw', {})

    raw_changed = False
    for key, prev_val in prev_raw_m.items():
        curr_val = curr_raw_m.get(key)
        if prev_val is None or curr_val is None:
            continue
        if not isinstance(prev_val, (int, float)) or not isinstance(curr_val, (int, float)):
            continue
        # Normalize change relative to typical range
        if abs(curr_val - prev_val) > 0.01 * max(abs(prev_val), 1e-9) * 5:
            raw_changed = True
            break

    curr_phase = current.get('v3_phase') or (current.get('phase') or {}).get('phase')
    phase_changed = (curr_phase != prev_phase)

    if not raw_changed and not phase_changed:
        alerts.append(
            f"SCORE_DRIFT: {prev_score}→{curr_score} (Δ{delta:.1f}pts) "
            f"with no metric change or phase transition"
        )
    return alerts


# ── Check 2: HMM phase stuck > 90 consecutive days ───────────────────────────

def _check_phase_stuck(phase_history, current_phase):
    warnings = []
    if not phase_history or current_phase is None:
        return warnings

    count = 0
    for entry in reversed(phase_history):
        if entry.get('phase') == current_phase:
            count += 1
        else:
            break

    if count >= PHASE_STUCK_DAYS:
        warnings.append(
            f"PHASE_STUCK: HMM phase '{current_phase}' unchanged for "
            f"{count} consecutive days (>{PHASE_STUCK_DAYS} threshold)"
        )
    return warnings


# ── Check 3: OOD — metric outside 3σ historical range ────────────────────────

def _check_ood(dv_history, current):
    warnings = []
    if len(dv_history) < OOD_MIN_HISTORY:
        return warnings

    lookback = dv_history[-730:]  # max 2 years

    # Collect per-metric series from mapped field
    series: dict[str, list[float]] = {}
    for entry in lookback:
        for key, val in (entry.get('mapped') or {}).items():
            if isinstance(val, (int, float)):
                series.setdefault(key, []).append(float(val))

    # Current mapped values: use the last daily_vector entry (just written)
    curr_mapped = (dv_history[-1].get('mapped') or {}) if dv_history else {}

    for key, vals in series.items():
        if len(vals) < OOD_MIN_HISTORY:
            continue
        curr_val = curr_mapped.get(key)
        if curr_val is None:
            continue
        n = len(vals)
        mean = sum(vals) / n
        std = math.sqrt(sum((v - mean) ** 2 for v in vals) / n)
        if std < 0.5:
            continue  # near-constant metric — skip
        z = (curr_val - mean) / std
        if abs(z) > OOD_Z_THRESHOLD:
            direction = "high" if z > 0 else "low"
            warnings.append(
                f"OOD: {key}={curr_val:.0f} is {direction} (z={z:.1f}, "
                f"mean={mean:.1f}, σ={std:.1f}, n={n})"
            )
    return warnings


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    today = datetime.date.today().isoformat()

    if not os.path.exists(DATA_PATH):
        print(f"Drift Monitor: {DATA_PATH} not found — skipping")
        return 0

    with open(DATA_PATH, encoding='utf-8') as f:
        current = json.load(f)

    dv_history     = _load(DV_PATH)
    phase_history  = _load(PHASE_HIST_PATH)

    current_phase = (
        current.get('v3_phase')
        or (current.get('phase') or {}).get('phase')
    )

    # Previous entries (exclude today if already written)
    prev_dv    = next((e for e in reversed(dv_history) if e.get('date') != today), None)
    prev_phase = next((e['phase'] for e in reversed(phase_history) if e.get('date') != today), None)

    alerts   = []
    warnings = []

    alerts   += _check_score_drift(current, prev_dv, prev_phase)
    warnings += _check_phase_stuck(phase_history, current_phase)
    warnings += _check_ood(dv_history, current)

    # Update phase history (de-dup by date)
    phase_history = [e for e in phase_history if e.get('date') != today]
    phase_history.append({
        'date':        today,
        'phase':       current_phase,
        'tiz_days':    current.get('v3_tiz_days'),
        'final_score': current.get('final_score'),
    })
    phase_history.sort(key=lambda e: e.get('date', ''))
    _save(PHASE_HIST_PATH, phase_history[-730:])

    report = {
        'date':     today,
        'phase':    current_phase,
        'score':    current.get('final_score'),
        'alerts':   alerts,
        'warnings': warnings,
        'ok':       len(alerts) == 0,
    }
    _save(REPORT_PATH, report)

    for a in alerts:
        print(f'[DRIFT] ALERT: {a}')
    for w in warnings:
        print(f'[DRIFT] WARNING: {w}')
    if not alerts and not warnings:
        print(f'[DRIFT] OK — phase={current_phase} score={current.get("final_score")} date={today}')

    return 0


if __name__ == '__main__':
    sys.exit(run())
