from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .transcript_models import TranscriptResult, TranscriptStatus
from .transcript_provider import TranscriptProvider
from .transcript_storage import TranscriptStorage
from .whisper_provider import WhisperTranscriptProvider
from .youtube_transcript_provider import YouTubeTranscriptProvider

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass
class TranscriptDiagnostics:
    transcript_requests: int = 0
    transcript_errors: int = 0
    provider_used: dict[str, int] = field(default_factory=dict)

    def record(self, result: TranscriptResult) -> None:
        self.transcript_requests += 1
        self.provider_used[result.source] = self.provider_used.get(result.source, 0) + 1
        if result.status == TranscriptStatus.ERROR:
            self.transcript_errors += 1


class TranscriptEngine:
    def __init__(self, providers: list[TranscriptProvider] | None = None, storage: TranscriptStorage | None = None, diagnostics: TranscriptDiagnostics | None = None) -> None:
        self.providers = providers or [YouTubeTranscriptProvider(), WhisperTranscriptProvider()]
        self.storage = storage or TranscriptStorage()
        self.diagnostics = diagnostics or TranscriptDiagnostics()

    def get(self, video: str | dict[str, Any]) -> TranscriptResult:
        video_id = self._video_id(video)
        cached = self.storage.load(video_id)
        if cached:
            return cached
        for provider in self.providers:
            result = provider.get(video_id)
            self.diagnostics.record(result)
            if result.status in {TranscriptStatus.FOUND, TranscriptStatus.WHISPER_REQUIRED, TranscriptStatus.NOT_AVAILABLE, TranscriptStatus.ERROR}:
                self.storage.save(result)
                return result
        result = TranscriptResult(video_id, None, "none", "", [], None, TranscriptStatus.NOT_AVAILABLE)
        self.diagnostics.record(result)
        self.storage.save(result)
        return result

    def debug_payload(self) -> dict[str, Any]:
        return {
            "transcripts_cached": self.storage.cached_count(),
            "transcript_requests": self.diagnostics.transcript_requests,
            "transcript_errors": self.diagnostics.transcript_errors,
            "provider_used": self.diagnostics.provider_used,
        }

    def _video_id(self, video: str | dict[str, Any]) -> str:
        value = video.get("youtube_id") or video.get("video_id") or video.get("id") if isinstance(video, dict) else video
        video_id = str(value or "").strip()
        if not YOUTUBE_ID_RE.fullmatch(video_id):
            raise ValueError("invalid YouTube video id")
        return video_id
