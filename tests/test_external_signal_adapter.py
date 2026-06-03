from app.services.external_signal_adapter import fetch_sharkfx_external_signals, parse_external_signal_text
from app.services.prop_signal_engine import build_prop_signal_score


def test_parse_external_signal_text_extracts_core_fields():
    signal = parse_external_signal_text(
        "EURUSD BUY\nEntry: 1.0850\nSL: 1.0810\nTP1: 1.0930\nConfidence: 78%",
        message_id=101,
        date="2026-06-03T10:00:00Z",
    )

    assert signal is not None
    assert signal["source"] == "sharkfx_ru"
    assert signal["symbol"] == "EURUSD"
    assert signal["action"] == "BUY"
    assert signal["entry"] == 1.0850
    assert signal["sl"] == 1.0810
    assert signal["tp"] == 1.0930
    assert signal["confidence"] == 78


def test_fetch_sharkfx_external_signals_is_unavailable_without_credentials(monkeypatch):
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_STRING"):
        monkeypatch.delenv(key, raising=False)
    import app.services.external_signal_adapter as adapter

    adapter._CACHE["updated_at"] = 0
    adapter._CACHE["payload"] = None

    payload = fetch_sharkfx_external_signals(force_refresh=True)

    assert payload["available"] is False
    assert payload["source"] == "sharkfx_ru"
    assert payload["signals"] == []
    assert payload["reason"] == "telegram_credentials_missing"


def test_prop_score_external_signal_alignment(monkeypatch):
    import app.services.prop_signal_engine as engine

    monkeypatch.setattr(
        engine,
        "get_latest_sharkfx_signal",
        lambda symbol: {"source": "sharkfx_ru", "symbol": symbol, "action": "BUY", "entry": 1.0850, "sl": 1.0810, "tp": 1.0930},
    )

    score = build_prop_signal_score({"symbol": "EURUSD", "action": "BUY", "entry": 1.0850, "sl": 1.0810, "tp": 1.0930})

    assert score["external_signal_used"] is True
    assert score["external_signal_alignment"] == "aligned"
    assert score["external_signal_filter"]["score_delta"] == 5


def test_prop_score_external_signal_conflict_blocks(monkeypatch):
    import app.services.prop_signal_engine as engine

    monkeypatch.setattr(
        engine,
        "get_latest_sharkfx_signal",
        lambda symbol: {"source": "sharkfx_ru", "symbol": symbol, "action": "SELL", "entry": 1.0850, "sl": 1.0900, "tp": 1.0750},
    )

    score = build_prop_signal_score({"symbol": "EURUSD", "action": "BUY", "entry": 1.0850, "sl": 1.0810, "tp": 1.0930})

    assert score["external_signal_used"] is True
    assert score["external_signal_alignment"] == "conflict"
    assert score["external_signal_filter"]["score_delta"] == -10
    assert any("SharkFX против" in blocker for blocker in score["blockers"])
