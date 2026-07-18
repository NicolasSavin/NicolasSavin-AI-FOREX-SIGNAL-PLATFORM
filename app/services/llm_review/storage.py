from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.services.llm_review.models import LLMReview


@dataclass
class StoredLLMReview:
    lookup_key: str
    storage_key: str
    review: LLMReview
    updated_at: str | None = None


@dataclass
class LLMReviewListResult:
    items: list[StoredLLMReview] = field(default_factory=list)
    files_scanned: int = 0
    malformed_count: int = 0
    error_count: int = 0


class LLMReviewStorage:
    def __init__(self, base_dir: Path | str = Path("data") / "llm_reviews") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def storage_key(self, video_id: str) -> str:
        return "".join(ch for ch in str(video_id) if ch.isalnum() or ch in {"-", "_"}) or "unknown"

    def path_for(self, video_id: str) -> Path:
        return self.base_dir / f"{self.storage_key(video_id)}.json"

    def get(self, video_id: str) -> LLMReview | None:
        path = self.path_for(video_id)
        if not path.exists():
            return None
        return LLMReview.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def set(self, video_id: str, review: LLMReview) -> None:
        self.path_for(video_id).write_text(review.model_dump_json(indent=2), encoding="utf-8")

    def list_keys(self) -> list[str]:
        return sorted(path.stem for path in self.base_dir.glob("*.json") if path.is_file())

    def list_reviews(self) -> LLMReviewListResult:
        result = LLMReviewListResult()
        for path in sorted(self.base_dir.glob("*.json")):
            if not path.is_file():
                continue
            result.files_scanned += 1
            try:
                review = LLMReview.model_validate(json.loads(path.read_text(encoding="utf-8")))
                updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                result.items.append(StoredLLMReview(lookup_key=path.stem, storage_key=path.stem, review=review, updated_at=updated_at))
            except json.JSONDecodeError:
                result.malformed_count += 1
            except Exception:
                result.error_count += 1
        return result

    def count(self) -> int:
        return len(self.list_keys())
