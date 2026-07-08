from __future__ import annotations

import logging
import re
from typing import Any

from .transcript_models import TranscriptResult, TranscriptSegment, TranscriptStatus

logger = logging.getLogger(__name__)
_SPACE_RE = re.compile(r"\s+")


def clean_transcript_text(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\ufeff", " ")).strip()


class YouTubeTranscriptProvider:
    provider_name = "youtube-transcript-api"
    preferred_languages = ("ru", "en")

    def get(self, video_id: str) -> TranscriptResult:
        try:
            from youtube_transcript_api import (  # type: ignore
                NoTranscriptAvailable,
                TranscriptsDisabled,
                VideoUnavailable,
                YouTubeTranscriptApi,
            )
        except Exception as exc:
            logger.warning("youtube_transcript_api_import_failed video_id=%s error=%s", video_id, exc)
            return TranscriptResult(video_id, None, self.provider_name, "", [], None, TranscriptStatus.WHISPER_REQUIRED, str(exc))

        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = self._select_transcript(transcript_list)
            language = getattr(transcript, "language_code", None)
            raw_segments = transcript.fetch()
            return self._normalize(video_id, language, raw_segments)
        except (NoTranscriptAvailable, TranscriptsDisabled, VideoUnavailable) as exc:
            return TranscriptResult(video_id, None, self.provider_name, "", [], None, TranscriptStatus.WHISPER_REQUIRED, str(exc))
        except Exception as exc:
            logger.exception("youtube_transcript_fetch_failed video_id=%s", video_id)
            return TranscriptResult(video_id, None, self.provider_name, "", [], None, TranscriptStatus.ERROR, str(exc))

    def _select_transcript(self, transcript_list: Any) -> Any:
        for finder in ("find_manually_created_transcript", "find_generated_transcript"):
            try:
                return getattr(transcript_list, finder)(list(self.preferred_languages))
            except Exception:
                continue
        for transcript in transcript_list:
            language = getattr(transcript, "language_code", "")
            if language in self.preferred_languages:
                return transcript
        for transcript in transcript_list:
            try:
                if getattr(transcript, "is_translatable", False):
                    return transcript.translate("ru")
            except Exception:
                continue
        return transcript_list.find_transcript(["ru", "en"])

    def _normalize(self, video_id: str, language: str | None, raw_segments: list[dict[str, Any]]) -> TranscriptResult:
        segments: list[TranscriptSegment] = []
        for item in raw_segments or []:
            text = clean_transcript_text(item.get("text"))
            if not text:
                continue
            start = float(item.get("start") or 0)
            duration = float(item.get("duration") or 0)
            speaker = clean_transcript_text(item.get("speaker")) or None
            segments.append(TranscriptSegment(text=text, start=start, duration=duration, speaker=speaker))
        full_text = "\n\n".join(segment.text for segment in segments)
        duration = max((segment.start + segment.duration for segment in segments), default=None)
        status = TranscriptStatus.FOUND if segments else TranscriptStatus.NOT_AVAILABLE
        return TranscriptResult(video_id, language or "auto", self.provider_name, full_text, segments, duration, status)
