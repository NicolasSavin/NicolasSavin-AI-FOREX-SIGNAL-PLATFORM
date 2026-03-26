from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app, canonical_market_service
from app.services.chart_data_service import ChartDataService
from app.services.market_providers import _TIMEFRAME_TO_TD, _td_symbol


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



def test_chart_data_service_treats_blank_env_key_as_missing(monkeypatch) -> None:
    monkeypatch.setenv('TWELVEDATA_API_KEY', '   ')
    service = ChartDataService()

    payload = service.get_chart('GBPUSD', 'H1')

    assert payload['status'] == 'unavailable'
    assert 'TWELVEDATA_API_KEY' in payload['message_ru']


def test_chart_data_service_returns_unavailable_without_key(monkeypatch) -> None:
    service = ChartDataService()
    monkeypatch.setattr(service, 'api_key', '')

    payload = service.get_chart('GBPUSD', 'H1')

    assert payload['status'] == 'unavailable'
    assert payload['candles'] == []
    assert 'TWELVEDATA_API_KEY' in payload['message_ru']


def test_chart_route_returns_candles_array(monkeypatch) -> None:
    monkeypatch.setattr(
        canonical_market_service,
        'get_chart_contract',
        lambda symbol, timeframe, limit=120: {
            'symbol': symbol,
            'timeframe': timeframe,
            'source': 'twelvedata',
            'data_status': 'real',
            'source_symbol': 'EUR/USD',
            'last_updated_utc': '2026-03-20T10:30:00+00:00',
            'is_live_market_data': True,
            'candles': [
                {'time': 1710929700, 'open': 1.086, 'high': 1.087, 'low': 1.085, 'close': 1.0865}
            ],
        },
    )

    client = TestClient(app)
    response = client.get('/api/chart/EURUSD?tf=M15')

    assert response.status_code == 200
    assert response.json() == [
        {'time': 1710929700, 'open': 1.086, 'high': 1.087, 'low': 1.085, 'close': 1.0865}
    ]


def test_chart_route_returns_fallback_on_service_exception(monkeypatch) -> None:
    def _boom(symbol, timeframe, limit=120):
        raise RuntimeError('boom')

    monkeypatch.setattr(canonical_market_service, 'get_chart_contract', _boom)

    client = TestClient(app)
    response = client.get('/api/chart/EURUSD?tf=M15')

    assert response.status_code == 200
    assert response.json() == []


def test_market_endpoint_never_returns_synthetic_contract_fields() -> None:
    client = TestClient(app)
    response = client.get('/api/market?symbols=EURUSD')
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get('market'), list)
    row = payload['market'][0]
    assert row['data_status'] in {'real', 'unavailable', 'delayed'}
    assert row['source'] in {'twelvedata', 'yahoo_finance'}
    assert isinstance(row['source_symbol'], str)
    assert isinstance(row['last_updated_utc'], str)
    assert isinstance(row['is_live_market_data'], bool)


def test_twelvedata_symbol_and_timeframe_mapping_contract() -> None:
    assert _td_symbol('EURUSD') == 'EUR/USD'
    assert _td_symbol('GBPUSD') == 'GBP/USD'
    assert _td_symbol('USDJPY') == 'USD/JPY'
    assert _td_symbol('XAUUSD') == 'XAU/USD'

    assert _TIMEFRAME_TO_TD['M15'] == '15min'
    assert _TIMEFRAME_TO_TD['H1'] == '1h'
    assert _TIMEFRAME_TO_TD['H4'] == '4h'


def test_twelvedata_debug_health_endpoint(monkeypatch) -> None:
    provider = canonical_market_service.live_provider
    monkeypatch.setattr(provider, 'api_key', 'debug-key')
    monkeypatch.setattr(
        provider,
        'get_candles',
        lambda symbol, timeframe, limit: {
            'symbol': symbol,
            'timeframe': timeframe,
            'source_symbol': 'EUR/USD',
            'candles': [{'time': 1710929700, 'open': 1.086, 'high': 1.087, 'low': 1.085, 'close': 1.0865}],
            'error': None,
        },
    )

    client = TestClient(app)
    response = client.get('/api/debug/twelvedata-health')
    assert response.status_code == 200
    payload = response.json()

    assert payload['provider'] == 'twelvedata'
    assert payload['api_key_present'] is True
    assert payload['symbol_mapping']['EURUSD'] == 'EUR/USD'
    assert payload['timeframe_mapping']['M15'] == '15min'
    assert payload['probe']['provider_symbol'] == 'EUR/USD'
    assert payload['probe']['provider_interval'] == '15min'
    assert payload['probe']['candles_count'] == 1
    assert payload['probe']['request_succeeded'] is True
