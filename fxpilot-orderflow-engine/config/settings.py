from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderflowSettings:
    databento_api_key: str = os.getenv("DATABENTO_API_KEY", "").strip()
    databento_dataset: str = os.getenv("DATABENTO_DATASET", "").strip()

    @property
    def databento_enabled(self) -> bool:
        return bool(self.databento_api_key and self.databento_dataset)
