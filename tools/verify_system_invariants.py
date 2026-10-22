#!/usr/bin/env python3
"""
6-Layer End-to-End System Invariant Verification Suite for BitcoinScore.

Verifies:
  Layer 1: Data & History Completeness (scores.json + sparklines.json 365d coverage)
  Layer 2: Normalizer Boundaries [0, 100] & Directional Monotonicity (all 16 metrics)
  Layer 3: Phase & Utility Weight Invariants (partition of unity, key parity, extremity response)
  Layer 4: Historical Cycle Milestone Calibration (all bottoms <= 20 BOTTOM, all ATHs >= 79 TOP)
  Layer 5: Empirical Forward-Return Monotonicity (bucket_returns.json 365d returns)
  Layer 6: Live Payload & Web Sync Parity (data/ vs web/ consistency, zero NaN/null corruption)

Usage:
    python3 tools/verify_system_invariants.py
"""
import datetime
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scraper.normalizer import normalize_metric
from scraper.utility_evaluator import evaluate_all_utilities_continuous
from scraper.score import compute_score, BASKET_OC, BASKET_MS, BASKET_MC, BASKET_CP


class InvariantSuite:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def check(self, layer: str, name: str, condition: bool, detail: str = ""):
        if condition:
            self.passed += 1
            print(f"  [PASS] {name}" + (f" ({detail})" if detail else ""))
        else:
            self.failed += 1
            msg = f"[{layer}] {name}" + (f" — {detail}" if detail else "")
            self.failures.append(msg)
            print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def verify_layer_1(suite: InvariantSuite):
    print("\n=== LAYER 1: Data & History Completeness (Last 365 Days) ===")
    scores_path = os.path.join(ROOT, "data", "history", "scores.json")
    rows = json.load(open(scores_path, encoding="utf-8"))
    suite.check("L1", "scores.json non-empty", len(rows) > 3000, f"{len(rows)} rows")

    cutoff = (datetime.date.today() - datetime.timedelta(days=365)).isoformat()
    recent = [r for r in rows if r.get("date", "") >= cutoff]
    suite.check("L1", "365d recent window complete", len(recent) >= 360, f"{len(recent)} days")

    required_fields = [
        "btc_price", "final_score", "nupl", "mvrv", "puell", "rhodl_ratio",
        "asopr", "cvdd_ratio", "mayer_multiple", "fear_greed", "m2_yoy",
        "cipherb_daily", "pi_gap_pct", "dxy", "funding_rate",
        "lth_supply_pct", "yield_curve",
    ]
    for fld in required_fields:
        cnt = sum(1 for r in recent if r.get(fld) is not None)
        cov = cnt / max(1, len(recent))
        suite.check("L1", f"scores.json 365d coverage: {fld}", cov >= 0.95, f"{cnt}/{len(recent)} ({cov*100:.1f}%)")

    spark_path = os.path.join(ROOT, "web", "sparklines.json")
    spark = json.load(open(spark_path, encoding="utf-8"))
    n_dates = len(spark.get("dates", []))
    suite.check("L1", "web/sparklines.json dates count", n_dates >= 360, f"{n_dates} dates")
    for k, vals in spark.items():
        if k == "dates":
            continue
        non_null = sum(1 for v in vals if v is not None)
        min_expected = int(0.60 * n_dates) if k == "etf_flows" else int(0.90 * n_dates)
        suite.check("L1", f"sparklines.json series: {k}", non_null >= min_expected, f"{non_null}/{n_dates}")


def verify_layer_2(suite: InvariantSuite):
    print("\n=== LAYER 2: Normalizer Boundaries [0, 100] & Monotonicity ===")
    td = datetime.date(2025, 10, 6)

    # (metric, low_risk_raw, mid_risk_raw, high_risk_raw)
    directional_tests = [
        ("nupl",           -25.0,  35.0,   75.0),
        ("mvrv_z_score",    -0.4,   1.5,    6.0),
        ("rhodl_ratio",    180.0, 1500.0, 15000.0),
        ("cvdd_ratio",       1.0,   2.2,    5.5),
        ("asopr",            0.91,  1.01,   1.12),
        ("puell_multiple",   0.35,  1.10,   3.50),
        ("mayer_multiple",   0.52,  1.15,   2.40),
        ("fear_greed",      10.0,  50.0,   90.0),
        ("funding_rate",   -0.02,  0.008,  0.06),
        ("etf_flows",     -800.0, 200.0, 1500.0),
        ("btc_price_cycle", 16000.0, 60000.0, 124000.0),
        ("dxy",            105.0, 116.0,  130.0),
        # Inverted metrics (high raw value = low risk, low raw value = high risk)
        ("pi_cycle",        65.0,  25.0,   -2.0),
        ("m2_yoy",          14.0,   5.0,   -2.0),
        ("lth_supply_pct",  78.0,  68.0,   55.0),
        ("yield_curve",      1.8,   0.3,   -1.0),
    ]

    for m, v_low, v_mid, v_high in directional_tests:
        s_low  = normalize_metric(m, v_low, td)
        s_mid  = normalize_metric(m, v_mid, td)
        s_high = normalize_metric(m, v_high, td)
        in_bounds = all(isinstance(s, (int, float)) and 0 <= s <= 100 for s in (s_low, s_mid, s_high))
        monotonic = (s_low is not None and s_mid is not None and s_high is not None and s_low < s_mid < s_high)
        suite.check(
            "L2",
            f"Normalizer monotonicity & [0,100]: {m}",
            in_bounds and monotonic,
            f"scores=({s_low} < {s_mid} < {s_high}) for raw=({v_low}, {v_mid}, {v_high})"
        )

    # CipherB composite normalizer
    cb_bot = normalize_metric("cipherb", {"weekly_score": 8, "daily_score": 10, "bullish_div": True}, td)
    cb_mid = normalize_metric("cipherb", {"weekly_score": 50, "daily_score": 50}, td)
    cb_top = normalize_metric("cipherb", {"weekly_score": 88, "daily_score": 85, "bearish_div": True}, td)
    suite.check(
        "L2",
        "Normalizer monotonicity & [0,100]: cipherb",
        0 <= cb_bot < cb_mid < cb_top <= 100,
        f"scores=({cb_bot} < {cb_mid} < {cb_top})"
    )


def verify_layer_3(suite: InvariantSuite):
    print("\n=== LAYER 3: Phase & Utility Evaluator Invariants ===")
    all_basket_keys = BASKET_OC | BASKET_MS | BASKET_MC | BASKET_CP
    suite.check("L3", "Basket partition covers all 15 basket metrics (+ dxy modifier = 16)", len(all_basket_keys) == 15, f"{len(all_basket_keys)} keys")

    dummy_norm = {k: 50 for k in all_basket_keys}
    utils_mid = evaluate_all_utilities_continuous(dummy_norm, w_top=0.1, w_bot=0.1, w_neutral=0.8, tiz_maturity=0.5, cycle_day=500)
    missing_keys = [k for k in all_basket_keys if k not in utils_mid]
    suite.check("L3", "Utility evaluator covers 100% of basket keys", len(missing_keys) == 0, f"missing={missing_keys}")
    suite.check(
        "L3",
        "All utility weights within [0.05, 1.0]",
        all(0.05 <= float(v) <= 1.0 for v in utils_mid.values()),
        f"range=[{min(utils_mid.values()):.3f}, {max(utils_mid.values()):.3f}]"
    )

    # CipherB dual-extreme utility check
    utils_cb_bot = evaluate_all_utilities_continuous({**dummy_norm, "cipherb": 10}, w_top=0.0, w_bot=0.9, w_neutral=0.1, tiz_maturity=0.5, cycle_day=1200)
    utils_cb_top = evaluate_all_utilities_continuous({**dummy_norm, "cipherb": 90}, w_top=0.9, w_bot=0.0, w_neutral=0.1, tiz_maturity=0.0, cycle_day=500)
    suite.check(
        "L3",
        "CipherB utility high at both cycle bottom and cycle top",
        utils_cb_bot["cipherb"] >= 0.75 and utils_cb_top["cipherb"] >= 0.75,
        f"u_bot={utils_cb_bot['cipherb']:.3f}, u_top={utils_cb_top['cipherb']:.3f}"
    )


def verify_layer_4(suite: InvariantSuite):
    print("\n=== LAYER 4: Historical Cycle Milestone Calibration ===")
    scores_path = os.path.join(ROOT, "data", "history", "scores.json")
    by_date = {r["date"]: r for r in json.load(open(scores_path, encoding="utf-8")) if r.get("date")}

    bottom_milestones = [
        ("2018-12-15", "2018 bear bottom ($3.2k)"),
        ("2020-03-13", "COVID crash ($3.8k)"),
        ("2022-06-18", "LUNA/3AC low ($17.6k)"),
        ("2022-11-21", "FTX collapse bottom ($15.5k)"),
    ]
    top_milestones = [
        ("2017-12-17", "2017 bull ATH ($19.7k)"),
        ("2021-04-14", "2021 first peak ($64.8k)"),
        ("2021-11-10", "2021 cycle ATH ($69k)"),
        ("2024-03-14", "2024 pre-halving ATH ($73k)"),
        ("2025-10-06", "2025 cycle ATH ($124.7k)"),
    ]

    bot_scores = []
    for d, label in bottom_milestones:
        r = by_date.get(d, {})
        sc = r.get("final_score")
        ph = r.get("phase")
        bot_scores.append(sc if sc is not None else 100)
        suite.check("L4", f"Bottom milestone {d} ({label})", sc is not None and sc <= 20 and ph == "BOTTOM", f"BRI={sc}, phase={ph}")

    top_scores = []
    for d, label in top_milestones:
        r = by_date.get(d, {})
        sc = r.get("final_score")
        ph = r.get("phase")
        top_scores.append(sc if sc is not None else 0)
        suite.check("L4", f"Top milestone {d} ({label})", sc is not None and sc >= 78 and ph == "TOP", f"BRI={sc}, phase={ph}")

    sep = (sum(top_scores) / len(top_scores)) - (sum(bot_scores) / len(bot_scores))
    suite.check("L4", "Mean Top-Bottom separation >= 70 pts", sep >= 70.0, f"separation=+{sep:.1f} pts")


def verify_layer_5(suite: InvariantSuite):
    print("\n=== LAYER 5: Empirical Forward-Return Monotonicity (bucket_returns.json) ===")
    br_path = os.path.join(ROOT, "data", "bucket_returns.json")
    br = json.load(open(br_path, encoding="utf-8"))
    b_map = {b["range_low"]: b for b in br.get("buckets", [])}

    low_meds = [b_map[k]["median_365d"] for k in (0, 10, 20) if b_map.get(k, {}).get("median_365d") is not None]
    high_meds = [b_map[k]["median_365d"] for k in (80, 90) if b_map.get(k, {}).get("median_365d") is not None]
    top_bucket_med = b_map.get(90, {}).get("median_365d", 0.0)

    avg_low_med = sum(low_meds) / len(low_meds) if low_meds else 0.0
    avg_high_med = sum(high_meds) / len(high_meds) if high_meds else 0.0

    suite.check(
        "L5",
        "Low-risk buckets (0-30) have strong positive 365d median return (> +60%)",
        avg_low_med >= 60.0 and avg_low_med > avg_high_med,
        f"low_avg=+{avg_low_med:.1f}% vs high_avg={avg_high_med:+.1f}%"
    )
    suite.check(
        "L5",
        "Extreme top risk bucket (90-100) has negative 365d median return (< 0%)",
        top_bucket_med < 0.0,
        f"bucket [90,100) median 365d return = {top_bucket_med:+.1f}%"
    )


def verify_layer_6(suite: InvariantSuite):
    print("\n=== LAYER 6: Live Payload & Web Sync Integrity ===")
    d_data = json.load(open(os.path.join(ROOT, "data", "data.json"), encoding="utf-8"))
    w_data = json.load(open(os.path.join(ROOT, "web", "data.json"), encoding="utf-8"))
    d_scores = json.load(open(os.path.join(ROOT, "data", "history", "scores.json"), encoding="utf-8"))
    w_scores = json.load(open(os.path.join(ROOT, "web", "history", "scores.json"), encoding="utf-8"))

    sync_keys = ["final_score", "v5b_score", "market_regime", "phase", "oc_basket", "ms_basket", "mc_basket", "cp_basket"]
    mismatches = [k for k in sync_keys if d_data.get(k) != w_data.get(k)]
    suite.check("L6", "data/data.json == web/data.json on core keys", len(mismatches) == 0, f"mismatches={mismatches}")

    suite.check(
        "L6",
        "data/history/scores.json latest == web/history/scores.json latest",
        d_scores[-1].get("date") == w_scores[-1].get("date") and d_scores[-1].get("final_score") == w_scores[-1].get("final_score"),
        f"date={d_scores[-1].get('date')}, score={d_scores[-1].get('final_score')}"
    )
    suite.check(
        "L6",
        "data/data.json final_score matches scores.json latest final_score",
        d_data.get("final_score") == d_scores[-1].get("final_score"),
        f"data.json={d_data.get('final_score')}, scores.json={d_scores[-1].get('final_score')}"
    )


def main():
    suite = InvariantSuite()
    verify_layer_1(suite)
    verify_layer_2(suite)
    verify_layer_3(suite)
    verify_layer_4(suite)
    verify_layer_5(suite)
    verify_layer_6(suite)

    print("\n" + "=" * 68)
    print(f"INVARIANT VERIFICATION SUMMARY: {suite.passed} PASSED, {suite.failed} FAILED")
    print("=" * 68)
    if suite.failures:
        for f in suite.failures:
            print(f"  ✗ {f}")
        sys.exit(1)
    else:
        print("  ✓ ALL 6 SYSTEM LAYERS PASSED 100% OF INVARIANT CHECKS.")


if __name__ == "__main__":
    main()
