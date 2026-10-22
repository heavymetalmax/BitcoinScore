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
    import bisect
    from scraper.score import compute_score
    from scraper.cycle_normalizer import invalidate_caches as inv_cycle
    from scraper.maps import invalidate_caches as inv_maps
    from scraper.wave_resonance import invalidate_caches as inv_wr

    scores_path = os.path.join(ROOT, 'data', 'history', 'scores.json')
    if not os.path.exists(scores_path):
        print("scores.json not found — skipping recompute")
        return

    with open(scores_path, encoding='utf-8') as f:
        rows = json.load(f)

    # Merge missing raw metrics from unified_history.json into scores.json
    uh_path = os.path.join(ROOT, 'data', 'history', 'unified_history.json')
    uh_by_date = {}
    if os.path.exists(uh_path):
        raw_uh = json.load(open(uh_path, encoding='utf-8'))
        for r in (raw_uh.get('series', raw_uh) if isinstance(raw_uh, dict) else raw_uh):
            if isinstance(r, dict) and r.get('date'):
                uh_by_date[r['date'][:10]] = r

    raw_fields = [
        'btc_price', 'nupl', 'mvrv', 'puell', 'rhodl_ratio', 'asopr',
        'cvdd_ratio', 'mayer_multiple', 'fear_greed', 'm2_yoy',
        'cipherb_daily', 'pi_gap_pct', 'dxy', 'funding_rate',
        'lth_supply_pct', 'yield_curve',
    ]
    ffill_max_days = {
        'dxy': 7,
        'yield_curve': 10,
        'm2_yoy': 45,
        'lth_supply_pct': 30,
        'puell': 14,
        'funding_rate': 7,
        'cvdd_ratio': 7,
        'asopr': 7,
        'rhodl_ratio': 7,
    }
    last_seen_val = {}
    last_seen_date = {}

    for row in rows:
        d_str = row.get('date', '')[:10]
        if not d_str:
            continue
        uh_r = uh_by_date.get(d_str, {})
        for fld in raw_fields:
            v = row.get(fld)
            if v is None and uh_r.get(fld) is not None:
                v = uh_r[fld]
                row[fld] = v
            # Normalize units if needed
            if v is not None and fld == 'lth_supply_pct' and float(v) > 100:
                v = round(float(v) / 21_000_000 * 100.0, 4)
                row[fld] = v
            if v is not None:
                last_seen_val[fld] = v
                last_seen_date[fld] = d_str
            elif fld in ffill_max_days and fld in last_seen_val:
                try:
                    gap = (datetime.date.fromisoformat(d_str) - datetime.date.fromisoformat(last_seen_date[fld])).days
                    if 0 < gap <= ffill_max_days[fld]:
                        row[fld] = last_seen_val[fld]
                except Exception:
                    pass

    # Save enriched raw metrics to scores.json first so normalizer/WR caches see full series
    with open(scores_path, 'w', encoding='utf-8') as f:
        json.dump(rows, f, separators=(',', ':'))

    inv_cycle()
    inv_maps()
    inv_wr()

    # Load weekly and daily CipherB history for accurate historical reconstruction
    wk_rows_by_date = {}
    wk_path = os.path.join(ROOT, 'data', 'history', 'cipherb_btcusdt_1w.json')
    if os.path.exists(wk_path):
        raw_wk = json.load(open(wk_path, encoding='utf-8'))
        for r in (raw_wk.get('series', raw_wk) if isinstance(raw_wk, dict) else raw_wk):
            if isinstance(r, dict) and r.get('date') and r.get('weekly_score') is not None:
                wk_rows_by_date[r['date'][:10]] = r
    wk_dates = sorted(wk_rows_by_date.keys())

    d1_rows_by_date = {}
    d1_path = os.path.join(ROOT, 'data', 'history', 'cipherb_btcusdt_1d.json')
    if os.path.exists(d1_path):
        raw_d1 = json.load(open(d1_path, encoding='utf-8'))
        for r in (raw_d1.get('series', raw_d1) if isinstance(raw_d1, dict) else raw_d1):
            if isinstance(r, dict) and r.get('date') and r.get('daily_score') is not None:
                d1_rows_by_date[r['date'][:10]] = r

    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    updated = 0
    history_tuples = []
    last_res = None
    n_rows = len(rows)

    for row_idx, row in enumerate(rows):
        d_str = row.get('date', '')[:10]
        if not d_str:
            continue
        # Backfill missing cipherb_daily in scores.json from 1d history
        if row.get('cipherb_daily') is None and d_str in d1_rows_by_date:
            row['cipherb_daily'] = round(float(d1_rows_by_date[d_str]['daily_score']), 2)

        if d_str < cutoff or row.get('nupl') is None or row.get('mvrv') is None:
            history_tuples.append((d_str, row.get('final_score'), row.get('phase'), row.get('w_bot')))
            continue

        td = datetime.date.fromisoformat(d_str)
        cb_val = row.get('cipherb_daily')
        wk_r = {}
        if wk_dates:
            idx = bisect.bisect_right(wk_dates, d_str) - 1
            if idx >= 0:
                wk_r = wk_rows_by_date[wk_dates[idx]]
        d1_r = d1_rows_by_date.get(d_str, {})
        wk_score = wk_r.get('weekly_score', cb_val)

        cb_dict = None
        if wk_score is not None or cb_val is not None:
            cb_dict = {
                'weekly_score':             wk_score if wk_score is not None else cb_val,
                'daily_score':              cb_val,
                'fast_bearish_div':         bool(wk_r.get('fast_bearish_div', False)),
                'fast_bullish_div':         bool(wk_r.get('fast_bullish_div', False)),
                'bearish_div':              bool(wk_r.get('bearish_div', False)),
                'bullish_div':              bool(wk_r.get('bullish_div', False)),
                'double_bearish_div':       bool(wk_r.get('double_bearish_div', False)),
                'double_bullish_div':       bool(wk_r.get('double_bullish_div', False)),
                'daily_fast_bearish_div':   bool(d1_r.get('fast_bear_div', False)),
                'daily_fast_bullish_div':   bool(d1_r.get('fast_bull_div', False)),
                'daily_bearish_div':        bool(d1_r.get('bearish_div', False)),
                'daily_bullish_div':        bool(d1_r.get('bullish_div', False)),
                'daily_double_bearish_div': bool(d1_r.get('double_bearish_div', False)),
                'daily_double_bullish_div': bool(d1_r.get('double_bullish_div', False)),
            }

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
            prev_scores={},
            scores_history=history_tuples,
            btc_price=row.get('btc_price'),
            persist_hmm_cache=False,
            include_v5b=(row_idx >= n_rows - 14),
        )
        if res.get('final_score') is not None:
            row['final_score'] = res['final_score']
            row['onchain_score'] = res.get('onchain_avg')
            row['tech_score'] = res.get('tech_avg')
            row['phase'] = res.get('phase')
            row['w_bot'] = res.get('w_bot')
            row['w_top'] = res.get('w_top')
            if res.get('v5b_score') is not None:
                row['v5b_score'] = res['v5b_score']
            if res.get('market_regime') is not None:
                row['market_regime'] = res['market_regime']
            updated += 1
            last_res = res

        history_tuples.append((d_str, row.get('final_score'), row.get('phase'), row.get('w_bot')))

    with open(scores_path, 'w', encoding='utf-8') as f:
        json.dump(rows, f, separators=(',', ':'))
    print(f"Recomputed V4 scores for {updated} rows in {scores_path}")

    # Sync latest recomputed score into data/data.json and web/data.json
    if last_res is not None:
        for dp in [os.path.join(ROOT, 'data', 'data.json'), os.path.join(ROOT, 'web', 'data.json')]:
            if os.path.exists(dp):
                try:
                    payload = json.load(open(dp, encoding='utf-8'))
                    payload['final_score'] = last_res['final_score']
                    payload['onchain_score'] = last_res.get('onchain_avg')
                    payload['tech_score'] = last_res.get('tech_avg')
                    payload['phase'] = last_res.get('phase')
                    payload['w_bot'] = last_res.get('w_bot')
                    payload['w_neutral'] = last_res.get('w_neutral')
                    payload['w_top'] = last_res.get('w_top')
                    payload['oc_basket'] = last_res.get('oc_basket')
                    payload['ms_basket'] = last_res.get('ms_basket')
                    payload['mc_basket'] = last_res.get('mc_basket')
                    payload['cp_basket'] = last_res.get('cp_basket')
                    payload['utilities'] = last_res.get('utilities')
                    payload['normalized_scores'] = last_res.get('normalized_scores')
                    if last_res.get('v5b_score') is not None:
                        payload['v5b_score'] = last_res['v5b_score']
                    if last_res.get('market_regime') is not None:
                        payload['market_regime'] = last_res['market_regime']
                    if last_res.get('composite_risk') is not None:
                        payload['composite_risk'] = last_res['composite_risk']
                    if last_res.get('meta_score') is not None:
                        payload['meta_score'] = last_res['meta_score']
                    with open(dp, 'w', encoding='utf-8') as f:
                        json.dump(payload, f, indent=2)
                except Exception as e:
                    print(f"Warning: failed to update {dp}: {e}")


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
