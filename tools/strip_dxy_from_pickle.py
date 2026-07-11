#!/usr/bin/env python3
"""Remove 'dxy' from v3_phase_model.pkl metric_relevance.

After removing DXY from TECH_GROUP scoring, the pickle's metric_relevance['dxy']
would be re-installed into RELEVANCE_PROFILES on every restart via load_relevance_weights().
This one-time script strips it so the entry stays removed.

Usage:
    python tools/strip_dxy_from_pickle.py
"""
import sys, os, pickle

sys.path.insert(0, '.')

_PICKLE_PATH = 'data/v3_phase_model.pkl'


def main():
    if not os.path.exists(_PICKLE_PATH):
        print(f'No pickle at {_PICKLE_PATH} — nothing to do.')
        return

    try:
        import tools.train_v3_hmm_model  # noqa: F401 — registers HMMPhaseClassifier for pickle
    except Exception:
        pass

    with open(_PICKLE_PATH, 'rb') as f:
        model_data = pickle.load(f)

    relevance = model_data.get('metric_relevance', {})
    if 'dxy' not in relevance:
        print("'dxy' not in metric_relevance — nothing to strip.")
        return

    del relevance['dxy']
    model_data['metric_relevance'] = relevance

    with open(_PICKLE_PATH, 'wb') as f:
        pickle.dump(model_data, f)

    print(f"Stripped 'dxy' from metric_relevance in {_PICKLE_PATH}.")
    print(f"Remaining keys: {sorted(relevance.keys())}")


if __name__ == '__main__':
    main()
