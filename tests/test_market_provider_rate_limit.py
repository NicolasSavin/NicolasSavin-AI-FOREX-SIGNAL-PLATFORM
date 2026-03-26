from datetime import datetime, timezone

import pandas as pd

from app.services.market_providers import YahooProvider


class _Ticker:
    calls = 0

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, period: str, interval: str):
        _Ticker.calls += 1
        idx = pd.date_range(end=datetime.now(timezone.utc), periods=20, freq="1h")
        return pd.DataFrame(
            {
                "Open": [1.1 + i * 0.0001 for i in range(20)],
                "High": [1.101 + i * 0.0001 for i in range(20)],
                "Low": [1.099 + i * 0.0001 for i in range(20)],
                "Close": [1.1005 + i * 0.0001 for i in range(20)],
            },
            index=idx,
        )


def test_yahoo_h4_is_derived_from_cached_h1(monkeypatch) -> None:
    monkeypatch.setattr("app.services.market_providers.yf.Ticker", _Ticker)
    provider = YahooProvider()

    h4 = provider.get_candles("EURUSD", "H4", 5)
    h1 = provider.get_candles("EURUSD", "H1", 5)

    assert _Ticker.calls == 1
    assert h4["timeframe"] == "H4"
    assert len(h4["candles"]) > 0
    assert len(h1["candles"]) > 0
