from __future__ import annotations

from app.main import DEFAULT_PAIRS, app, signal_engine


@app.get('/signals')
async def legacy_signals_feed():
    """Совместимость со старым entrypoint: возвращает текущие live-сигналы."""
    signals = await signal_engine.generate_live_signals(DEFAULT_PAIRS)
    return {
        'status': 'ok',
        'signals': signals,
        'message': 'legacy feed mapped to /signals/live',
    }


@app.get('/pairs')
async def legacy_pairs():
    return {
        'status': 'ok',
        'pairs': DEFAULT_PAIRS,
    }
