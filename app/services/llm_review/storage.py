from __future__ import annotations

import json
from pathlib import Path

from app.services.llm_review.models import LLMReview


class LLMReviewStorage:
    def __init__(self, base_dir: Path | str = Path("data") / "llm_reviews") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, video_id: str) -> Path:
        safe = "".join(ch for ch in str(video_id) if ch.isalnum() or ch in {"-", "_"}) or "unknown"
        return self.base_dir / f"{safe}.json"

    def get(self, video_id: str) -> LLMReview | None:
        path = self.path_for(video_id)
        if not path.exists():
            return None
        return LLMReview.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def set(self, video_id: str, review: LLMReview) -> None:
        self.path_for(video_id).write_text(review.model_dump_json(indent=2), encoding="utf-8")
