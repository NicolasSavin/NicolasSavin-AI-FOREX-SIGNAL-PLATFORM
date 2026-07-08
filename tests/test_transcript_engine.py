import json
from pathlib import Path

from app.services.transcript import TranscriptEngine, TranscriptStorage, TranscriptStatus
from app.services.transcript.transcript_models import TranscriptResult, TranscriptSegment


class FakeProvider:
    provider_name = "fake-youtube"

    def __init__(self) -> None:
        self.calls = 0

    def get(self, video_id: str) -> TranscriptResult:
        self.calls += 1
        return TranscriptResult(
            video_id=video_id,
            language="ru",
            source=self.provider_name,
            transcript="Первый абзац.\n\nВторой абзац.",
            segments=[TranscriptSegment("Первый абзац.", 0, 2), TranscriptSegment("Второй абзац.", 2, 3)],
            duration=5,
            status=TranscriptStatus.FOUND,
        )


def test_transcript_engine_saves_and_reuses_cache(tmp_path: Path):
    provider = FakeProvider()
    engine = TranscriptEngine(providers=[provider], storage=TranscriptStorage(tmp_path))

    first = engine.get("4M6s03LQbsg")
    second = engine.get("4M6s03LQbsg")

    assert provider.calls == 1
    assert first.status == TranscriptStatus.FOUND
    assert second.cached is True
    assert second.transcript == "Первый абзац.\n\nВторой абзац."
    saved = json.loads((tmp_path / "4M6s03LQbsg.json").read_text(encoding="utf-8"))
    assert {"video_id", "created_at", "provider", "language", "segments", "full_text", "duration"}.issubset(saved.keys())


def test_transcript_engine_rejects_invalid_youtube_id(tmp_path: Path):
    engine = TranscriptEngine(providers=[FakeProvider()], storage=TranscriptStorage(tmp_path))
    try:
        engine.get("bad")
    except ValueError as exc:
        assert "invalid YouTube video id" in str(exc)
    else:
        raise AssertionError("invalid id accepted")
