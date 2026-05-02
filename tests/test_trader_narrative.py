from app.services.trade_idea_service import TradeIdeaService


def test_build_trader_narrative_buy_contains_live_story() -> None:
    result = TradeIdeaService.buildTraderNarrative(
        {
            "symbol": "EURUSD",
            "action": "BUY",
            "entry": 1.081,
            "stop_loss": 1.078,
            "take_profit": 1.087,
            "confidence_percent": 76,
            "confluence_confirmations": ["BOS на M15", "реакция от OB"],
            "confluence_warnings": ["новостной риск в Лондоне"],
            "options_summary_ru": "данные недоступны",
        }
    )
    assert "sell-side ликвидность" in result["unified_narrative"]
    assert "Подтверждения:" in result["unified_narrative"]
    assert "Риски:" in result["unified_narrative"]
    assert "Опционный слой сейчас недоступен" in result["unified_narrative"]


def test_build_trader_narrative_sell_contains_live_story() -> None:
    result = TradeIdeaService.buildTraderNarrative(
        {
            "symbol": "GBPUSD",
            "action": "SELL",
            "entry": 1.255,
            "stop_loss": 1.259,
            "take_profit": 1.248,
            "confidence_percent": 68,
            "options_summary_ru": "put/call 1.3, max pain 1.2500",
        }
    )
    assert "buy-side ликвидность" in result["unified_narrative"]
    assert "Опционный слой: put/call" in result["unified_narrative"]


def test_build_trader_narrative_wait_is_not_empty() -> None:
    result = TradeIdeaService.buildTraderNarrative({"symbol": "USDJPY", "action": "WAIT", "confidence_percent": 52})
    assert "в ожидании подтверждения" in result["unified_narrative"].lower()
    assert result["execution_summary_ru"].strip()
