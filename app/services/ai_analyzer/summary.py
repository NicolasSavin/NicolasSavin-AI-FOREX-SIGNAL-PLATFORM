from __future__ import annotations

from app.services.ai_analyzer.models import ExtractedEntities, TradingIdea


def direction_ru(direction: str | None) -> str:
    return {"BUY": "рост", "SELL": "снижение", "NEUTRAL": "нейтральный сценарий"}.get(direction or "", "сценарий без явного направления")


class SummaryEngine:
    def build(self, entities: ExtractedEntities, idea: TradingIdea) -> str:
        symbol = entities.symbols[0] if entities.symbols else "инструменту"
        direction = entities.directions[0] if entities.directions else None
        sentences = [f"Автор описывает {direction_ru(direction)} по {symbol}."]
        if entities.indicators:
            sentences.append(f"Основные аргументы: {', '.join(entities.indicators[:3])}.")
        if idea.entry is not None:
            sentences.append(f"Зона входа отмечена около {idea.entry}.")
        target = idea.take_profit or (idea.targets[0] if idea.targets else None)
        if target is not None:
            sentences.append(f"Цель движения находится около {target}.")
        if len(sentences) < 2:
            sentences.append("Транскрипт содержит недостаточно структурированных торговых данных для полного вывода.")
        return " ".join(sentences[:4])
