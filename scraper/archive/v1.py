"""V1 archived scorer — fixed weights, no TiZ, no phase model.

Returns minimal dict for efficiency comparison with V3.
"""


def run(metrics: dict) -> dict | None:
    """Run V1 scorer. Returns {'onchain_score', 'tech_score', 'final_score'} or None."""
    try:
        from scraper.scoring import compute_scores, build_slider_map
        scores = compute_scores(metrics)
        return {
            'onchain_score': scores.get('onchain_score'),
            'tech_score':    scores.get('tech_score'),
            'final_score':   scores.get('final_score'),
        }
    except Exception:
        return None
