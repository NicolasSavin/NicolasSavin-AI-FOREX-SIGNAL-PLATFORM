from backend.analysis.candle_validation import CandleValidationService
from backend.feature_builder import FeatureBuilder


def test_candle_validation_excludes_only_invalid_and_marks_outlier() -> None:
    candles = [
        {"time": 1, "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105},
        {"time": 2, "open": 1.105, "high": 1.115, "low": 1.10, "close": 1.11},
        {"time": 2, "open": 1.11, "high": 1.20, "low": 1.10, "close": 1.12},  # duplicate ts -> excluded
        {"time": 4, "open": 1.12, "high": 1.30, "low": 1.11, "close": 1.121},  # suspicious wick/range
        {"time": 5, "open": -1.0, "high": -0.9, "low": -1.1, "close": -1.0},  # invalid
    ]
    result = CandleValidationService().validate(candles, symbol="EURUSD", timeframe="H1")

    assert result.excluded_count == 2
    assert len(result.valid_candles) == 3
    assert result.data_quality["has_warnings"] is True
    assert "duplicated_timestamp" in result.data_quality["warning_codes"]
    assert any(item.get("data_quality") == "suspicious" for item in result.valid_candles)


def test_feature_builder_exposes_data_quality_without_breaking_contract() -> None:
    candles = [
        {"open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105},
        {"open": 1.105, "high": 1.115, "low": 1.10, "close": 1.11},
        {"open": 1.11, "high": 1.112, "low": 1.101, "close": 1.102},
        {"open": 1.102, "high": 1.103, "low": 1.09, "close": 1.091},
    ]

    features = FeatureBuilder().build({"data_status": "real", "symbol": "EURUSD", "timeframe": "M15", "candles": candles})
    assert "liquidity_sweep" in features
    assert "data_quality" in features
    assert "data_warnings" in features
    assert "missing_timestamp" in features["data_warnings"]
