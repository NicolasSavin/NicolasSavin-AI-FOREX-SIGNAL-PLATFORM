from app.services.smc_detector import SmcDetector


def _base_candles(count: int = 32) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    price = 1.1000
    for i in range(count):
        open_price = price
        close_price = price + (0.0002 if i % 2 == 0 else -0.00015)
        high = max(open_price, close_price) + 0.0002
        low = min(open_price, close_price) - 0.0002
        candles.append({"open": open_price, "high": high, "low": low, "close": close_price})
        price = close_price
    return candles


def test_smc_detector_returns_empty_when_not_enough_candles() -> None:
    detector = SmcDetector(min_candles=30)
    overlays = detector.detect(_base_candles(20))

    assert overlays == {"order_blocks": [], "fvg": [], "liquidity": []}


def test_smc_detector_detects_order_blocks_fvg_and_liquidity() -> None:
    detector = SmcDetector(min_candles=30)
    candles = _base_candles(34)

    # Demand OB: bearish candle then bullish impulse above previous high.
    candles[20] = {"open": 1.1012, "high": 1.1013, "low": 1.1005, "close": 1.1006}
    candles[21] = {"open": 1.1007, "high": 1.1033, "low": 1.1006, "close": 1.1030}

    # Supply OB: bullish candle then bearish impulse below previous low.
    candles[24] = {"open": 1.1022, "high": 1.1028, "low": 1.1021, "close": 1.1027}
    candles[25] = {"open": 1.1026, "high": 1.1027, "low": 1.0995, "close": 1.0998}

    # Bullish FVG between i-1 and i+1 around i=28.
    candles[27] = {"open": 1.1000, "high": 1.1002, "low": 1.0996, "close": 1.0997}
    candles[28] = {"open": 1.0998, "high": 1.1010, "low": 1.0997, "close": 1.1009}
    candles[29] = {"open": 1.1015, "high": 1.1022, "low": 1.1014, "close": 1.1020}

    # Equal highs/lows liquidity pools.
    candles[30]["high"] = 1.1035
    candles[31]["high"] = 1.10349
    candles[32]["low"] = 1.0990
    candles[33]["low"] = 1.09901

    overlays = detector.detect(candles)

    assert overlays["order_blocks"]
    assert any(item["type"] == "demand" for item in overlays["order_blocks"])
    assert any(item["type"] == "supply" for item in overlays["order_blocks"])

    assert overlays["fvg"]
    assert any(item["high"] > item["low"] for item in overlays["fvg"])

    assert overlays["liquidity"]
    assert any(item["type"] == "buy_side" for item in overlays["liquidity"])
    assert any(item["type"] == "sell_side" for item in overlays["liquidity"])
