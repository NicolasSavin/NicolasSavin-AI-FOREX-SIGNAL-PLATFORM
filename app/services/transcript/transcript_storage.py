from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .transcript_models import TranscriptResult, TranscriptSegment, TranscriptStatus


class TranscriptStorage:
    def __init__(self, directory: Path | str = Path("data/transcripts")) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, video_id: str) -> Path:
        return self.directory / f"{video_id}.json"

    def exists(self, video_id: str) -> bool:
        return self.path_for(video_id).exists()

    def load(self, video_id: str) -> TranscriptResult | None:
        path = self.path_for(video_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        segments = [TranscriptSegment(text=str(item.get("text") or ""), start=float(item.get("start") or 0), duration=float(item.get("duration") or 0), speaker=item.get("speaker")) for item in payload.get("segments", []) if isinstance(item, dict)]
        return TranscriptResult(
            video_id=str(payload.get("video_id") or video_id),
            language=payload.get("language"),
            source=str(payload.get("provider") or payload.get("source") or "cache"),
            transcript=str(payload.get("full_text") or payload.get("transcript") or ""),
            segments=segments,
            duration=payload.get("duration"),
            status=TranscriptStatus(str(payload.get("status") or ("FOUND" if segments else "NOT_AVAILABLE"))),
            error=payload.get("error"),
            cached=True,
        )

    def save(self, result: TranscriptResult) -> Path:
        payload = {
            "video_id": result.video_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "provider": result.source,
            "language": result.language,
            "status": result.status.value,
            "segments": [segment.to_dict() for segment in result.segments],
            "full_text": result.transcript,
            "duration": result.duration,
        }
        if result.error:
            payload["error"] = result.error
        path = self.path_for(result.video_id)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def cached_count(self) -> int:
        return len(list(self.directory.glob("*.json")))
