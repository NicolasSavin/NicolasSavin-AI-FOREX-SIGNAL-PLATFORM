from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calculators.orderflow import build_orderflow_signal
from providers.databento.provider import DatabentoOrderflowProvider


def test_unavailable_provider_does_not_fake_data(monkeypatch):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    monkeypatch.delenv("DATABENTO_DATASET", raising=False)

    snapshot = DatabentoOrderflowProvider().load_snapshot("eurusd")
    signal = build_orderflow_signal(snapshot)

    assert snapshot.data_status == "unavailable"
    assert snapshot.delta is None
    assert signal.side == "neutral"
    assert signal.confidence == 0


def test_real_bid_ask_snapshot_builds_buy_signal():
    snapshot = DatabentoOrderflowProvider.from_bid_ask("EURUSD", bid_volume=100, ask_volume=160)
    signal = build_orderflow_signal(snapshot)

    assert signal.data_status == "real"
    assert signal.side == "buy"
    assert signal.metric_kind == "real_market_metric"
