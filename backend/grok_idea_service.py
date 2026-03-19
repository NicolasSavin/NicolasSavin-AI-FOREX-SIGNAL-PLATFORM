from __future__ import annotations

# =========================
# API KEY
# =========================

OPENAI_API_KEY = "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"


class GrokIdeaService:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or OPENAI_API_KEY

    def should_publish_idea(self, ctx: dict) -> bool:
        return (
            ctx.get("price_in_key_zone")
            and ctx.get("structure_confirmed")
            and len(ctx.get("confirmations", [])) >= 2
            and ctx.get("direction")
            and ctx.get("entry_logic")
            and ctx.get("targets")
            and ctx.get("invalidation")
        )

    def classify_market_state(self, ctx: dict) -> dict:
        if ctx.get("news_event") and not ctx.get("structure_confirmed"):
            return {
                "status": "watching",
                "publish_trade_idea": False,
                "message": "Есть событие, но подтвержденного сетапа еще нет."
            }

        if self.should_publish_idea(ctx):
            return {
                "status": "confirmed_idea",
                "publish_trade_idea": True,
                "message": "Сетап подтвержден. Можно публиковать торговую идею."
            }

        return {
            "status": "setup_forming",
            "publish_trade_idea": False,
            "message": "Сценарий формируется, но еще не готов к публикации."
        }

    def build_idea_payload(self, ctx: dict) -> dict:
        return {
            "symbol": ctx.get("symbol"),
            "direction": ctx.get("direction"),
            "confidence": ctx.get("confidence", 60),
            "timeframe": ctx.get("timeframe", "Intraday"),
            "summary": ctx.get("entry_logic", ""),
            "technical": ", ".join(ctx.get("confirmations", [])),
            "options": ctx.get("options_context", ""),
            "scenario": ctx.get("scenario", ""),
            "targets": ctx.get("targets", ""),
            "invalidation": ctx.get("invalidation", ""),
            "image": ctx.get("image", "/static/default-chart.png"),
            "tags": ctx.get("tags", ["SMC", "Liquidity"]),
        }

    def generate_trade_idea(self, ctx: dict) -> dict | None:
        market_state = self.classify_market_state(ctx)
        if market_state["publish_trade_idea"]:
            return self.build_idea_payload(ctx)
        return None

    async def generate_trade_idea_async(self, ctx: dict) -> dict | None:
        return self.generate_trade_idea(ctx)

    def generate(self, ctx: dict) -> dict | None:
        return self.generate_trade_idea(ctx)

    async def generate_async(self, ctx: dict) -> dict | None:
        return self.generate_trade_idea(ctx)


if __name__ == "__main__":
    service = GrokIdeaService()

    ctx = {
        "symbol": "DXY",
        "news_event": True,
        "price_in_key_zone": True,
        "structure_confirmed": True,
        "confirmations": [
            "bearish order block",
            "liquidity sweep",
            "weak structure"
        ],
        "direction": "SHORT",
        "entry_logic": "Цена в зоне предложения после новости",
        "targets": "104.20 / 103.80",
        "invalidation": "Выше 105.00",
        "options_context": "Опционы ниже как цель",
        "scenario": "Реакция вниз",
        "confidence": 68,
        "timeframe": "Intraday",
        "image": "/static/default-chart.png",
        "tags": ["News", "Liquidity", "Options"]
    }

    print(service.generate_trade_idea(ctx))
