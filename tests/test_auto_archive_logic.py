from __future__ import annotations

from pathlib import Path

from app import main


def test_evaluate_trade_result_by_price_buy_and_sell() -> None:
    buy_trade = {"signal": "BUY", "entry": 1.1, "sl": 1.09, "tp": 1.12}
    sell_trade = {"signal": "SELL", "entry": 1.1, "sl": 1.11, "tp": 1.08}

    buy_tp = main.evaluate_trade_result_by_price(buy_trade, 1.1201)
    buy_sl = main.evaluate_trade_result_by_price(buy_trade, 1.0899)
    sell_tp = main.evaluate_trade_result_by_price(sell_trade, 1.0799)
    sell_sl = main.evaluate_trade_result_by_price(sell_trade, 1.1101)

    assert buy_tp["is_closed"] is True and buy_tp["result"] == "TP"
    assert buy_sl["is_closed"] is True and buy_sl["result"] == "SL"
    assert sell_tp["is_closed"] is True and sell_tp["result"] == "TP"
    assert sell_sl["is_closed"] is True and sell_sl["result"] == "SL"


def test_evaluate_trade_result_by_price_skips_when_price_missing_or_wait() -> None:
    wait_trade = {"signal": "WAIT", "entry": 1.1, "sl": 1.09, "tp": 1.12}
    no_price = main.evaluate_trade_result_by_price(wait_trade, None)
    wait_result = main.evaluate_trade_result_by_price(wait_trade, 1.12)

    assert no_price["is_closed"] is False
    assert "не закрывается автоматически" in no_price["reason_ru"].lower()
    assert wait_result["is_closed"] is False
    assert "не является buy/sell" in wait_result["reason_ru"].lower()


def test_move_to_archive_updates_existing_by_id(tmp_path: Path, monkeypatch) -> None:
    archive_file = tmp_path / "archive.json"
    monkeypatch.setattr(main, "ARCHIVE_FILE", archive_file)

    first = {"id": "EURUSD-BUY", "result": "TP", "status": "CLOSED_TP"}
    second = {"id": "EURUSD-BUY", "result": "SL", "status": "CLOSED_SL", "closed_price": 1.1}

    main.move_to_archive(first)
    main.move_to_archive(second)

    archive = main.load_json(archive_file)
    assert len(archive) == 1
    assert archive[0]["id"] == "EURUSD-BUY"
    assert archive[0]["result"] == "SL"
    assert archive[0]["closed_price"] == 1.1


def test_is_real_market_price_available_accepts_real_delayed_or_live() -> None:
    assert main.is_real_market_price_available({"data_status": "real"}) is True
    assert main.is_real_market_price_available({"data_status": "delayed"}) is True
    assert main.is_real_market_price_available({"data_status": "unavailable", "is_live_market_data": True}) is True
    assert main.is_real_market_price_available({"data_status": "unavailable", "is_live_market_data": False}) is False
