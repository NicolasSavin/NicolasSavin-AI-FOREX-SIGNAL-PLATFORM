from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


class JsonStorage:
    def __init__(self, path: str, default_payload: Any) -> None:
        self.path = Path(path)
        self.default_payload = default_payload

    def read(self) -> Any:
        if not self.path.exists():
            return copy.deepcopy(self.default_payload)
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, payload: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
