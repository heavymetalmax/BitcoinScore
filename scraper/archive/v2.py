"""V2 archived scorer — regime-based, TiZ, wave resonance.

Returns minimal dict for efficiency comparison with V3.
"""


def run(metrics: dict) -> dict | None:
    """Run V2 scorer. Returns comparison fields or None."""
    try:
        from scraper.scoring_v2 import compute_scores_v2
        s = compute_scores_v2(metrics)
        return {
            'onchain_score': s.get('onchain_score'),
            'tech_score':    s.get('tech_score'),
            'final_score':   s.get('final_score'),
            'regime':        s.get('regime'),
            'tiz_score':     s.get('tiz_score'),
        }
    except Exception:
        return None
