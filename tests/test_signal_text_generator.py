from __future__ import annotations

import re

from backend.signal_text_generator import generate_signal_text


def test_generate_signal_text_full_context_in_single_paragraph() -> None:
    text = generate_signal_text(
        {
            "symbol": "EURUSD",
            "direction": "SELL",
            "trend": "down",
            "entry": 1.0900,
            "stop_loss": 1.0950,
            "take_profit": 1.0820,
            "key_levels": [1.0950, 1.0900, 1.0820],
            "pattern_type": "bearish_gartley",
            "smc_signals": {
                "bos": True,
                "choch": True,
                "liquidity": "buy-side liquidity sweep above equal highs",
                "order_block": "bearish 4H block",
                "fvg": True,
            },
            "wave_context": "коррекционная волна завершена, стартует импульс вниз",
            "volume_data": {"summary": "отрицательная кумулятивная дельта подтверждает доминирование продавца"},
            "options_data": {"summary": "опционные страйки 1.0920/1.0950 выступают сопротивлением перед экспирацией"},
            "timeframe": "H1",
        }
    )

    assert "\n" not in text
    assert len(re.findall(r"\.(?:\s|$)", text)) == 4
    assert "BOS" in text
    assert "CHoCH" in text
    assert "liquidity" in text
    assert "order block" in text
    assert "FVG" in text
    assert "волновой контекст" in text
    assert "опционные страйки" in text
    assert "1.09" in text
    assert "1.095" in text
    assert "1.082" in text
    assert "отменяет сценарий" in text


def test_generate_signal_text_skips_missing_optional_data() -> None:
    text = generate_signal_text(
        {
            "symbol": "GBPUSD",
            "direction": "BUY",
            "trend": "up",
            "entry": 1.2742,
            "stop_loss": 1.2690,
            "take_profit": 1.2815,
            "smc_signals": {"bos": True, "order_block": "bullish demand", "fvg": False},
            "timeframe": "M30",
        }
    )

    assert "\n" not in text
    assert len(re.findall(r"\.(?:\s|$)", text)) == 4
    assert "Причина входа" in text
    assert "опционные" not in text
    assert "волновой контекст" not in text
    assert "long от 1.2742" in text
    assert "стоп 1.269" in text
    assert "цель 1.2815" in text


def test_generate_signal_text_no_trade_like_input_still_returns_plan() -> None:
    text = generate_signal_text(
        {
            "symbol": "USDJPY",
            "direction": "NO_TRADE",
            "trend": "neutral",
            "entry": None,
            "stop_loss": None,
            "take_profit": None,
            "smc_signals": {},
            "timeframe": "H4",
        }
    )

    assert "\n" not in text
    assert len(re.findall(r"\.(?:\s|$)", text)) == 4
    assert "сценарий оценивается от ключевых уровней" in text
    assert "позиция от —" in text
