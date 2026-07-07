#!/usr/bin/env python3
"""Report YouTube sources that still need a channel_id for RSS import."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "data" / "media_sources.json"


def main() -> int:
    try:
        payload = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Не найден файл источников: {SOURCES_PATH}")
        return 0
    except json.JSONDecodeError as exc:
        print(f"Некорректный JSON в {SOURCES_PATH}: {exc}")
        return 0

    missing = [
        source for source in payload
        if isinstance(source, dict)
        and source.get("provider") == "youtube"
        and not str(source.get("channel_id") or "").strip()
    ]

    if not missing:
        print("Все YouTube источники уже содержат channel_id.")
        return 0

    print("YouTube источники без channel_id:")
    for source in missing:
        print(f"- {source.get('id', 'unknown')}: {source.get('name', 'Без названия')} — {source.get('channel_url', 'нет URL')}")
    print()
    print("Как заполнить channel_id:")
    print("1. Open the YouTube channel page -> View page source -> search for channelId")
    print("2. Use any public channel ID lookup tool manually")
    print("3. Добавьте channel_id и rss_url в data/media_sources.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
