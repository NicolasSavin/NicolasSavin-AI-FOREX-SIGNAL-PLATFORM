from __future__ import annotations

from typing import Any

from app.services.idea_narrative_llm import IdeaNarrativeLLMService


class _Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _ok_content() -> str:
    return (
        '{"idea_thesis":"После снятия sell-side ликвидности цена быстро вернулась в dealing range, потому что крупный покупатель защитил discount order block. '
        'Это привело к локальному BOS вверх и снижению инициативы продавца, поэтому сценарий сместился в continuation, а не в разворот. '
        'В результате повторный тест зоны рассматривается как рабочий entry только при сохранении реакции и объёма. '
        'Если реакция ослабеет и структура сломается, сценарий отменяется.",'
        '"headline":"EURUSD H1 long","summary":"Кратко","cause":"После снятия ликвидности причина ясна","confirmation":"Подтверждение",'
        '"risk":"Риск","invalidation":"Инвалидация","target_logic":"Логика цели",'
        '"update_explanation":"Что изменилось","short_text":"Коротко","full_text":"После sweep ликвидности сформирован BOS, крупный покупатель защищает order block, объём и дельта подтверждают реакцию.",'
        '"unified_narrative":"После sweep ликвидности под минимумом цена вернулась в диапазон, что показывает вход крупного покупателя. BOS подтверждает, что это скорее continuation после реакции, а не разворот. В зоне order block идёт набор позиции и защита уровня. Объём и дельта подтверждают импульс, дивергенция продавцов не усиливается. При сломе структуры сценарий отменяется.",'
        '"summary_structured":{"signal":"BUY","situation":"Рынок у зоны","cause":"Сняли ликвидность","effect":"Ожидаем импульс","action":"Ждать триггер и входить","risk_note":"Риск растет при сломе структуры"},'
        '"trade_plan_structured":{"entry_trigger":"BOS вверх","entry_zone":"1.0800-1.0810","stop_loss":"ниже 1.0790","take_profit":"1.0840","invalidation":"уход ниже 1.0790"},'
        '"market_structure_structured":{"bias":"бычий","structure":"локальный BOS","liquidity":"снята нижняя ликвидность","zone":"discount OB","confluence":"SMC + импульс"}}'
    )


def test_parse_valid_llm_json(monkeypatch) -> None:
    service = IdeaNarrativeLLMService()
    service.api_key = "test"

    def _post(*args, **kwargs):
        return _Resp({"choices": [{"message": {"content": _ok_content()}}]})

    monkeypatch.setattr("requests.post", _post)
    result = service.generate(event_type="idea_created", facts={"symbol": "EURUSD"})

    assert result.source == "llm"
    assert result.data["headline"] == "EURUSD H1 long"


def test_invalid_json_retries_once(monkeypatch) -> None:
    service = IdeaNarrativeLLMService()
    service.api_key = "test"
    calls = {"n": 0}

    def _post(*args, **kwargs):
        calls["n"] += 1
        content = "not-json" if calls["n"] == 1 else _ok_content()
        return _Resp({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr("requests.post", _post)
    result = service.generate(event_type="idea_updated", facts={"symbol": "EURUSD"})

    assert calls["n"] == 2
    assert result.source == "llm"


def test_fallback_after_invalid_retry(monkeypatch) -> None:
    service = IdeaNarrativeLLMService()
    service.api_key = "test"

    def _post(*args, **kwargs):
        return _Resp({"choices": [{"message": {"content": "still-invalid"}}]})

    monkeypatch.setattr("requests.post", _post)
    result = service.generate(
        event_type="idea_updated",
        facts={"symbol": "EURUSD", "timeframe": "H1", "direction": "bullish", "status": "active"},
    )

    assert result.source == "fallback"
    assert "EURUSD" in result.data["full_text"]


def test_rejects_banned_phrase_and_retries(monkeypatch) -> None:
    service = IdeaNarrativeLLMService()
    service.api_key = "test"
    calls = {"n": 0}

    invalid = (
        '{"headline":"EURUSD H1 long","summary":"Кратко","cause":"Причина","confirmation":"Подтверждение",'
        '"risk":"Риск","invalidation":"Инвалидация","target_logic":"Логика цели",'
        '"update_explanation":"Что изменилось","short_text":"Коротко","full_text":"Сценарий строится вокруг зоны входа.",'
        '"unified_narrative":"Сценарий строится вокруг зоны входа.",'
        '"summary_structured":{"signal":"BUY","situation":"Рынок у зоны","cause":"Причина","effect":"Эффект","action":"Действие","risk_note":"Риск"},'
        '"trade_plan_structured":{"entry_trigger":"Триггер","entry_zone":"Зона","stop_loss":"SL","take_profit":"TP","invalidation":"Инвалидация"},'
        '"market_structure_structured":{"bias":"бычий","structure":"структура","liquidity":"ликвидность","zone":"зона","confluence":"конфлюенс"}}'
    )

    def _post(*args, **kwargs):
        calls["n"] += 1
        content = invalid if calls["n"] == 1 else _ok_content()
        return _Resp({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr("requests.post", _post)
    result = service.generate(event_type="idea_updated", facts={"symbol": "EURUSD"})

    assert calls["n"] == 2
    assert result.source == "llm"


def test_rejects_missing_cause_effect_chain_and_retries(monkeypatch) -> None:
    service = IdeaNarrativeLLMService()
    service.api_key = "test"
    calls = {"n": 0}

    invalid = (
        '{"headline":"EURUSD H1 long","summary":"Кратко","cause":"Причина","confirmation":"Подтверждение",'
        '"risk":"Риск","invalidation":"Инвалидация","target_logic":"Логика цели","update_explanation":"Что изменилось",'
        '"idea_thesis":"Крупный игрок отмечен в стакане, ликвидность снята, сформирован BOS и удерживается order block. '
        'Сценарий описан для зоны спроса и импульса в continuation. Триггер подтверждается структурой, риск ограничен уровнем отмены. '
        'Логика входа и цели построена по SMC-модели без лишних допущений.",'
        '"short_text":"Коротко","full_text":"Крупный игрок защищает order block после sweep, виден BOS и контроль ликвидности.",'
        '"unified_narrative":"Крупный участник защищает order block после sweep ликвидности, в структуре есть BOS и контроль зоны discount. '
        'Импульс подтверждён и сценарий остаётся в continuation, пока зона не потеряна.",'
        '"summary_structured":{"signal":"BUY","situation":"Рынок у зоны","cause":"Причина","effect":"Эффект","action":"Действие","risk_note":"Риск"},'
        '"trade_plan_structured":{"entry_trigger":"Триггер","entry_zone":"Зона","stop_loss":"SL","take_profit":"TP","invalidation":"Инвалидация"},'
        '"market_structure_structured":{"bias":"бычий","structure":"структура","liquidity":"ликвидность","zone":"зона","confluence":"конфлюенс"}}'
    )

    def _post(*args, **kwargs):
        calls["n"] += 1
        content = invalid if calls["n"] == 1 else _ok_content()
        return _Resp({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr("requests.post", _post)
    result = service.generate(event_type="idea_updated", facts={"symbol": "EURUSD"})

    assert calls["n"] == 2
    assert result.source == "llm"
