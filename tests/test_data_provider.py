from __future__ import annotations

from datetime import datetime

import pandas as pd

from backend.data_provider import DataProvider


def test_h4_aggregation_resamples_hourly_candles() -> None:
    provider = DataProvider()
    idx = pd.date_range("2026-03-20 00:00:00+00:00", periods=8, freq="1h")
    frame = pd.DataFrame(
        {
            "Open": [1.1000, 1.1010, 1.1020, 1.1030, 1.1040, 1.1050, 1.1060, 1.1070],
            "High": [1.1015, 1.1025, 1.1035, 1.1045, 1.1055, 1.1065, 1.1075, 1.1085],
            "Low": [1.0990, 1.1000, 1.1010, 1.1020, 1.1030, 1.1040, 1.1050, 1.1060],
            "Close": [1.1010, 1.1020, 1.1030, 1.1040, 1.1050, 1.1060, 1.1070, 1.1080],
            "Volume": [10, 20, 30, 40, 50, 60, 70, 80],
        },
        index=idx,
    )

    aggregated = provider._aggregate_to_h4(frame)

    assert len(aggregated) == 2
    first = aggregated.iloc[0]
    second = aggregated.iloc[1]

    assert first["Open"] == 1.1
    assert first["High"] == 1.1045
    assert first["Low"] == 1.099
    assert first["Close"] == 1.104
    assert first["Volume"] == 100

    assert second["Open"] == 1.104
    assert second["High"] == 1.1085
    assert second["Low"] == 1.103
    assert second["Close"] == 1.108
    assert second["Volume"] == 260


def test_snapshot_contract_has_standard_market_fields(monkeypatch) -> None:
    provider = DataProvider()

    class _Ticker:
        def history(self, period: str, interval: str):
            idx = pd.date_range("2026-03-20 00:00:00+00:00", periods=2, freq="1h")
            return pd.DataFrame(
                {
                    "Open": [1.1, 1.11],
                    "High": [1.12, 1.13],
                    "Low": [1.09, 1.1],
                    "Close": [1.11, 1.12],
                    "Volume": [100, 120],
                },
                index=idx,
            )

    monkeypatch.setattr("backend.data_provider.yf.Ticker", lambda symbol: _Ticker())
    payload = provider.snapshot_sync("EURUSD", timeframe="H1")

    assert payload["data_status"] == "real"
    assert payload["source"] == "Yahoo Finance"
    assert payload["source_symbol"] == "EURUSD=X"
    assert payload["is_live_market_data"] is True
    assert payload["timeframe"] == "H1"
    assert datetime.fromisoformat(payload["last_updated_utc"])
