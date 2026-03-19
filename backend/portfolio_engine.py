from datetime import datetime, timezone
from backend.chart_generator import ChartGenerator


class PortfolioEngine:

    def __init__(self):
        self._chart_generator = ChartGenerator()

    def market_ideas(self):

        idea = {
            "symbol": "XAUUSD",
            "direction": "NEUTRAL",
            "confidence": 64,
            "timeframe": "Интрадей",
            "summary": "Рынок в диапазоне. Ожидание выхода к ликвидности."
        }

        # === ВАЖНО: генерим chart_data ===
        idea["chart_data"] = self._chart_generator.generate_chart("XAUUSD", idea)

        return {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "ideas": [idea]
        }
