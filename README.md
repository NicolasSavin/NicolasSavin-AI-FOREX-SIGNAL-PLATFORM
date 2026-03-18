# NicolasSavin AI FOREX SIGNAL PLATFORM — Версия 3.2

Платформа на **FastAPI** с модульным backend skeleton (Stage 1/2), статическим тёмным frontend и API для live-сигналов на основе реальных данных yfinance.

## Stage 1 (Audit)
- Проверена структура репозитория и текущие маршруты.
- Зафиксированы основные API-контракты для frontend/backend.

## Stage 2 (Backend skeleton)
Добавлены модули:
- `backend/data_provider.py`
- `backend/feature_builder.py`
- `backend/signal_engine.py`
- `backend/risk_engine.py`
- `backend/portfolio_engine.py`

## API
- `GET /health`
- `GET /signals/live`
- `GET /ideas/market`
- `GET /news/market`
- `GET /calendar/events`
- `GET /heatmap`

Legacy:
- `GET /api/health`
- `GET /api/signals/{symbol}`

## Запуск
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Render
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### Новости рынка
- Блок новостей подключён к RSS-источнику и по умолчанию использует Google News RSS по поисковым запросам `forex market` и `central bank forex`.
- Источники можно переопределить через переменную окружения `NEWS_FEED_URLS` (список RSS URL через запятую).
- Дополнительные параметры:
  - `NEWS_MAX_ITEMS` — сколько новостей отдавать (по умолчанию `6`)
  - `NEWS_CACHE_TTL_SECONDS` — TTL кэша в памяти (по умолчанию `300`)
  - `NEWS_REQUEST_TIMEOUT_SECONDS` — таймаут запросов к RSS (по умолчанию `10`)
- Если RSS-источник не отвечает, API честно возвращает fallback-статус о недоступности канала и не выдумывает новости.

## Принципы данных
- Сигналы строятся на реальных OHLCV-данных из yfinance (D1/H1/M15).
- Система не выдумывает недоступные рыночные данные.
- При нехватке данных возвращается `NO_TRADE`.
- Proxy-метрики явно маркируются `label=proxy`.
