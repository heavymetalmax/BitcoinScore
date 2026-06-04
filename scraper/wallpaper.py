#!/usr/bin/env python3
"""
Pexels wallpaper downloader.

Called after scoring is complete. Downloads one "real" (landscape / nature)
and one "abstract" (artwork / digital art) wallpaper that matches the current
risk-score category (low / neutral / high) and saves them to:

    web/wallpaper-real.jpg
    web/wallpaper-abstract.jpg

The frontend loadWallpaper() in spatial.html will prefer these local files
over the Unsplash fallback URLs.
"""
import os
import random
import requests

# ── Pexels search queries per category ───────────────────────────────────────

REAL_QUERIES = {
    'low': [
        'calm mountain lake misty',
        'peaceful green forest river',
        'serene valley sunrise fog',
        'tranquil alpine meadow',
    ],
    'neutral': [
        'rolling hills golden hour',
        'autumn forest path',
        'dramatic canyon landscape',
        'ocean waves cliffs sunset',
    ],
    'high': [
        'vibrant city night lights',
        'neon street cityscape night',
        'festival fireworks celebration',
        'night market glowing lights',
    ],
}

ABSTRACT_QUERIES = {
    'low': [
        'abstract teal blue fluid art',
        'cool dark gradient abstract',
        'abstract geometric dark blue',
        'dark abstract smooth waves',
    ],
    'neutral': [
        'abstract golden warm tones',
        'abstract beige brown artwork',
        'abstract marble texture warm',
        'abstract fluid art gold',
    ],
    'high': [
        'abstract neon pink red art',
        'vibrant hot pink abstract digital',
        'abstract red orange energy',
        'neon glowing abstract shapes',
    ],
}

# ── Pexels color filters mapping to category ─────────────────────────────────
# Pexels supports: red, orange, yellow, green, turquoise, blue, violet, pink, brown, black, gray, white
CATEGORY_COLORS = {
    'low': ['blue', 'turquoise', 'green'],
    'neutral': ['orange', 'yellow', 'brown', 'gray'],
    'high': ['red', 'pink', 'violet'],
}


def _score_to_category(score: float) -> str:
    if score is None:
        return 'neutral'
    if score <= 25:
        return 'low'
    if score >= 75:
        return 'high'
    return 'neutral'


from typing import Optional


def _fetch_pexels_photo(query: str, api_key: str, color: Optional[str] = None, orientation: str = 'landscape') -> Optional[bytes]:
    """Search Pexels for one photo and return its raw JPEG bytes, or None on failure."""
    url = 'https://api.pexels.com/v1/search'
    params = {
        'query': query,
        'per_page': 15,
        'orientation': orientation,
        'size': 'large',
    }
    if color:
        params['color'] = color
        
    headers = {'Authorization': api_key}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        photos = r.json().get('photos', [])
        if not photos:
            # Fallback without color filter if query is too specific
            if color:
                print(f'No results with color filter "{color}", trying without color filter...')
                params.pop('color')
                r = requests.get(url, params=params, headers=headers, timeout=15)
                r.raise_for_status()
                photos = r.json().get('photos', [])
                if not photos:
                    return None
            else:
                return None
        photo = random.choice(photos)
        # prefer large2x (2560px), fallback to large (1280px)
        img_url = photo.get('src', {}).get('large2x') or photo.get('src', {}).get('large')
        if not img_url:
            return None
        img_r = requests.get(img_url, timeout=30, stream=True)
        img_r.raise_for_status()
        return img_r.content
    except Exception as e:
        print(f'Pexels fetch error for query "{query}" (color: {color}): {e}')
        return None


def update_wallpapers(final_score: float, web_dir: str = 'web') -> bool:
    """
    Download one real and one abstract wallpaper from Pexels based on the
    current risk score. Saves to web/wallpaper-real.jpg and
    web/wallpaper-abstract.jpg. Returns True if both succeeded.
    """
    api_key = os.getenv('PEXELS_API_KEY', '').strip()
    if not api_key:
        print('PEXELS_API_KEY not set — skipping wallpaper update')
        return False

    category = _score_to_category(final_score)
    color_choice = random.choice(CATEGORY_COLORS[category])
    print(f'Wallpaper category: {category} (score={final_score}), color filter: {color_choice}')

    os.makedirs(web_dir, exist_ok=True)
    success = True

    # ── Real / nature ─────────────────────────────────────────────────────────
    real_query = random.choice(REAL_QUERIES[category])
    print(f'Fetching real wallpaper: "{real_query}" with color filter "{color_choice}" …')
    real_bytes = _fetch_pexels_photo(real_query, api_key, color=color_choice)
    if real_bytes:
        out_path = os.path.join(web_dir, 'wallpaper-real.jpg')
        with open(out_path, 'wb') as f:
            f.write(real_bytes)
        print(f'Saved {out_path} ({len(real_bytes) // 1024} KB)')
    else:
        print('Real wallpaper fetch failed')
        success = False

    # ── Abstract ──────────────────────────────────────────────────────────────
    abstract_query = random.choice(ABSTRACT_QUERIES[category])
    print(f'Fetching abstract wallpaper: "{abstract_query}" with color filter "{color_choice}" …')
    abstract_bytes = _fetch_pexels_photo(abstract_query, api_key, color=color_choice)
    if abstract_bytes:
        out_path = os.path.join(web_dir, 'wallpaper-abstract.jpg')
        with open(out_path, 'wb') as f:
            f.write(abstract_bytes)
        print(f'Saved {out_path} ({len(abstract_bytes) // 1024} KB)')
    else:
        print('Abstract wallpaper fetch failed')
        success = False

    return success
