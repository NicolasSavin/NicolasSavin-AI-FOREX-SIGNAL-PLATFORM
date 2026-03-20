from __future__ import annotations

from backend.grok_idea_service import GrokIdeaService



def test_build_idea_payload_omits_options_without_real_levels() -> None:
    service = GrokIdeaService(api_key="test")

    payload = service.build_idea_payload(
        {
            "symbol": "EURUSD",
            "direction": "LONG",
            "entry_logic": "Цена удерживает зону спроса.",
            "confirmations": ["SMC", "Liquidity"],
            "scenario": "Ждём продолжение роста.",
            "targets": "1.1550 / 1.1580",
            "invalidation": "Потеря 1.1480 ломает сценарий.",
            "tags": ["SMC", "Liquidity", "Options"],
            "options_context": "учитывая опционы",
        }
    )

    assert payload["options"] == ""
    assert "Options" not in payload["tags"]



def test_build_idea_payload_uses_only_concrete_real_option_levels() -> None:
    service = GrokIdeaService(api_key="test")

    payload = service.build_idea_payload(
        {
            "symbol": "EURUSD",
            "direction": "LONG",
            "entry_logic": "Цена удерживает зону спроса.",
            "confirmations": ["SMC", "Liquidity"],
            "scenario": "Ждём продолжение роста.",
            "targets": "1.1550 / 1.1580",
            "invalidation": "Потеря 1.1480 ломает сценарий.",
            "option_levels": [{"strike": 1.1550}],
            "gamma_levels": [{"level": 1.1575}],
            "expiry_levels": ["1.1600"],
            "tags": ["SMC", "Liquidity", "Options"],
        }
    )

    assert "крупный опцион на 1.155" in payload["options"]
    assert "gamma-уровни на 1.1575" in payload["options"]
    assert "экспирационные уровни на 1.1600" in payload["options"]
    assert "опционный контекст" not in payload["options"].lower()
    assert "учитывая опционы" not in payload["options"].lower()
    assert "Options" in payload["tags"]



def test_build_detailed_idea_from_news_does_not_add_option_block_without_data() -> None:
    service = GrokIdeaService(api_key="test")

    payload = service.build_detailed_idea_from_news(
        {
            "title": "EUR укрепляется после сильного CPI",
            "summary_ru": "После публикации CPI спрос на евро усилился.",
        },
        "EURUSD",
    )

    assert payload["options"] == ""
    assert payload["analysis"]["options_ru"] == ""
    assert "Options" not in payload["tags"]
