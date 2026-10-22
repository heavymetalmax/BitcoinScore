#!/usr/bin/env python3
"""Rebuild unified history, V4 historical scores, bucket returns, sparklines, ICS, and optionally ML models.

Usage:
    python3 tools/rebuild_all_v4.py               # rebuild history + V4 scores + bucket returns + sparklines + ICS
    python3 tools/rebuild_all_v4.py --retrain-ml  # also retrain HMM phase model & V5B model
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _run_step(label: str, cmd: list[str]) -> None:
    print(f"\n=== {label} ===")
    subprocess.run(cmd, cwd=ROOT, check=True)


def recompute_recent_scores(days: int = 365) -> None:
    """Recompute V4 scores in data/history/scores.json for the last `days` entries."""
    from scraper.score import compute_score
    from scraper.cycle_normalizer import invalidate_caches as inv_cycle
    from scraper.maps import invalidate_caches as inv_maps

    inv_cycle()
    inv_maps()

    scores_path = os.path.join(ROOT, 'data', 'history', 'scores.json')
    if not os.path.exists(scores_path):
        print("scores.json not found — skipping recompute")
        return

    with open(scores_path, encoding='utf-8') as f:
        rows = json.load(f)

    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    updated = 0
    history_tuples = []

    for row in rows:
        d_str = row.get('date', '')[:10]
        if not d_str:
            continue
        if d_str < cutoff or row.get('nupl') is None or row.get('mvrv') is None:
            history_tuples.append((d_str, row.get('final_score'), row.get('phase'), row.get('w_bot')))
            continue

        td = datetime.date.fromisoformat(d_str)
        cb_val = row.get('cipherb_daily')
        cb_dict = (
            {'weekly_score': cb_val, 'daily_score': cb_val, 'fast_bearish_div': False}
            if cb_val is not None else None
        )
        raw = {
            'nupl': row.get('nupl'),
            'mvrv': row.get('mvrv'),
            'rhodl_ratio': row.get('rhodl_ratio'),
            'cvdd_ratio': row.get('cvdd_ratio'),
            'asopr': row.get('asopr'),
            'puell_multiple': row.get('puell'),
            'mayer_multiple': row.get('mayer_multiple'),
            'fear_greed': row.get('fear_greed'),
            'm2_yoy': row.get('m2_yoy'),
            'm2_mom': row.get('m2_yoy'),
            'yield_curve': row.get('yield_curve'),
            'lth_supply_pct': row.get('lth_supply_pct'),
            'dxy': row.get('dxy'),
            'funding_rate': row.get('funding_rate'),
            'pi_cycle': row.get('pi_gap_pct'),
            'pi_gap_pct': row.get('pi_gap_pct'),
            'cipherb': cb_dict,
        }
        res = compute_score(
            raw_metrics=raw,
            target_date=td,
            scores_history=history_tuples,
            btc_price=row.get('btc_price'),
            persist_hmm_cache=False,
            include_v5b=True,
        )
        if res.get('final_score') is not None:
            row['final_score'] = res['final_score']
            row['onchain_score'] = res.get('onchain_score')
            row['tech_score'] = res.get('tech_score')
            row['phase'] = res.get('phase')
            row['w_bot'] = res.get('w_bot')
            row['w_top'] = res.get('w_top')
            if res.get('v5b_score') is not None:
                row['v5b_score'] = res['v5b_score']
            if res.get('market_regime') is not None:
                row['market_regime'] = res['market_regime']
            updated += 1

        history_tuples.append((d_str, row.get('final_score'), row.get('phase'), row.get('w_bot')))

    with open(scores_path, 'w', encoding='utf-8') as f:
        json.dump(rows, f, separators=(',', ':'))
    print(f"Recomputed V4 scores for {updated} rows in {scores_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=365, help='Number of recent days to recompute in scores.json')
    parser.add_argument('--retrain-ml', action='store_true', help='Retrain HMM phase model and V5B model')
    args = parser.parse_args()

    _run_step("1. Build unified history", [sys.executable, "tools/build_unified_history.py"])
    print(f"\n=== 2. Recompute V4 scores in scores.json (last {args.days} days) ===")
    recompute_recent_scores(days=args.days)

    if args.retrain_ml:
        _run_step("3a. Retrain V3 HMM Phase Model", [sys.executable, "tools/train_v3_hmm_model.py"])
        _run_step("3b. Build V5B training features", [sys.executable, "tools/build_training_features.py"])
        _run_step("3c. Build V5B labels", [sys.executable, "tools/build_v5b_labels.py"])
        _run_step("3d. Train V5B model", [sys.executable, "tools/train_v5b_model.py"])

    _run_step("4. Recompute bucket returns", [sys.executable, "tools/bucket_returns.py"])
    if os.path.exists(os.path.join(ROOT, "data", "bucket_returns.json")):
        shutil.copyfile(
            os.path.join(ROOT, "data", "bucket_returns.json"),
            os.path.join(ROOT, "web", "bucket_returns.json"),
        )
    _run_step("5. Generate ICS & patch data.json", [sys.executable, "tools/generate_ics.py"])
    _run_step("6. Generate sparklines", [sys.executable, "tools/generate_sparklines.py"])
    _run_step("7. Sync web scores", [sys.executable, "tools/sync_web_scores.py"])
    _run_step("8. Run drift monitor", [sys.executable, "tools/drift_monitor.py"])


if __name__ == '__main__':
    main()
