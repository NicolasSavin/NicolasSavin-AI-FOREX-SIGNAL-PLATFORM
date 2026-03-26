from __future__ import annotations

from app.services.narrative_generator import generate_signal_preview_text, generate_signal_text


def test_generate_signal_text_contains_causal_chain_and_trade_levels() -> None:
    text = generate_signal_text(
        {
            "symbol": "EURUSD",
            "timeframe": "M15",
            "direction": "bullish",
            "market_structure": "breakout + retest",
            "liquidity_context": "sweep sell-side liquidity ниже 1.0820",
            "bos": True,
            "fvg": "1.0834-1.0839",
            "chart_patterns": ["bull flag"],
            "volume_context": "volume expansion на импульсе и contraction на откате",
            "cumulative_delta": "cumdelta подтверждает агрессию покупателей",
            "fundamental_context": "ожидания мягкого цикла ФРС давят на доллар",
            "entry": 1.0842,
            "stop_loss": 1.0828,
            "take_profit": 1.0876,
            "target_liquidity": "equal highs 1.0875-1.0880",
            "entry_type": "ретест FVG + BOS на LTF",
            "invalidation": "возврат под FVG и закрепление ниже локального HL",
        }
    )

    assert "EURUSD" in text
    assert "smart money" in text.lower()
    assert "Торговый план" in text
    assert "Инвалидация" in text
    assert "1.0842" in text
    assert "1.0828" in text
    assert "1.0876" in text


def test_generate_signal_preview_text_is_short_but_meaningful() -> None:
    text = generate_signal_preview_text(
        {
            "direction": "bearish",
            "market_structure": "failed breakout",
            "entry": 1.2715,
            "stopLoss": 1.2741,
            "takeProfit": 1.2668,
        }
    )

    assert text.startswith("Шорт:")
    assert "вход 1.2715" in text
    assert "стоп 1.2741" in text
    assert "цель 1.2668" in text
