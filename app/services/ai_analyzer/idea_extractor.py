from __future__ import annotations

import re

from app.services.ai_analyzer.models import TradingIdea

NUMBER = r"(\d{1,6}(?:[.,]\d{1,5})?)"
PATTERNS = {
    "entry": [rf"(?:entry|вход|заход|buy from|sell from)\D{{0,24}}{NUMBER}"],
    "stop_loss": [rf"(?:stop loss|stop-loss|stop|sl|стоп|стоп.?лосс)\D{{0,24}}{NUMBER}"],
    "take_profit": [rf"(?:take profit|take-profit|tp|тейк|профит)\D{{0,24}}{NUMBER}"],
    "targets": [rf"(?:target\s*[12]?|цель\s*[12]?|таргет\s*[12]?)\D{{0,24}}{NUMBER}"],
    "risk": [rf"(?:risk|риск)\D{{0,24}}{NUMBER}"],
    "reward": [rf"(?:reward|прибыль|потенциал)\D{{0,24}}{NUMBER}"],
    "rr": [rf"(?:rr|r/r|risk.?reward)\D{{0,12}}(\d+(?:[.,]\d+)?)"],
}


def _number(value: str) -> float:
    return float(value.replace(",", "."))


class TradingIdeaExtractor:
    def extract(self, transcript: str) -> TradingIdea:
        text = transcript or ""
        found: dict[str, float | list[float] | None] = {"entry": None, "stop_loss": None, "take_profit": None, "targets": [], "risk": None, "reward": None, "rr": None}
        for key, patterns in PATTERNS.items():
            values: list[float] = []
            for pattern in patterns:
                values.extend(_number(match.group(1)) for match in re.finditer(pattern, text, flags=re.IGNORECASE))
            if key == "targets":
                found[key] = list(dict.fromkeys(values))
            elif values:
                found[key] = values[0]
        tp = found["take_profit"]
        targets = found["targets"] if isinstance(found["targets"], list) else []
        if tp is None and targets:
            tp = targets[0]
        return TradingIdea(entry=found["entry"], stop_loss=found["stop_loss"], take_profit=tp, targets=targets, risk=found["risk"], reward=found["reward"], rr=found["rr"])  # type: ignore[arg-type]
