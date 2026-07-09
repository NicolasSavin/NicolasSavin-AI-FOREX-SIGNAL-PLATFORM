from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.pipeline.models import PipelineResult


class PipelineStorage:
    def __init__(self, root: Path | str = "data/pipeline") -> None:
        self.root = Path(root)

    def get(self, video_id: str) -> PipelineResult | None:
        path = self._path(video_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return PipelineResult(**payload)

    def set(self, result: PipelineResult) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(result.video_id)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def all(self) -> list[PipelineResult]:
        if not self.root.exists():
            return []
        rows: list[PipelineResult] = []
        for path in self.root.glob("*.json"):
            try:
                rows.append(PipelineResult(**json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return rows

    def metrics(self) -> dict[str, Any]:
        rows = self.all()
        finished = [r for r in rows if r.pipeline_status in {"completed", "completed_with_warnings", "failed"}]
        times = [float(r.execution_time or 0) for r in finished if r.execution_time]
        last = sorted(finished, key=lambda r: r.finished_at or "", reverse=True)[:1]
        failed = [r for r in rows if r.pipeline_status == "failed" or r.failed_steps]
        return {
            "pipeline_running": len([r for r in rows if r.pipeline_status == "running"]),
            "pipeline_completed": len([r for r in rows if r.pipeline_status in {"completed", "completed_with_warnings"}]),
            "pipeline_failed": len(failed),
            "pipeline_average_time": round(sum(times) / len(times), 3) if times else 0,
            "last_pipeline_video": last[0].video_id if last else None,
            "last_pipeline_error": (failed[-1].errors[-1] if failed and failed[-1].errors else None),
        }

    def _path(self, video_id: str) -> Path:
        safe = "".join(ch for ch in str(video_id) if ch.isalnum() or ch in {"_", "-"}) or "unknown"
        return self.root / f"{safe}.json"
