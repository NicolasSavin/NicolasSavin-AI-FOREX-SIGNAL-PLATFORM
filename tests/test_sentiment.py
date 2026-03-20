from __future__ import annotations

from backend.sentiment_provider import ExternalSentimentProvider, MockSentimentProvider


def test_mock_sentiment_returns_supported_contract() -> None:
    snapshot = MockSentimentProvider().get_snapshot("EURUSD")

    assert snapshot.symbol == "EURUSD"
    assert snapshot.data_status == "mock"
    assert -1.0 <= snapshot.sentiment_score <= 1.0
    assert 0.0 <= snapshot.confidence <= 1.0


def test_contrarian_logic_turns_bearish_when_retail_too_long() -> None:
    provider = MockSentimentProvider()
    snapshot = provider.build_snapshot(
        symbol="EURUSD",
        source="test",
        long_pct=72,
        short_pct=28,
        data_status="mock",
    )

    assert snapshot.retail_bias == "bullish"
    assert snapshot.contrarian_bias == "bearish"
    assert snapshot.extreme is True
    assert snapshot.sentiment_score < 0


def test_external_provider_falls_back_safely_without_base_url() -> None:
    snapshot = ExternalSentimentProvider(base_url="", api_key="").get_snapshot("GBPUSD")

    assert snapshot.symbol == "GBPUSD"
    assert snapshot.data_status == "unavailable"
    assert snapshot.sentiment_score == 0.0
    assert snapshot.confidence == 0.0
