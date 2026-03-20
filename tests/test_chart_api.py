from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app, chart_data_service
from app.services.chart_data_service import ChartDataService


def test_chart_data_service_normalizes_twelvedata_payload(monkeypatch) -> None:
    service = ChartDataService()
    monkeypatch.setattr(service, 'api_key', 'test-key')

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                'values': [
                    {'datetime': '2026-03-20 10:30:00', 'open': '1.0865', 'high': '1.0880', 'low': '1.0861', 'close': '1.0878'},
                    {'datetime': '2026-03-20 10:15:00', 'open': '1.0860', 'high': '1.0870', 'low': '1.0850', 'close': '1.0865'},
                ]
            }

    monkeypatch.setattr('app.services.chart_data_service.requests.get', lambda *args, **kwargs: _Response())

    payload = service.get_chart('EURUSD', 'M15')

    assert payload['status'] == 'ok'
    assert payload['symbol'] == 'EURUSD'
    assert payload['timeframe'] == 'M15'
    assert payload['candles'][0]['open'] == 1.086
    assert payload['candles'][0]['time'] < payload['candles'][1]['time']


def test_chart_data_service_returns_unavailable_without_key(monkeypatch) -> None:
    service = ChartDataService()
    monkeypatch.setattr(service, 'api_key', '')

    payload = service.get_chart('GBPUSD', 'H1')

    assert payload['status'] == 'unavailable'
    assert payload['candles'] == []
    assert 'TWELVEDATA_API_KEY' in payload['message_ru']


def test_chart_route_returns_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        chart_data_service,
        'get_chart',
        lambda symbol, timeframe: {
            'symbol': symbol,
            'timeframe': timeframe,
            'source': 'twelvedata',
            'status': 'ok',
            'message_ru': None,
            'candles': [
                {'time': 1710929700, 'open': 1.086, 'high': 1.087, 'low': 1.085, 'close': 1.0865}
            ],
            'meta': {'provider': 'Twelve Data', 'interval': '15min', 'outputsize': 1},
        },
    )

    client = TestClient(app)
    response = client.get('/api/chart/EURUSD?tf=M15')

    assert response.status_code == 200
    assert response.json()['candles'][0]['close'] == 1.0865
