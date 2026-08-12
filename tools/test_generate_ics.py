from tools.generate_ics import generate_ics


def test_calendar_event_contains_only_minimal_bri_title():
    calendar = generate_ics([{
        'date': '2026-08-12',
        'final_score': 31,
        'onchain_score': 28,
        'tech_score': 30,
        'btc_price': 63786,
    }])
    assert 'SUMMARY:BRI 31\r\n' in calendar
    assert 'DESCRIPTION:' not in calendar
    assert '[C28 | T30]' not in calendar
    assert '$63' not in calendar
