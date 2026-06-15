#!/usr/bin/env python3
import sys
import os
import json
import math
import datetime

sys.path.insert(0, '.')

from tools.backtest import load_data
from tools.optimize_v3_relevance import get_btc_price_dict, build_precomputed_dataset

def get_orthogonal_basis(n):
    e = [1.0] * n
    norm_e = math.sqrt(n)
    u1 = [1.0 / norm_e] * n
    
    basis = []
    for i in range(n - 1):
        v = [0.0] * n
        v[i] = 1.0
        basis.append(v)
        
    all_vectors = [u1]
    for v in basis:
        for u in all_vectors:
            dot = sum(a * b for a, b in zip(v, u))
            v = [a - dot * b for a, b in zip(v, u)]
        norm = math.sqrt(sum(a * a for a in v))
        if norm > 1e-9:
            v = [a / norm for a in v]
            all_vectors.append(v)
            
    V = []
    for r in range(n):
        row = [all_vectors[c][r] for c in range(1, n)]
        V.append(row)
    return V

def invert_matrix(A):
    n = len(A)
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(A)]
    for i in range(n):
        pivot_row = i
        for r in range(i + 1, n):
            if abs(aug[r][i]) > abs(aug[pivot_row][i]):
                pivot_row = r
        if abs(aug[pivot_row][i]) < 1e-9:
            return None
        aug[i], aug[pivot_row] = aug[pivot_row], aug[i]
        factor = aug[i][i]
        aug[i] = [val / factor for val in aug[i]]
        for r in range(n):
            if r != i:
                factor = aug[r][i]
                aug[r] = [val_r - factor * val_i for val_r, val_i in zip(aug[r], aug[i])]
    return [row[n:] for row in aug]

def calculate_mahalanobis_for_row(row, covariance_data):
    norm = row['normalized']
    oc_keys = covariance_data['keys']
    
    # Filter active keys
    K = [k for k in oc_keys if norm.get(k) is not None]
    n = len(K)
    if n < 3:
        return None
        
    x_K = [norm[k] / 100.0 for k in K]
    
    # Sub-covariance matrix
    full_keys = covariance_data['keys']
    indices = [full_keys.index(k) for k in K]
    Sigma_K = []
    for r in indices:
        row_cov = [covariance_data['covariance'][r][c] for c in indices]
        Sigma_K.append(row_cov)
        
    # Orthogonal basis
    V_K = get_orthogonal_basis(n)
    
    # Project x_K to y
    y = []
    for col in range(n - 1):
        val = sum(V_K[row_idx][col] * x_K[row_idx] for row_idx in range(n))
        y.append(val)
        
    # Projected covariance Sigma_y = V_K^T * Sigma_K * V_K
    temp = []
    for r in range(n):
        row_temp = []
        for c in range(n - 1):
            val = sum(Sigma_K[r][i] * V_K[i][c] for i in range(n))
            row_temp.append(val)
        temp.append(row_temp)
        
    Sigma_y = []
    for r in range(n - 1):
        row_y = []
        for c in range(n - 1):
            val = sum(V_K[i][r] * temp[i][c] for i in range(n))
            row_y.append(val)
        Sigma_y.append(row_y)
        
    # Invert Sigma_y
    inv_Sigma_y = invert_matrix(Sigma_y)
    if not inv_Sigma_y:
        return None
        
    # D_M^2
    temp_y = []
    for r in range(n - 1):
        val = sum(inv_Sigma_y[r][c] * y[c] for c in range(n - 1))
        temp_y.append(val)
    dm_sq = sum(y[i] * temp_y[i] for i in range(n - 1))
    return math.sqrt(max(0.0, dm_sq))

def main():
    print("=== TESTING MAHALANOBIS DISTRIBUTION ===")
    
    # Load covariance
    cov_path = 'data/mahalanobis_covariance.json'
    if not os.path.exists(cov_path):
        print(f"Error: {cov_path} not found.")
        return
    with open(cov_path) as f:
        covariance_data = json.load(f)
        
    series = load_data()
    btc_price = get_btc_price_dict()
    
    # Load scores history
    scores_history = None
    scores_path = 'data/history/scores.json'
    if os.path.exists(scores_path):
        try:
            with open(scores_path, encoding='utf-8') as f:
                history_data = json.load(f)
                scores_history = [(r['date'], r.get('final_score')) for r in history_data if r.get('date')]
        except Exception:
            pass

    dataset = build_precomputed_dataset(series, btc_price, scores_history)
    
    dms = []
    for row in dataset:
        dm = calculate_mahalanobis_for_row(row, covariance_data)
        if dm is not None:
            dms.append(dm)
            
    print(f"Total days analyzed: {len(dms)}")
    if not dms:
        return
        
    dms.sort()
    n = len(dms)
    
    print("\nMahalanobis Distance Percentiles in History:")
    print(f"  Min   : {dms[0]:.4f}")
    print(f"  25%   : {dms[int(n*0.25)]:.4f}")
    print(f"  50%   : {dms[int(n*0.50)]:.4f} (Median)")
    print(f"  75%   : {dms[int(n*0.75)]:.4f}")
    print(f"  90%   : {dms[int(n*0.90)]:.4f}")
    print(f"  95%   : {dms[int(n*0.95)]:.4f}")
    print(f"  99%   : {dms[int(n*0.99)]:.4f}")
    print(f"  Max   : {dms[-1]:.4f}")

if __name__ == '__main__':
    main()
