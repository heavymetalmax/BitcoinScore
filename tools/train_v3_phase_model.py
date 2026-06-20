"""Train ML phase classifier (Logistic Regression) on historical wave vectors.

Classes:
  0: BOTTOM  (dates close to cycle bottoms)
  1: NEUTRAL (mid-cycle, early trend, standard consolidation)
  2: TOP     (dates close to cycle peaks)

Features:
  22-dim wave vector = [11 metric scores] + [11 metric deltas]

Output:
  data/v3_phase_model.pkl
"""
import sys, os, json, pickle, datetime, random, math
sys.path.insert(0, '.')

import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit, cross_val_score

from tools.backtest import load_data, compute_at

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TOP_DATES = [
    '2021-04-14',   # Spring 2021 ATH
    '2021-11-10',   # Nov 2021 ATH
    '2024-03-14',   # 2024 cycle ATH
    '2025-07-17',   # CB weekly peak
    '2025-09-29',   # Price ATH ($129k)
]

BOTTOM_DATES = [
    '2018-12-15',   # 2018 cycle bottom
    '2020-03-13',   # COVID crash
    '2022-06-18',   # Capitulation
    '2022-11-21',   # FTX bottom
    '2026-06-04',   # 2026 accumulation zone (BTC ~$64K, score 28-29)
]

METRIC_ORDER = [
    'nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'mayer_multiple',
    'asopr', 'etf_flows', 'cipherb_weekly', 'cipherb_daily', 'fear_greed',
    'm2_yoy',
]

METRIC_LOOKBACK = {
    'nupl':               30,
    'mvrv_z_score':       30,
    'rhodl_ratio':        30,
    'cvdd_ratio':         30,
    'mayer_multiple':     30,
    'asopr':              14,
    'etf_flows':          14,
    'cipherb_weekly':     14,
    'cipherb_daily':       7,
    'fear_greed':          7,
    'm2_yoy':             60,
}

# Cache for compute_at scores to avoid redundant roll-percentile calculations
_COMPUTE_CACHE = {}


def dates_around(date_str, window=14):
    """Yield dates in [date - window, date + window]."""
    center = datetime.date.fromisoformat(date_str)
    for delta in range(-window, window + 1):
        yield (center + datetime.timedelta(days=delta)).isoformat()


def get_cached_scores(td, series):
    """Retrieve scores for a date, caching results to avoid repeating calculations."""
    d_str = td.isoformat()
    if d_str not in _COMPUTE_CACHE:
        _COMPUTE_CACHE[d_str] = compute_at(td, series)['scores']
    return _COMPUTE_CACHE[d_str]


def build_wave_vector(date_str, series):
    """Compute the 22-dimensional wave vector for a given date using cached scores."""
    td = datetime.date.fromisoformat(date_str)
    scores = get_cached_scores(td, series)

    # prev scores
    prev = {}
    for metric, lb in METRIC_LOOKBACK.items():
        prev_td = td - datetime.timedelta(days=lb)
        prev[metric] = get_cached_scores(prev_td, series).get(metric)

    vec = []
    # 1. Position scores (11 dims)
    for m in METRIC_ORDER:
        vec.append(scores.get(m))
    # 2. Velocity deltas (11 dims)
    for m in METRIC_ORDER:
        s_now = scores.get(m)
        s_prev = prev.get(m)
        if s_now is not None and s_prev is not None:
            vec.append(s_now - s_prev)
        else:
            vec.append(None)

    n_present = sum(1 for v in vec[:11] if v is not None)
    return vec if n_present >= 6 else None


def main():
    print("Loading historical data series...")
    series = load_data()

    # Get list of all available dates from NUPL (longest history)
    all_dates = sorted(d for d, _ in series.get('nupl', []))
    all_dates = [d for d in all_dates if d >= '2016-01-01']  # Restrict to post-2016 for data density

    print("Collecting training samples...")
    X_raw, y = [], []
    date_labels = []  # (date_str, label) parallel to X_raw

    # 1. TOP dates (Class 2)
    top_dates_set = set()
    for center in TOP_DATES:
        for d in dates_around(center, window=14):
            top_dates_set.add(d)

    # 2. BOTTOM dates (Class 0)
    bot_dates_set = set()
    for center in BOTTOM_DATES:
        for d in dates_around(center, window=14):
            bot_dates_set.add(d)

    # 3. NEUTRAL dates (Class 1)
    # Exclude any dates within 60 days of any top or bottom date
    excluded_dates = set()
    for center in TOP_DATES + BOTTOM_DATES:
        for d in dates_around(center, window=60):
            excluded_dates.add(d)

    neutral_candidates = [d for d in all_dates if d not in excluded_dates]
    
    # Deterministically sample neutral candidates to keep classes reasonably balanced
    random.seed(42)
    sampled_neutrals = random.sample(neutral_candidates, min(len(neutral_candidates), 180))

    # Process all classes
    classes = [
        (bot_dates_set, 0, "BOTTOM"),
        (sampled_neutrals, 1, "NEUTRAL"),
        (top_dates_set, 2, "TOP")
    ]

    for dates, label, name in classes:
        count = 0
        for d in sorted(dates):
            if d not in all_dates:
                continue
            v = build_wave_vector(d, series)
            if v is not None:
                X_raw.append(v)
                y.append(label)
                date_labels.append((d, label))
                count += 1
        print(f"  Class {label} ({name:<7}): {count} vectors")

    X = np.array(X_raw, dtype=float)
    y = np.array(y, dtype=int)

    print(f"\nTotal Dataset: {X.shape[0]} samples × {X.shape[1]} features")

    # Define training pipeline
    clf = Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('scaler', StandardScaler()),
        ('lr', LogisticRegression(
            C=1.0,
            class_weight='balanced',
            random_state=42,
            max_iter=1000
        ))
    ])

    # TimeSeriesSplit cross-validation to check generalizeability
    tscv = TimeSeriesSplit(n_splits=3)
    cv_scores = cross_val_score(clf, X, y, cv=tscv, scoring='accuracy')
    print(f"\nTimeSeriesSplit CV Accuracy: {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")

    # Train on all data
    clf.fit(X, y)
    print("\nModel trained successfully!")

    # Display coefficients for TOP class to inspect feature influence
    lr = clf.named_steps['lr']
    top_coefs = lr.coef_[2]  # Coefficients for Class 2 (TOP)
    feature_names = METRIC_ORDER + [f"{m}_delta" for m in METRIC_ORDER]
    print("\nFeature Coefficients for TOP class (higher = more likely to predict TOP):")
    pairs = sorted(zip(feature_names, top_coefs), key=lambda x: -x[1])
    for name, coef in pairs:
        bar = ('█' * int(abs(coef) * 10)) if coef > 0 else ('░' * int(abs(coef) * 10))
        sign = '+' if coef > 0 else '-'
        print(f"  {name:<25} {sign}{abs(coef):.3f}  {bar}")

    # ── Stage 2: Conviction weights ────────────────────────────────────────────
    # Train binary LogRegs on [w_bot, tiz_maturity, oc_coherence] → BOTTOM conviction
    # and [w_top, oc_coherence] → TOP conviction using in-memory clf (no pkl re-read).
    print("\nTraining Stage 2: conviction weights...")

    from scraper.tiz import adaptive_calibration as _adapt_cal

    _phase_history = []
    _scores_path = os.path.join(ROOT, 'data', 'history', 'scores.json')
    if os.path.exists(_scores_path):
        with open(_scores_path) as _f:
            _sh = json.load(_f)
        _phase_history = sorted(
            (r['date'], r.get('phase'), r.get('final_score'))
            for r in _sh if r.get('date')
        )

    def _tiz_at(date_str):
        cutoff = (datetime.date.fromisoformat(date_str) - datetime.timedelta(days=180)).isoformat()
        in_zone = [
            s for d, p, s in _phase_history
            if cutoff <= d <= date_str
            and ((p == 'BOTTOM') if p is not None else (s is not None and s <= 40))
        ]
        if not in_zone:
            return 0.0
        min_s = min((s for s in in_zone if s is not None), default=26)
        return min(1.0, len(in_zone) / _adapt_cal(int(min_s)))

    # OC weights mirror _oc_coherence() in scoring.py
    _OC_IDX = {0: 0.30, 1: 0.20, 2: 0.20, 3: 0.15, 5: 0.15}  # METRIC_ORDER → OC_WEIGHTS

    def _oc_coh_from_vec(vec):
        pairs = [
            (vec[i], w) for i, w in _OC_IDX.items()
            if i < len(vec) and vec[i] is not None and not math.isnan(float(vec[i]))
        ]
        if len(pairs) < 3:
            return 0.5
        total_w = sum(w for _, w in pairs)
        mean_v = sum((v / 100.0) * w for v, w in pairs) / total_w
        var = sum(w * (v / 100.0 - mean_v) ** 2 for v, w in pairs) / total_w
        return max(0.0, 1.0 - var ** 0.5 / 0.289)

    _imp = clf.named_steps['imputer']
    _sc = clf.named_steps['scaler']
    _lr = clf.named_steps['lr']

    X2_bot, y2_bot = [], []
    X2_top, y2_top = [], []

    for (d, lbl), raw_vec in zip(date_labels, X_raw):
        v_arr = np.array(raw_vec, dtype=float)
        v_imp = _imp.transform([np.nan_to_num(v_arr, nan=np.nan)])[0]
        v_sc = _sc.transform([v_imp])[0]
        proba = _lr.predict_proba([v_sc])[0]  # [P(BOT), P(NEU), P(TOP)]
        w_bot_s2 = float(proba[0])
        w_top_s2 = float(proba[2])
        tiz = _tiz_at(d)
        coh = _oc_coh_from_vec(raw_vec)
        X2_bot.append([w_bot_s2, tiz, coh])
        y2_bot.append(1 if lbl == 0 else 0)
        X2_top.append([w_top_s2, coh])
        y2_top.append(1 if lbl == 2 else 0)

    from sklearn.linear_model import LogisticRegression as _LR2
    s2_bot_clf = _LR2(C=1.0, class_weight='balanced', random_state=42, max_iter=500).fit(
        np.array(X2_bot, dtype=float), np.array(y2_bot)
    )
    s2_top_clf = _LR2(C=1.0, class_weight='balanced', random_state=42, max_iter=500).fit(
        np.array(X2_top, dtype=float), np.array(y2_top)
    )

    s2b_coef = s2_bot_clf.coef_[0].tolist()
    s2b_int = float(s2_bot_clf.intercept_[0])
    s2t_coef = s2_top_clf.coef_[0].tolist()
    s2t_int = float(s2_top_clf.intercept_[0])

    print(f"  BOTTOM coef=[w_bot:{s2b_coef[0]:.3f}, tiz:{s2b_coef[1]:.3f}, coh:{s2b_coef[2]:.3f}] intercept={s2b_int:.3f}")
    print(f"  TOP    coef=[w_top:{s2t_coef[0]:.3f}, coh:{s2t_coef[1]:.3f}] intercept={s2t_int:.3f}")

    def _sig(x):
        return 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, x))))

    print("\nStage 2 conviction check:")
    _dl_lookup = {(d, l): rv for (d, l), rv in zip(date_labels, X_raw)}
    for chk_d, chk_l, chk_name in [
        ('2026-06-04', 0, 'BOTTOM'), ('2022-11-21', 0, 'BOTTOM'), ('2018-12-15', 0, 'BOTTOM'),
        ('2021-11-10', 2, 'TOP'),   ('2024-03-14', 2, 'TOP'),
    ]:
        rv = _dl_lookup.get((chk_d, chk_l))
        if rv is None:
            continue
        v_i = _imp.transform([np.nan_to_num(np.array(rv, dtype=float), nan=np.nan)])[0]
        v_s = _sc.transform([v_i])[0]
        pr = _lr.predict_proba([v_s])[0]
        wb = float(pr[0]); wt = float(pr[2])
        tiz_c = _tiz_at(chk_d); coh_c = _oc_coh_from_vec(rv)
        if chk_l == 0:
            logit = sum(c * f for c, f in zip(s2b_coef, [wb, tiz_c, coh_c])) + s2b_int
            print(f"  {chk_d} {chk_name}: w_bot={wb:.3f} tiz={tiz_c:.3f} coh={coh_c:.3f} → conviction={_sig(logit):.3f}")
        else:
            logit = sum(c * f for c, f in zip(s2t_coef, [wt, coh_c])) + s2t_int
            print(f"  {chk_d} {chk_name}: w_top={wt:.3f} coh={coh_c:.3f} → conviction={_sig(logit):.3f}")

    # Export model pipeline and metadata
    model_data = {
        'pipeline': clf,
        'feature_names': feature_names,
        'n_samples': int(X.shape[0]),
        'cv_accuracy': float(cv_scores.mean()),
        'trained_at': datetime.datetime.utcnow().isoformat() + "Z",
        'stage2': {
            'bottom': {
                'coef': s2b_coef,
                'intercept': s2b_int,
                'features': ['w_bot', 'tiz_maturity', 'oc_coherence'],
            },
            'top': {
                'coef': s2t_coef,
                'intercept': s2t_int,
                'features': ['w_top', 'oc_coherence'],
            },
        },
    }

    dest_path = os.path.join(ROOT, 'data', 'v3_phase_model.pkl')
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, 'wb') as f:
        pickle.dump(model_data, f)
    print(f"\nSaved phase model → {dest_path}")


if __name__ == '__main__':
    main()
