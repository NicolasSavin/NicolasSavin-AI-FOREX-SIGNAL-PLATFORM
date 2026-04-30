from __future__ import annotations

from fastapi.responses import FileResponse

from app.main import DEFAULT_PAIRS, app, signal_engine
from app.main import canonical_market_service
from backend.signal_engine import SUPPORTED_TIMEFRAMES


@app.get('/analytics', include_in_schema=False)
async def analytics_page():
    """Страница AI-аналитики рынка."""
    return FileResponse('app/static/analytics.html')


@app.get('/signals')
async def legacy_signals_feed():
    """Совместимость со старым entrypoint: возвращает текущие live-сигналы."""
    signals = await signal_engine.generate_live_signals(DEFAULT_PAIRS)
    return {
        'status': 'ok',
        'signals': signals,
        'market': [canonical_market_service.get_market_contract(symbol) for symbol in DEFAULT_PAIRS],
        'message': 'legacy feed mapped to /signals/live',
    }


@app.get('/pairs')
async def legacy_pairs():
    return {
        'status': 'ok',
        'pairs': DEFAULT_PAIRS,
        'timeframes': SUPPORTED_TIMEFRAMES,
    }
