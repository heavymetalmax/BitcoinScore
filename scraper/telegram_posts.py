"""Fetch latest posts from public Telegram channels for AI commentary context.

Uses Telegram's public, unauthenticated web preview (https://t.me/s/<channel>) —
no bot token or account login required. Same source RSS-bridge style tools scrape.
"""
import json
import logging
import os

from curl_cffi import requests
from lxml import html

logger = logging.getLogger(__name__)

CHANNELS = ['finstop', 'khtrader']
_SEEN_PATH = 'data/history/telegram_seen.json'
_MAX_POSTS_PER_CHANNEL = 3
_MAX_TEXT_LEN = 400


def _fetch_channel_html(channel):
    url = f'https://t.me/s/{channel}'
    resp = requests.get(url, impersonate='chrome', timeout=20)
    if resp.status_code != 200:
        logger.error(f'Telegram fetch failed for {channel}: status {resp.status_code}')
        return None
    return resp.text


def _parse_posts(raw_html):
    tree = html.fromstring(raw_html)
    posts = []
    for wrap in tree.xpath('//div[contains(@class,"tgme_widget_message") and @data-post]'):
        post_id = wrap.get('data-post') or ''
        if '/' not in post_id:
            continue
        try:
            msg_num = int(post_id.rsplit('/', 1)[1])
        except ValueError:
            continue
        text_nodes = wrap.xpath('.//div[contains(@class,"tgme_widget_message_text")]')
        if not text_nodes:
            continue  # media-only post, no text
        text = text_nodes[0].text_content().strip()
        if not text:
            continue
        time_nodes = wrap.xpath('.//time[@datetime]')
        ts = time_nodes[0].get('datetime') if time_nodes else None
        posts.append({'id': msg_num, 'text': text[:_MAX_TEXT_LEN], 'date': ts})
    posts.sort(key=lambda p: p['id'])
    return posts


def _load_seen():
    if os.path.exists(_SEEN_PATH):
        try:
            with open(_SEEN_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_seen(seen):
    os.makedirs(os.path.dirname(_SEEN_PATH), exist_ok=True)
    with open(_SEEN_PATH, 'w', encoding='utf-8') as f:
        json.dump(seen, f, indent=2)


def get_telegram_posts(channels=None):
    """Return {channel: [new post dicts]} for posts not seen on a previous run.

    Each post dict is {'id': int, 'text': str, 'date': str|None}. Up to
    _MAX_POSTS_PER_CHANNEL most recent new posts are returned per channel.
    """
    channels = channels or CHANNELS
    seen = _load_seen()
    new_by_channel = {}

    for channel in channels:
        try:
            raw = _fetch_channel_html(channel)
            if raw is None:
                continue
            posts = _parse_posts(raw)
            if not posts:
                continue
            last_seen_id = seen.get(channel, 0)
            new_posts = [p for p in posts if p['id'] > last_seen_id][-_MAX_POSTS_PER_CHANNEL:]
            if new_posts:
                new_by_channel[channel] = new_posts
            seen[channel] = max(p['id'] for p in posts)
        except Exception as e:
            logger.error(f'Error fetching telegram channel {channel}: {e}')

    _save_seen(seen)
    return new_by_channel


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(get_telegram_posts(), indent=2, ensure_ascii=False))
