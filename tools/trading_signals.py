import os
import json
import datetime

def calculate_active_signal(score_history):
    """
    Runs a state machine over the chronological score history to calculate the current strategy state.
    
    Strategy:
      - Entry: Buy when ML Score <= 25 (transition to HOLD_BTC).
      - Trigger: When in HOLD_BTC, if ML Score >= 60, activate Trailing Stop (transition to TRAILING_EXIT).
      - Exit: When in TRAILING_EXIT, track the peak score observed since activation. If the score falls
              by >= 5 points from the peak, sell (transition to CASH).
    """
    state = "CASH"  # CASH | HOLD_BTC | TRAILING_EXIT
    peak_score = 0
    last_action = None  # BUY | SELL
    last_action_date = None
    last_action_price = None
    trailing_trigger = None

    for entry in score_history:
        date = entry.get('date')
        score = entry.get('score')
        price = entry.get('price')
        if score is None or price is None:
            continue

        if state == "CASH":
            if score <= 25:
                state = "HOLD_BTC"
                last_action = "BUY"
                last_action_date = date
                last_action_price = price
                peak_score = 0
                trailing_trigger = None
        elif state == "HOLD_BTC":
            if score >= 60:
                state = "TRAILING_EXIT"
                peak_score = score
                trailing_trigger = peak_score - 5
        elif state == "TRAILING_EXIT":
            if score > peak_score:
                peak_score = score
                trailing_trigger = peak_score - 5
            
            if score <= trailing_trigger:
                state = "CASH"
                last_action = "SELL"
                last_action_date = date
                last_action_price = price
                trailing_trigger = None
                peak_score = 0

    return {
        'state': state,
        'last_action': last_action,
        'last_action_date': last_action_date,
        'last_action_price': last_action_price,
        'trailing_trigger_score': trailing_trigger,
        'peak_score_in_run': peak_score
    }
