from __future__ import annotations

from .transcript_models import TranscriptResult, TranscriptStatus


class WhisperTranscriptProvider:
    """Architecture placeholder for a future Whisper/audio transcription provider."""

    provider_name = "whisper-placeholder"

    def get(self, video_id: str) -> TranscriptResult:
        return TranscriptResult(
            video_id=video_id,
            language=None,
            source=self.provider_name,
            transcript="",
            segments=[],
            duration=None,
            status=TranscriptStatus.WHISPER_REQUIRED,
        )
