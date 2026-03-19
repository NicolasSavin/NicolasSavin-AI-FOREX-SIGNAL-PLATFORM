import os

# =========================
# API KEY
# =========================

# ВСТАВЬ СВОЙ КЛЮЧ СЮДА
OPENAI_API_KEY = "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"


# =========================
# IDEA LOGIC
# =========================

def should_publish_idea(ctx):
    return (
        ctx.get("price_in_key_zone")
        and ctx.get("structure_confirmed")
        and len(ctx.get("confirmations", [])) >= 2
        and ctx.get("direction")
        and ctx.get("entry_logic")
        and ctx.get("targets")
        and ctx.get("invalidation")
    )


def classify_market_state(ctx):
    if ctx.get("news_event") and not ctx.get("structure_confirmed"):
        return {
            "status": "watching",
            "publish_trade_idea": False,
        }

    if should_publish_idea(ctx):
        return {
            "status": "confirmed_idea",
            "publish_trade_idea": True,
        }

    return {
        "status": "setup_forming",
        "publish_trade_idea": False,
    }


def build_idea_payload(ctx):
    return {
        "symbol": ctx.get("symbol"),
        "direction": ctx.get("direction"),
        "confidence": ctx.get("confidence", 60),
        "timeframe": ctx.get("timeframe", "Intraday"),
        "summary": ctx.get("entry_logic"),
        "technical": ", ".join(ctx.get("confirmations", [])),
        "options": ctx.get("options_context", ""),
        "scenario": ctx.get("scenario", ""),
        "targets": ctx.get("targets"),
        "invalidation": ctx.get("invalidation"),
        "image": ctx.get("image", "/images/default-chart.png"),
        "tags": ctx.get("tags", ["SMC", "Liquidity"]),
    }


def generate_trade_idea(ctx):
    market_state = classify_market_state(ctx)

    if market_state["publish_trade_idea"]:
        idea = build_idea_payload(ctx)
        return idea

    return None


# =========================
# TEST
# =========================

if __name__ == "__main__":
    print("OPENAI_API_KEY:", OPENAI_API_KEY[:12] + "..." if OPENAI_API_KEY and OPENAI_API_KEY != "ВСТАВЬ_СЮДА_СВОЙ_OPENAI_API_KEY" else "KEY NOT SET")

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
        "image": "/images/chart-dxy.png",
        "tags": ["News", "Liquidity", "Options"]
    }

    idea = generate_trade_idea(ctx)
    print(idea)
