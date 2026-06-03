from app.services.prop_signal_engine import enrich_idea_with_prop_score


def _candles(count: int = 35, start: float = 1.1000, step: float = 0.0002):
    rows = []
    price = start
    for _ in range(count):
        price += step
        rows.append(
            {
                "open": price - step / 2,
                "high": price + abs(step) * 1.5,
                "low": price - abs(step) * 1.5,
                "close": price,
            }
        )
    return rows


def test_prop_signal_uses_candle_direction_and_atr_levels_when_wait():
    idea = {"symbol": "EURUSD", "signal": "WAIT", "chartData": _candles(35)}

    enriched = enrich_idea_with_prop_score(idea)
    score = enriched["prop_signal_score"]
    geometry = score["trade_geometry"]

    assert enriched["signal"] == "BUY"
    assert enriched["action"] == "BUY"
    assert enriched["final_signal"] == "BUY"
    assert enriched["direction"] == "BUY"
    assert enriched["entry"] == enriched["entry_price"]
    assert enriched["sl"] == enriched["stop_loss"]
    assert enriched["tp"] == enriched["take_profit"]
    assert enriched["entry_source"] == "atr_fallback"
    assert enriched["sl"] < enriched["entry"] < enriched["tp"]
    assert enriched["risk_reward"] >= 1.10
    assert enriched["advisor_signal"]["allowed"] is True
    assert score["direction"] == "BUY"
    assert geometry["level_source"] == "atr_fallback"
    assert geometry["fallback_used"] is True
    assert geometry["candles_count"] == 35
    assert "Направление BUY/SELL" not in score["missing_inputs"]
    assert "Entry / SL / TP" not in score["missing_inputs"]
    assert "Реальные свечи" not in score["missing_inputs"]


def test_prop_signal_blocks_when_sentiment_conflicts_with_fallback_direction():
    idea = {
        "symbol": "EURUSD",
        "action": "WAIT",
        "chart_data": {"candles": _candles(32)},
        "sentiment": {"bias": "bullish_usd", "score": 0.7},
    }

    enriched = enrich_idea_with_prop_score(idea)

    assert enriched["action"] == "BUY"
    assert enriched["prop_signal_score"]["sentiment_filter"]["alignment"] == "conflict"
    assert enriched["advisor_signal"]["allowed"] is False
