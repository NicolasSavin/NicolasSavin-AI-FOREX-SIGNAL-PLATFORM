from __future__ import annotations

import re

from app.services.narrative_generator import generate_signal_preview_text, generate_signal_text


def test_generate_signal_text_contains_structured_causal_chain() -> None:
    text = generate_signal_text(
        {
            "symbol": "EURUSD",
            "timeframe": "M15",
            "direction": "bullish",
            "current_price": 1.0849,
            "market_structure": "после liquidity sweep сформирован HL и локальный BOS",
            "liquidity_context": "sweep sell-side liquidity ниже 1.0820",
            "target_liquidity": "equal highs 1.0875-1.0880",
            "bos": True,
            "choch": True,
            "fvg": "1.0834-1.0839",
            "premium_discount_state": "discount-зона intraday dealing range",
            "order_blocks": ["bullish OB 1.0832-1.0840"],
            "chart_patterns": ["bull flag"],
            "volume_context": "volume expansion на импульсе и contraction на откате",
            "cumulative_delta": "cumdelta подтверждает агрессию покупателей",
            "divergence_context": "медвежья дивергенция не получила развития",
            "wave_context": "после импульсной волны идёт коррективная 2/4",
            "options_context": "put-wall 1.0825, gamma support 1.0840",
            "fundamental_context": "ожидания мягкого цикла ФРС давят на доллар",
            "entry": 1.0842,
            "stop_loss": 1.0828,
            "take_profit": 1.0876,
            "invalidation": "цена закрепится ниже HL и потеряет спрос в OB",
            "data_status": "live",
        }
    )

    sentences = [x for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]
    assert 5 <= len(sentences) <= 8
    assert "smart money" in text.lower()
    assert "liquidity" in text.lower()
    assert "bias" in text.lower()
    assert "TP 1.0876" in text
    assert "1.0828" in text
    assert any(token in text.lower() for token in ("пока", "если", "поэтому"))
    assert "atr" not in text.lower()
    assert "pattern module" not in text.lower()
    assert "no pattern detected" not in text.lower()


def test_generate_signal_text_is_deterministic_for_same_payload() -> None:
    payload = {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "direction": "bullish",
        "current_price": 1.0849,
        "entry": 1.0842,
        "stop_loss": 1.0828,
        "take_profit": 1.0876,
        "data_status": "live",
    }
    first = generate_signal_text(payload)
    second = generate_signal_text(payload)
    assert first == second


def test_generate_signal_text_returns_neutral_when_market_data_unavailable() -> None:
    text = generate_signal_text(
        {
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "direction": "bullish",
            "data_status": "unavailable",
        }
    )

    assert "нет надёжного рыночного снимка" in text
    assert "нейтральным наблюдением" in text


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
