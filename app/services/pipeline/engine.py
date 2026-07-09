from __future__ import annotations

import logging
import time
from typing import Any, Callable

from app.services.pipeline.models import PipelineResult, utc_now_iso
from app.services.pipeline.storage import PipelineStorage

logger = logging.getLogger(__name__)


class PipelineEngine:
    """Fault-tolerant orchestrator for existing FXPilot TV engines."""

    def __init__(
        self,
        *,
        media_catalog_loader: Callable[[], list[dict[str, Any]]],
        transcript_engine: Any,
        ai_analyzer_engine: Any,
        knowledge_engine_factory: Callable[[], Any],
        review_engine_factory: Callable[[], Any],
        committee_engine_factory: Callable[[], Any],
        consensus_engine_factory: Callable[[], Any],
        author_intelligence_engine_factory: Callable[[], Any],
        performance_engine_factory: Callable[[], Any],
        storage: PipelineStorage | None = None,
    ) -> None:
        self.media_catalog_loader = media_catalog_loader
        self.transcript_engine = transcript_engine
        self.ai_analyzer_engine = ai_analyzer_engine
        self.knowledge_engine_factory = knowledge_engine_factory
        self.review_engine_factory = review_engine_factory
        self.committee_engine_factory = committee_engine_factory
        self.consensus_engine_factory = consensus_engine_factory
        self.author_intelligence_engine_factory = author_intelligence_engine_factory
        self.performance_engine_factory = performance_engine_factory
        self.storage = storage or PipelineStorage()

    def run(self, video_id: str) -> dict[str, Any]:
        started = time.monotonic()
        result = PipelineResult(video_id=str(video_id), pipeline_status="running", started_at=utc_now_iso())
        self.storage.set(result)
        logger.info("Pipeline started video_id=%s", video_id)
        video = self._find_video(video_id)
        if not video:
            result.pipeline_status = "failed"
            result.errors.append("TV video not found")
            result.finished_at = utc_now_iso()
            result.execution_time = round(time.monotonic() - started, 3)
            self.storage.set(result)
            return result.to_dict()

        transcript_text = ""
        transcript_id = str(video.get("youtube_id") or video.get("id") or video_id)

        transcript = self._stage(result, "transcript", "transcript_status", "Transcript completed", lambda: self.transcript_engine.get(transcript_id))
        if transcript is not None:
            transcript_text = str(getattr(transcript, "transcript", "") or "")
            result.transcript_status = getattr(getattr(transcript, "status", None), "value", result.transcript_status)

        self._stage(result, "rule_ai", "rule_ai_status", "Rule AI completed", lambda: self.ai_analyzer_engine.analyze(transcript_text, {**video, "video_id": video.get("id")}))
        self._stage(result, "knowledge", "knowledge_status", "Knowledge completed", lambda: self.knowledge_engine_factory().build_for_video(str(video.get("id") or video_id)))
        self._stage(result, "llm_review", "review_status", "LLM Review completed", lambda: self.review_engine_factory().generate(str(video.get("id") or video_id), force=True))
        self._stage(result, "committee", "committee_status", "Committee completed", lambda: self.committee_engine_factory().build_for_video(str(video.get("id") or video_id)))
        self._stage(result, "consensus", "consensus_status", "Consensus completed", lambda: self.consensus_engine_factory().build(str(video.get("symbol") or "MARKET")))
        self._stage(result, "author_intelligence", "author_status", "Author Intelligence completed", lambda: self.author_intelligence_engine_factory().build_for_author(str(video.get("author") or video.get("source_id") or "Unknown")))
        self._stage(result, "performance", "performance_status", "Performance completed", lambda: self.performance_engine_factory().evaluate_video(str(video.get("id") or video_id)))

        result.pipeline_status = "completed" if not result.failed_steps else ("completed_with_warnings" if result.completed_steps else "failed")
        result.finished_at = utc_now_iso()
        result.execution_time = round(time.monotonic() - started, 3)
        self.storage.set(result)
        logger.info("Pipeline finished video_id=%s status=%s execution_time=%s", video_id, result.pipeline_status, result.execution_time)
        return result.to_dict()

    def status(self, video_id: str) -> dict[str, Any]:
        stored = self.storage.get(video_id) or PipelineResult.empty(video_id)
        return stored.to_dict()

    def _stage(self, result: PipelineResult, step: str, status_attr: str, log_message: str, func: Callable[[], Any]) -> Any:
        try:
            value = func()
            setattr(result, status_attr, "completed")
            result.completed_steps.append(step)
            result.artifacts[step] = self._compact(value)
            logger.info("%s video_id=%s", log_message, result.video_id)
            self.storage.set(result)
            return value
        except Exception as exc:
            setattr(result, status_attr, "failed")
            result.failed_steps.append(step)
            message = f"{step}: {exc.__class__.__name__}: {exc}"
            result.errors.append(message)
            result.warnings.append(message)
            logger.exception("pipeline_stage_failed step=%s video_id=%s", step, result.video_id)
            self.storage.set(result)
            return None

    def _find_video(self, video_id: str) -> dict[str, Any] | None:
        wanted = str(video_id)
        return next((v for v in self.media_catalog_loader() if str(v.get("id")) == wanted or str(v.get("youtube_id")) == wanted), None)

    def _compact(self, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        elif hasattr(value, "to_dict"):
            value = value.to_dict()
        if isinstance(value, dict):
            return {k: value.get(k) for k in list(value.keys())[:20]}
        return str(value)[:500]
