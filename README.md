# NicolasSavin AI FOREX SIGNAL PLATFORM — Версия 3.3

Платформа на **FastAPI** с модульным backend, тёмным профессиональным frontend и API для live-сигналов на основе реальных данных yfinance.

## Что обновлено в версии 3.3
- Главная страница теперь сфокусирована только на блоке сигналов, без встроенных секций ideas/news/calendar/heatmap.
- Страницы `/ideas`, `/news`, `/calendar` и `/heatmap/page` сохранены и продолжают работать через существующие маршруты.
- Карточка сигнала расширена: инструмент, направление, entry, stop loss, take profit, время сигнала, статус, lifecycle (`active/open/closed`), уникальное описание, вероятность и прогресс к TP/SL.
- На frontend добавлено звуковое уведомление о новом сигнале через Web Audio API.
- На backend добавлен read-only контракт `GET /api/mt4/signals` для будущей интеграции сигналов в советник MT4 без выполнения сделок на стороне сервера.

## Stage 1 (Audit)
- Проверена структура репозитория и текущие маршруты.
- Зафиксированы основные API-контракты для frontend/backend.

## Backend modules
Основные backend-модули:
- `backend/data_provider.py`
- `backend/feature_builder.py`
- `backend/signal_engine.py`
- `backend/risk_engine.py`
- `backend/portfolio_engine.py`
- `app/services/mt4_bridge.py`

## API
Основные маршруты:
- `GET /health`
- `GET /signals/live`
- `GET /ideas/market`
- `GET /news/market`
- `GET /calendar/events`
- `GET /heatmap`
- `GET /api/mt4/signals` — read-only JSON-контракт для будущего polling из MT4/EA

Legacy:
- `GET /api/health`
- `GET /api/signals/{symbol}`

## Запуск
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
# либо совместимый entrypoint для деплоя
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Render
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
# либо если платформа запускает корневой модуль
uvicorn main:app --host 0.0.0.0 --port $PORT
```

## MT4 bridge contract
Эндпоинт `GET /api/mt4/signals` подготавливает инфраструктуру для будущего советника MT4:
- сервер отдаёт только read-only сигнал-фид;
- в контракте есть `schema_version`, `poll_interval_seconds`, `bridge_status` и массив `signals`;
- каждый сигнал содержит `symbol`, `side`, `entry`, `stop_loss`, `take_profit`, `probability_percent`, `signal_time_utc` и `expires_at_utc`;
- выполнение ордеров на стороне MT4 пока **не реализовано**.

## Новости рынка
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
