#!/usr/bin/env python3
import sys
import os
import json
import pickle
import datetime
import math
import numpy as np

sys.path.insert(0, '.')

from sklearn.base import BaseEstimator
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

from tools.backtest import load_data, compute_at
from scraper.scoring_v3 import BOTTOM_DATES, TOP_DATES

METRIC_ORDER = [
    'nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'mayer_multiple',
    'asopr', 'etf_flows', 'cipherb_weekly', 'cipherb_daily', 'fear_greed',
    'm2_yoy',
]

CORE_METRICS = ['nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'mayer_multiple', 'cipherb_weekly']
FEATURE_INDICES = [METRIC_ORDER.index(m) for m in CORE_METRICS]

CACHE_PATH = 'data/v3_hmm_state_cache.json'

class HMMPhaseClassifier(BaseEstimator):
    def __init__(self, means, vars, trans, pi, feature_indices):
        self.means = np.array(means)          # shape (3, 6)
        self.vars = np.array(vars)            # shape (3, 6)
        self.trans = np.array(trans)          # shape (3, 3)
        self.pi = np.array(pi)                # shape (3,)
        self.feature_indices = feature_indices

    def _log_gaussian_pdf(self, x, mean, var):
        # log P(x) = -0.5 * sum(log(2*pi*var) + (x - mean)^2 / var)
        return -0.5 * np.sum(np.log(2 * np.pi * var) + ((x - mean) ** 2) / var)

    def _forward_step(self, x, alpha_prev):
        emissions = []
        for j in range(3):
            log_prob = self._log_gaussian_pdf(x, self.means[j], self.vars[j])
            emissions.append(np.exp(log_prob))
        emissions = np.array(emissions)
        
        if np.all(emissions == 0):
            emissions = np.array([1.0/3.0]*3)
            
        alpha = np.zeros(3)
        for j in range(3):
            alpha[j] = emissions[j] * np.sum(alpha_prev * self.trans[:, j])
            
        alpha_sum = np.sum(alpha)
        if alpha_sum > 0:
            alpha = alpha / alpha_sum
        else:
            alpha = np.array([1.0/3.0]*3)
        return alpha

    def _get_current_date(self):
        try:
            import inspect
            for frame_record in inspect.stack():
                locs = frame_record.frame.f_locals
                if 'target_date' in locs:
                    td = locs['target_date']
                    if isinstance(td, str):
                        return td[:10]
                    elif hasattr(td, 'isoformat'):
                        return td.isoformat()[:10]
        except Exception:
            pass
        return datetime.date.today().isoformat()

    def predict_proba(self, X):
        # Ensure input is 2D numpy array
        X_arr = np.array(X, dtype=float)
        
        # Extract the 6 core scaled features
        X_f = X_arr[:, self.feature_indices]
        
        M = X_arr.shape[0]
        if M > 1:
            # Sequence mode (e.g. bulk training or fit)
            probs = np.zeros((M, 3))
            alpha = self.pi.copy()
            # First step
            emissions = []
            for j in range(3):
                log_prob = self._log_gaussian_pdf(X_f[0], self.means[j], self.vars[j])
                emissions.append(np.exp(log_prob))
            emissions = np.array(emissions)
            alpha = alpha * emissions
            alpha_sum = np.sum(alpha)
            if alpha_sum > 0:
                alpha = alpha / alpha_sum
            else:
                alpha = np.array([1.0/3.0]*3)
            probs[0] = alpha
            
            for t in range(1, M):
                alpha = self._forward_step(X_f[t], alpha)
                probs[t] = alpha
            return probs
        else:
            # Single-step mode (daily scraper run)
            date_str = self._get_current_date()
            
            # Load cache
            cache = {}
            if os.path.exists(CACHE_PATH):
                try:
                    with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                        cache = json.load(f)
                except Exception:
                    pass
            
            # Find yesterday's date
            try:
                curr_dt = datetime.date.fromisoformat(date_str)
                yesterday_str = (curr_dt - datetime.timedelta(days=1)).isoformat()
            except Exception:
                yesterday_str = None
                
            # Retrieve yesterday's probabilities, fallback to pi
            alpha_prev = None
            if yesterday_str and yesterday_str in cache:
                alpha_prev = np.array(cache[yesterday_str])
            else:
                # Fallback: find the closest past date in cache
                sorted_keys = sorted([k for k in cache.keys() if k < date_str])
                if sorted_keys:
                    alpha_prev = np.array(cache[sorted_keys[-1]])
                    
            if alpha_prev is None:
                alpha_prev = self.pi.copy()
                
            # Perform single forward step
            alpha_curr = self._forward_step(X_f[0], alpha_prev)
            
            # Save today's probabilities to cache
            cache[date_str] = [float(v) for v in alpha_curr]
            try:
                with open(CACHE_PATH, 'w', encoding='utf-8') as f:
                    json.dump(cache, f, indent=2)
            except Exception:
                pass
                
            return np.array([alpha_curr])

    def fit(self, X, y=None):
        self.is_fitted_ = True
        return self

def dates_around(date_str, window=14):
    center = datetime.date.fromisoformat(date_str)
    for delta in range(-window, window + 1):
        yield (center + datetime.timedelta(days=delta)).isoformat()

def main():
    print("=== TRAINING SUPERVISED HMM PHASE CLASSIFIER (EXPERIMENT 2) ===")
    series = load_data()
    all_dates = sorted(d for d, _ in series.get('nupl', []))
    all_dates = [d for d in all_dates if d >= '2016-01-01']
    
    # 1. Setup chronological state labels
    top_dates_set = set()
    for center in TOP_DATES:
        for d in dates_around(center.isoformat(), window=60): # 60 days around tops
            top_dates_set.add(d)

    bot_dates_set = set()
    for center in BOTTOM_DATES:
        for d in dates_around(center.isoformat(), window=90): # 90 days around bottoms
            bot_dates_set.add(d)

    labels = []
    chronological_dates = []
    
    # Precalculate scores and feature matrix X
    X_raw = []
    score_cache = {}
    print("Loading normalized scores...")
    for idx, d in enumerate(all_dates):
        td = datetime.date.fromisoformat(d)
        scores = compute_at(td, series)['scores']
        score_cache[d] = scores
        
        # 22-dimensional wave vector = [11 metrics] + [11 deltas]
        # (Mirroring exact feature structure expected by scoring_v3)
        vec = []
        for m in METRIC_ORDER:
            vec.append(scores.get(m))
        for m in METRIC_ORDER:
            # We don't strictly need delta values for the HMM emission, 
            # but we need to include them to keep the 22-dim shape consistent.
            vec.append(0.0) 
            
        X_raw.append(vec)
        
        if d in top_dates_set:
            labels.append(2) # TOP
        elif d in bot_dates_set:
            labels.append(0) # BOTTOM
        else:
            labels.append(1) # NEUTRAL
        chronological_dates.append(d)

    X = np.array(X_raw, dtype=float)
    y = np.array(labels, dtype=int)
    
    print(f"Dataset shape: {X.shape[0]} dates × {X.shape[1]} features")

    # 2. Impute and Scale 22-dim features
    imputer = SimpleImputer(strategy='median')
    scaler = StandardScaler()
    
    X_imputed = imputer.fit_transform(X)
    X_scaled = scaler.fit_transform(X_imputed)

    # 3. Calculate transitions A and initial state distribution pi
    A = np.zeros((3, 3))
    for t in range(len(y) - 1):
        A[y[t]][y[t+1]] += 1.0
        
    for r in range(3):
        row_sum = np.sum(A[r])
        if row_sum > 0:
            A[r] /= row_sum
        else:
            A[r] = np.array([1.0/3.0]*3)
            
    pi = np.zeros(3)
    for state in (0, 1, 2):
        pi[state] = np.sum(y == state)
    pi /= np.sum(pi)

    # 4. Extract core metrics and calculate emission parameters on scaled features
    X_scaled_core = X_scaled[:, FEATURE_INDICES]
    means = np.zeros((3, len(FEATURE_INDICES)))
    vars = np.zeros((3, len(FEATURE_INDICES)))
    
    for state in (0, 1, 2):
        state_mask = (y == state)
        state_features = X_scaled_core[state_mask]
        
        means[state] = np.mean(state_features, axis=0)
        vars[state] = np.var(state_features, axis=0)
        # Enforce minimum variance to prevent divide-by-zero
        vars[state] = np.clip(vars[state], a_min=1e-3, a_max=None)

    print("\nHMM Parameters Fit:")
    print("Transition Matrix:")
    print(A)
    print("Initial State Probabilities:")
    print(pi)
    print("Core metrics indices:", FEATURE_INDICES)

    # 5. Build and package pipeline
    hmm_clf = HMMPhaseClassifier(
        means=means,
        vars=vars,
        trans=A,
        pi=pi,
        feature_indices=FEATURE_INDICES
    )
    
    pipeline = Pipeline([
        ('imputer', imputer),
        ('scaler', scaler),
        ('hmm', hmm_clf)
    ])
    # Call fit() so the Pipeline is marked as fitted by sklearn's check_is_fitted.
    # imputer/scaler are already fit from above; this just records pipeline state.
    pipeline.fit(X, y)

    # 6. Optimize relevance weights and embed in pickle
    print("\n=== OPTIMIZING RELEVANCE WEIGHTS (IC-initialized) ===")
    metric_relevance = None
    try:
        from tools.optimize_v3_relevance import (
            build_precomputed_dataset, optimize_relevance_weights, get_btc_price_dict
        )
        from tools.compute_ic_profiles import (
            compute_ic_profiles, ic_profiles_to_bounds, print_ic_comparison
        )
        from scraper.utility_evaluator import RELEVANCE_PROFILES

        btc_price = get_btc_price_dict()
        scores_history = None
        scores_path = 'data/history/scores.json'
        if os.path.exists(scores_path):
            try:
                with open(scores_path, encoding='utf-8') as f:
                    history_data = json.load(f)
                    scores_history = [
                        (r['date'], r.get('final_score'), r.get('phase'), r.get('w_bot'))
                        for r in history_data if r.get('date')
                    ]
            except Exception as e:
                print(f"Warning: could not load scores history: {e}")

        skip_optimize = os.environ.get('SKIP_RELEVANCE_OPTIMIZE') == '1'
        if skip_optimize:
            print("Skipping relevance optimization (SKIP_RELEVANCE_OPTIMIZE=1).")
            metric_relevance = {k: dict(v) for k, v in RELEVANCE_PROFILES.items()}
        else:
            dataset = build_precomputed_dataset(series, btc_price, scores_history)
            if dataset:
                # Step 1: IC-based initialization — replaces hardcoded RELEVANCE_PROFILES
                print("\n--- Step 1: Computing IC-based initial weights ---")
                ic_profiles = compute_ic_profiles(dataset, verbose=True)
                ic_bounds   = ic_profiles_to_bounds(ic_profiles, slack=0.30)
                print_ic_comparison(ic_profiles, RELEVANCE_PROFILES)

                # Step 2: Coordinate descent starting from IC weights, bounded by IC bounds
                # L2 regularisation pulls toward IC prior (not hardcoded defaults)
                print("\n--- Step 2: Coordinate descent refinement ---")
                optimized, final_loss = optimize_relevance_weights(
                    dataset,
                    initial_profiles=ic_profiles,
                    l2_lambda=50.0,
                    ic_bounds=ic_bounds,
                )
                metric_relevance = optimized
                print(f"Relevance optimization complete. Loss: {final_loss:.2f}")
            else:
                print("Warning: empty dataset — keeping IC-only weights (no coord descent).")
                ic_profiles = compute_ic_profiles([], verbose=False)
                metric_relevance = ic_profiles if ic_profiles else {k: dict(v) for k, v in RELEVANCE_PROFILES.items()}
    except Exception as e:
        print(f"Warning: relevance optimization failed ({e}) — keeping defaults.")
        from scraper.utility_evaluator import RELEVANCE_PROFILES
        metric_relevance = {k: dict(v) for k, v in RELEVANCE_PROFILES.items()}

    # 7. Save model pickle (pipeline + learned relevance weights = single source of truth)
    output_path = 'data/v3_phase_model.pkl'
    model_data = {
        'pipeline': pipeline,
        'metric_relevance': metric_relevance,
        'metadata': {
            'trained_at': datetime.datetime.now().isoformat(),
            'model_type': 'Supervised Gaussian HMM (3-states)',
            'relevance_method': 'ic_initialized_coordinate_descent',
            'ic_alpha': 6.0,
            'phase_horizons': {'BOTTOM': 365, 'NEUTRAL': 270, 'TOP': 180},
        }
    }

    with open(output_path, 'wb') as f:
        pickle.dump(model_data, f)

    print(f"\nSuccessfully saved HMM phase model to {output_path}!")

    # Reset cache file to force recalculation from scratch
    if os.path.exists(CACHE_PATH):
        try:
            os.remove(CACHE_PATH)
            print("Cleared historical state cache file.")
        except Exception:
            pass

if __name__ == '__main__':
    main()
