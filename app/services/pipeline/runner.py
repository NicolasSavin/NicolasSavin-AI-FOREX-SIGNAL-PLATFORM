from __future__ import annotations

from typing import Any

from app.services.pipeline.engine import PipelineEngine


class PipelineRunner:
    def __init__(self, engine: PipelineEngine) -> None:
        self.engine = engine

    def run(self, video_id: str) -> dict[str, Any]:
        return self.engine.run(video_id)

    def run_all(self) -> dict[str, Any]:
        results = []
        for video in self.engine.media_catalog_loader():
            video_id = str(video.get("id") or video.get("youtube_id") or "")
            if video_id:
                results.append(self.run(video_id))
        return {"processed": len(results), "results": results}

    def status(self, video_id: str) -> dict[str, Any]:
        return self.engine.status(video_id)
