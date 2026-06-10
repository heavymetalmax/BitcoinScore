import os
import sys
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

openai_key = os.environ.get('OPENAI_API_KEY')
gemini_key = os.environ.get('GEMINI_API_KEY')

print("="*60)
print("Checking OpenAI Models...")
print("="*60)
if not openai_key:
    # Try getting the gpt_api key if it was loaded locally
    openai_key = os.environ.get('gpt_api')

if openai_key:
    url = "https://api.openai.com/v1/models"
    headers = {"Authorization": f"Bearer {openai_key}"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        models = resp.json().get('data', [])
        # Sort and filter to show common text models
        model_names = sorted([m['id'] for m in models])
        print(f"Total OpenAI models found: {len(model_names)}")
        print("\nCommon Text & Reasoning Models:")
        for name in model_names:
            if any(keyword in name for keyword in ['gpt', 'o1', 'o3', 'davinci', 'curie']):
                print(f" - {name}")
    except Exception as e:
        print(f"Error fetching OpenAI models: {e}")
else:
    print("OPENAI_API_KEY (or gpt_api) not set.")

print("\n" + "="*60)
print("Checking Google Gemini Models...")
print("="*60)
if gemini_key:
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        models = resp.json().get('models', [])
        print(f"Total Gemini models found: {len(models)}")
        for m in sorted(models, key=lambda x: x.get('name', '')):
            name = m.get('name', '').replace('models/', '')
            supported_methods = m.get('supportedGenerationMethods', [])
            if 'generateContent' in supported_methods:
                desc = m.get('description', '')
                print(f" - {name} ({m.get('displayName')})")
    except Exception as e:
        print(f"Error fetching Gemini models: {e}")
else:
    print("GEMINI_API_KEY not set.")
print("="*60)
