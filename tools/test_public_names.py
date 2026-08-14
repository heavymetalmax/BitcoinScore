from scraper.scoring_pipeline import sync_public_names


def test_public_names_follow_authoritative_score():
    payload = {'final_score': 30, 'bri_score': 30}
    scores = {
        'final_score': 31,
        'v5b_score': 13.2,
        'v5_score': None,
        'phase': 'NEUTRAL',
        'w_bot': 0.55,
        'w_neutral': 0.45,
        'w_top': 0.0,
    }
    sync_public_names(payload, scores)
    assert payload['bri_score'] == 31
    assert payload['forward_risk'] == 13.2
    assert payload['market_context']['score'] == 31
