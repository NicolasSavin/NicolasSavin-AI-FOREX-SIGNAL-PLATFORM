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
        '"volume_context":"Объём расширяется на импульсе и сжимается на откате.","divergence_context":"Дивергенция продавца не усиливается и не ломает импульс.","options_context":"Опционный слой ограничен: выраженного контр-сигнала нет.","execution_context":"Вход только после подтверждения реакции от зоны и удержания структуры.",'
        '"unified_narrative":"EURUSD сейчас торгуется внутри рабочей зоны спроса после резкого возврата цены в диапазон. Сначала цена сняла sell-side ликвидность под локальными минимумами, и это вызвало быстрый разворот вверх. Такая реакция указывает, что крупный покупатель защищает discount-область по SMC/ICT. В структуре уже отмечен локальный BOS, поэтому базовый сценарий остаётся continuation, а не разворотом вниз. В зоне сохраняется order block и рядом виден FVG, который пока не закрыт полностью и поддерживает импульс. Объём расширяется на росте, а дельта и дивергенция не показывают усиления встречного давления. По опционному слою данных недостаточно для отдельного сигнала, поэтому вывод опирается на структуру и объём. Вход логичен только после повторного подтверждения реакции от зоны и удержания текущего импульса. Инвалидация сценария проходит при сломе локальной структуры и уходе ниже рабочей области. Цель размещается в ближайшем пуле buy-side ликвидности над диапазоном. Главный риск в том, что импульс может оказаться краткосрочным и рынок вернётся в коррекционную фазу.",'
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


def test_invalid_json_uses_llm_text(monkeypatch) -> None:
    service = IdeaNarrativeLLMService()
    service.api_key = "test"
    calls = {"n": 0}

    def _post(*args, **kwargs):
        calls["n"] += 1
        content = "not-json" if calls["n"] == 1 else _ok_content()
        return _Resp({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr("requests.post", _post)
    result = service.generate(event_type="idea_updated", facts={"symbol": "EURUSD"})

    assert calls["n"] >= 1
    assert result.source == "llm_text"
    assert result.data["idea_article_ru"] == "not-json"


def test_plain_text_response_is_not_fallback(monkeypatch) -> None:
    service = IdeaNarrativeLLMService()
    service.api_key = "test"

    def _post(*args, **kwargs):
        return _Resp({"choices": [{"message": {"content": "still-invalid"}}]})

    monkeypatch.setattr("requests.post", _post)
    result = service.generate(
        event_type="idea_updated",
        facts={"symbol": "EURUSD", "timeframe": "H1", "direction": "bullish", "status": "active"},
    )

    assert result.source == "llm_text"
    assert result.data["idea_article_ru"] == "still-invalid"


def test_partial_json_is_used_without_full_reject(monkeypatch) -> None:
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

    assert calls["n"] >= 1
    assert result.source == "llm"


def test_json_without_strict_quality_checks_is_used(monkeypatch) -> None:
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

    assert calls["n"] >= 1
    assert result.source == "llm"


def test_wait_fallback_is_descriptive_and_honest() -> None:
    service = IdeaNarrativeLLMService()
    result = service.generate(
        event_type="idea_created",
        facts={"symbol": "USDJPY", "timeframe": "H1", "direction": "neutral", "status": "waiting"},
    )
    assert result.source == "fallback"
    text = result.data["unified_narrative"].lower()
    assert "сценарии wait" in text
    assert "опционный слой сейчас недоступен" in text
