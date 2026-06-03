from app.services.future_delta_service import calculate_cum_delta_from_candles


def test_calculated_cum_delta_proxy_is_labelled():
    candles = []
    price = 1.1000
    for _ in range(30):
        candles.append({"open": price, "high": price + 0.0005, "low": price - 0.0002, "close": price + 0.0003, "tick_volume": 100})
        price += 0.0002

    payload = calculate_cum_delta_from_candles(candles)

    assert payload["available"] is True
    assert payload["source"] == "calculated_cum_delta_proxy"
    assert payload["is_proxy_metric"] is True
    assert payload["bias"] == "bullish"
    assert "proxy" in payload["label_ru"]
