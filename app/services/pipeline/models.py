from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

PIPELINE_STEPS = [
    "transcript",
    "rule_ai",
    "knowledge",
    "llm_review",
    "committee",
    "consensus",
    "author_intelligence",
    "performance",
]


@dataclass
class PipelineResult:
    video_id: str
    pipeline_status: str = "pending"
    transcript_status: str = "pending"
    rule_ai_status: str = "pending"
    knowledge_status: str = "pending"
    review_status: str = "pending"
    committee_status: str = "pending"
    consensus_status: str = "pending"
    author_status: str = "pending"
    performance_status: str = "pending"
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    execution_time: float = 0.0
    started_at: str | None = None
    finished_at: str | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def empty(cls, video_id: str) -> "PipelineResult":
        return cls(video_id=video_id, pipeline_status="not_started")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
