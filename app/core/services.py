from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AppServices:
    media_import: Any | None = None
    automation: Any | None = None
    review: Any | None = None
    knowledge_graph: Any | None = None
    consensus: Any | None = None
    authors: Any | None = None
    performance: Any | None = None
    validation: Any | None = None
    market_state: Any | None = None
    multi_timeframe: Any | None = None
    confluence: Any | None = None
    opportunities: Any | None = None
    decisions: Any | None = None
    strategies: Any | None = None
    paper: Any | None = None
    portfolio: Any | None = None
    execution: Any | None = None
    orchestration: Any | None = None
