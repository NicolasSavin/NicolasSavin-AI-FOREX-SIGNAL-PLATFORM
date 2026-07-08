from __future__ import annotations

from typing import Protocol

from .transcript_models import TranscriptResult


class TranscriptProvider(Protocol):
    provider_name: str

    def get(self, video_id: str) -> TranscriptResult:
        """Return a normalized transcript result for a YouTube video id."""
