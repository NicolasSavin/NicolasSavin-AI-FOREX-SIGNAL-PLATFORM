# NicolasSavin AI FOREX SIGNAL PLATFORM — Версия 3.4

Платформа на **FastAPI** с модульным backend, тёмным профессиональным frontend и подготовленными API-контрактами для live-сигналов, news alert и будущей интеграции с MT4.

## Что обновлено в версии 3.4
- Главная страница оставляет только поток сигналов и news banner; страницы `/ideas`, `/news`, `/calendar` и `/heatmap/page` сохранены и продолжают работать через прежние маршруты.
- Карточка сигнала расширена и теперь показывает инструмент, дату/время выхода сигнала, направление BUY/SELL, точку входа, Stop Loss, Take Profit, время сигнала, статус, состояние (`active/open/closed`), уникальное описание, вероятность и прогресс до TP/SL.
- На frontend добавлены анимированная шкала вероятности, крупный график сигнала, уровни, зоны liquidity/order block, projected candles и блок важных новостей по инструменту.
- Добавлены отдельные звуки для нового сигнала и нового news alert с защитой от дублей и повторов при ререндере.
- Backend расширен типизированными моделями сигналов и новостей, ручным ingest слоем, fallback/mock-слоем и MT4 export endpoint без реализации самого советника.

## Backend modules
Основные backend-модули:
- `backend/data_provider.py`
- `backend/feature_builder.py`
- `backend/signal_engine.py`
- `backend/risk_engine.py`
- `backend/portfolio_engine.py`
- `backend/news_provider.py`
- `app/services/signal_hub.py`
- `app/services/news_service.py`
- `app/services/mt4_bridge.py`

## API
### Базовые маршруты
- `GET /health`
- `GET /api/health`
- `GET /signals/live`
- `GET /ideas/market`
- `GET /news/market`
- `GET /calendar/events`
- `GET /heatmap`

### Новый API сигналов
- `GET /api/signals`
- `GET /api/signals/active`
- `GET /api/signals/{id}` — поддерживает и lookup по `signal_id`, и обратную совместимость для symbol lookup
- `GET /api/signals/{id}/news`
- `POST /api/signals`
- `PATCH /api/signals/{id}/status`
- `GET /api/signals/lookup/{symbol}`
- `GET /api/legacy/signals/{symbol}`

### Новый API новостей
- `GET /api/news`
- `GET /api/news/relevant`
- `GET /api/news/{id}`
- `POST /api/news/webhook`
- `POST /api/news/ingest`

### Подготовка к MT4
- `GET /api/mt4/signals` — read-only polling контракт для будущего советника
- `POST /api/mt4/export` — подготовка export payload для MT4/bridge слоя

## Контракты данных
### Расширенная модель сигнала
Сигнал теперь поддерживает:
- `id/signal_id`
- `instrument/symbol`
- `signalDateTime`
- `side/action`
- `entry`
- `stopLoss/stop_loss`
- `takeProfit/take_profit`
- `signalTime`
- `status`
- `state`
- `description`
- `probability`
- `progressToTP`
- `progressToSL`
- `chartData`
- `zones`
- `levels`
- `liquidityAreas`
- `projectedCandles`
- `relatedNews`
- `createdAt/created_at_utc`
- `updatedAt/updated_at_utc`

### Модель news alert
Новость нормализуется в контракт с полями:
- `id`
- `title`
- `description`
- `instrument`
- `relatedInstruments`
- `currency`
- `impact`
- `eventTime`
- `publishedAt/published_at`
- `status`
- `source`
- `isRelevantToSignal`
- `relatedSignalIds`
- `soundPlayed`
- `createdAt`
- `updatedAt`

## Архитектура данных
Система подготовлена для следующих источников:
- mock data / fallback слой;
- REST API;
- WebSocket в будущем;
- внешний новостной сервис;
- экономический календарь.

Если внешние данные недоступны, платформа:
- не выдумывает рыночные котировки;
- честно отмечает `data_status=unavailable`;
- использует безопасные fallback/mock структуры только для UI-представления графика, projected candles и прогресса.

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

## Новости рынка
- Раздел `/news` продолжает работать как отдельная страница.
- На главной появился news banner с релевантными событиями средней и высокой важности.
- В карточке сигнала отображается блок «Внимание: важная новость», если событие связано с инструментом.
- Поддерживаются открытые RSS-источники и ручной ingest через API.
- Один и тот же news alert не должен проигрывать звук повторно после ререндера.

## MT4 bridge contract
Эндпоинты `GET /api/mt4/signals` и `POST /api/mt4/export` подготавливают инфраструктуру для будущего советника MT4:
- сервер отдаёт read-only сигнал-фид;
- export payload сохраняется как очередь для bridge-слоя;
- в контракте есть `magicNumber`, `riskPercent`, `brokerSymbol`, `timeframe`, `comment`;
- выполнение ордеров на стороне MT4 пока **не реализовано**.

## Принципы данных
- Сигналы строятся на реальных OHLCV-данных из yfinance там, где они доступны.
- Система не выдумывает недоступные рыночные данные.
- При нехватке данных возвращается fallback/mock UI-слой с честной маркировкой.
- Proxy-метрики явно маркируются `label=proxy`.
