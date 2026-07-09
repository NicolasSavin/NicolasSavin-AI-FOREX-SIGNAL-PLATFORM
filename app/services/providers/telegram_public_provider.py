from __future__ import annotations
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from app.services.media_import_engine import ImportSourceResult, MediaImportError, MediaItem, MediaSource, detect_symbol

class TelegramPublicProvider:
    provider_name = 'telegram_public'
    @staticmethod
    def normalize_url(url: str) -> str:
        v=str(url or '').strip()
        if not v: raise MediaImportError('Telegram URL is required')
        if not re.match(r'^https?://', v, re.I): v='https://'+v
        p=urlparse(v); host=p.netloc.lower()
        if host not in {'t.me','telegram.me'}: raise MediaImportError('Only public t.me URLs are supported')
        parts=[x for x in p.path.split('/') if x]
        if parts and parts[0]=='s': parts=parts[1:]
        if not parts: raise MediaImportError('Telegram channel name is required')
        return f'https://t.me/s/{parts[0]}'
    def fetch_latest(self, source: MediaSource) -> ImportSourceResult:
        url=self.normalize_url(source.channel_url)
        r=requests.get(url, timeout=15, headers={'User-Agent':'Mozilla/5.0'})
        if r.status_code>=400: return ImportSourceResult(source, [], 'http_error', r.status_code, 0, f'Telegram HTTP {r.status_code}')
        soup=BeautifulSoup(r.text, 'html.parser'); posts=soup.select('.tgme_widget_message')
        items=[]; channel=(urlparse(url).path.rstrip('/').split('/')[-1] or source.name)
        for post in posts[:20]:
            pid=post.get('data-post') or post.get('id') or ''
            text_el=post.select_one('.tgme_widget_message_text'); text=text_el.get_text(' ', strip=True) if text_el else ''
            if not text: continue
            title=text[:90]; post_url=f"https://t.me/{pid}" if pid else url; sym=detect_symbol(text)
            img=post.select_one('.tgme_widget_message_photo_wrap')
            thumb=None
            if img and img.get('style'):
                m=re.search(r"url\(['\"]?([^)'\"]+)", img.get('style') or ''); thumb=m.group(1) if m else None
            time_el=post.select_one('time'); published=time_el.get('datetime')[:10] if time_el and time_el.get('datetime') else None
            items.append(MediaItem(id=f'telegram:{pid or abs(hash(post_url+text))}', provider=self.provider_name, source_id=source.id, title=title, author=source.name, youtube_id=None, url=post_url, thumbnail=thumb, published_at=published, duration=None, category=source.categories[0] if source.categories else 'Market Analysis', symbol=sym, language=source.language, description=text, tags=[*source.categories, sym], imported_at=datetime.now(timezone.utc).isoformat()))
        return ImportSourceResult(replace(source, feed_url=url, rss_url=url, entry_count=len(posts)), items, 'ok', r.status_code, len(posts), channel_title=channel)
    def resolve_source(self, source: MediaSource) -> dict[str, Any]:
        url=self.normalize_url(source.channel_url); return {'ok': True, 'provider': self.provider_name, 'resolved_url': url, 'rss_url': url}
