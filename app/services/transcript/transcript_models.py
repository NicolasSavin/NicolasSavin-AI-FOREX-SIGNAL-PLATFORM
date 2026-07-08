from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class TranscriptStatus(StrEnum):
    FOUND = "FOUND"
    WHISPER_REQUIRED = "WHISPER_REQUIRED"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    ERROR = "ERROR"


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start: float
    duration: float
    speaker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.speaker is None:
            payload.pop("speaker", None)
        return payload


@dataclass(frozen=True)
class TranscriptResult:
    video_id: str
    language: str | None
    source: str
    transcript: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    duration: float | None = None
    status: TranscriptStatus = TranscriptStatus.NOT_AVAILABLE
    error: str | None = None
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "video_id": self.video_id,
            "language": self.language,
            "source": self.source,
            "transcript": self.transcript,
            "segments": [segment.to_dict() for segment in self.segments],
            "duration": self.duration,
            "status": self.status.value,
            "cached": self.cached,
        }
        if self.error:
            payload["error"] = self.error
        return payload
