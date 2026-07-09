from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
import feedparser
from app.services.media_import_engine import ImportSourceResult, MediaItem, MediaSource, detect_symbol

class RssFeedProvider:
    provider_name = "rss_feed"
    def fetch_latest(self, source: MediaSource) -> ImportSourceResult:
        url = source.channel_url or source.feed_url or source.rss_url
        parsed = feedparser.parse(url)
        entries = list(getattr(parsed, 'entries', []) or [])
        if getattr(parsed, 'bozo', False) and not entries:
            return ImportSourceResult(source, [], 'parse_error', None, 0, f"RSS parse failed: {getattr(parsed,'bozo_exception','unknown')}")
        items=[]
        for e in entries[:20]:
            title=str(e.get('title') or 'Без названия').strip(); link=str(e.get('link') or '').strip()
            if not link: continue
            desc=str(e.get('summary') or e.get('description') or '').strip(); sym=detect_symbol(f'{title} {desc}')
            thumb=None
            if e.get('media_thumbnail'): thumb=(e.get('media_thumbnail') or [{}])[0].get('url')
            if not thumb and e.get('enclosures'): thumb=(e.get('enclosures') or [{}])[0].get('href')
            items.append(MediaItem(id=f"rss:{abs(hash(link))}", provider=self.provider_name, source_id=source.id, title=title, author=str(e.get('author') or source.name), youtube_id=None, url=link, thumbnail=thumb, published_at=str(e.get('published') or e.get('updated') or '')[:10] or None, duration=None, category=source.categories[0] if source.categories else 'Market Analysis', symbol=sym, language=source.language, description=desc, tags=[*source.categories, sym], imported_at=datetime.now(timezone.utc).isoformat()))
        feed=getattr(parsed,'feed',{})
        return ImportSourceResult(replace(source, feed_url=url, rss_url=url, feed_title=feed.get('title') if hasattr(feed,'get') else None, entry_count=len(entries)), items, 'ok', 200, len(entries), channel_title=(feed.get('title') if hasattr(feed,'get') else None))
    def resolve_source(self, source: MediaSource) -> dict[str, Any]:
        return {'ok': True, 'provider': self.provider_name, 'rss_url': source.channel_url, 'resolved_url': source.channel_url}
