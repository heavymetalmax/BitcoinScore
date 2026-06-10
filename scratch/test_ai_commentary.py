import os
import sys

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from scraper.gemini_commentary import generate_commentary

dummy_payload = {
    'final_score': 35,
    'onchain_score': 28,
    'tech_score': 42,
    'btc_price': 69420,
    'nupl': 0.42,
    'mvrv_z_score': 1.85,
    'fear_greed': 50,
    'metrics': {
        'nupl': {'value': 0.42},
        'mvrv': {'value': 1.85},
        'fear_greed': {'avg_7d': 50}
    }
}

dummy_slider_map = {
    'nupl': 30,
    'mvrv_z_score': 40,
    'fear_greed': 50
}

print("Testing generate_commentary with dummy payload...")
print(f"OPENAI_API_KEY present: {bool(os.environ.get('OPENAI_API_KEY'))}")
print(f"GEMINI_API_KEY present: {bool(os.environ.get('GEMINI_API_KEY'))}")

res = generate_commentary(dummy_payload, dummy_slider_map)
print("\nResult:")
print(res)
