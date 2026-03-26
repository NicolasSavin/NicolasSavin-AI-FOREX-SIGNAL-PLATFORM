from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from backend.market_data import MarketDataService


def test_legacy_backend_market_data_uses_no_synthetic_generation() -> None:
    candles = MarketDataService().get_candles(symbol='EURUSD', timeframe='H1', count=10)
    assert isinstance(candles, list)
    # synthetic random generator removed: service can return only provider candles or empty list
    assert all({'open', 'high', 'low', 'close'}.issubset(set(item.keys())) for item in candles)


def test_price_endpoint_returns_strict_contract() -> None:
    client = TestClient(app)
    response = client.get('/api/price/EURUSD')
    assert response.status_code == 200
    row = response.json()
    assert row['data_status'] in {'real', 'unavailable', 'delayed'}
    assert isinstance(row['source'], str)
    assert isinstance(row['source_symbol'], str)
    assert isinstance(row['last_updated_utc'], str)
    assert isinstance(row['is_live_market_data'], bool)
