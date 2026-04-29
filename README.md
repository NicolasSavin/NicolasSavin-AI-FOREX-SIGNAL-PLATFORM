# NicolasSavin AI FOREX SIGNAL PLATFORM — Версия 3.8

Платформа на **FastAPI** с модульным backend, тёмным профессиональным frontend и подготовленными API-контрактами для live-сигналов, news alert и будущей интеграции с MT4.

## Что обновлено в версии 3.8
- Добавлен единый `CanonicalMarketService` и интерфейс `RealMarketDataProvider` для всех рыночных endpoint: `GET /price/{symbol}`, `GET /market`, `GET /chart/{symbol}/{tf}`, а также `GET /api/price/{symbol}`, `GET /api/market`, `GET /api/canonical/chart/{symbol}/{tf}`.
- Trade idea графики переведены на server-side snapshot pipeline: при создании идеи backend получает реальные OHLC candles, строит PNG через `matplotlib`, сохраняет файл в `/static/charts/{symbol}_{timeframe}_{timestamp}.png` и возвращает путь как `chartImageUrl` (повторная генерация в modal больше не выполняется).
- Исправлено восстановление legacy ideas без `chartImageUrl`: при наличии реальных candles снапшот теперь строится независимо от устаревшего `chartSnapshotStatus=rate_limited`, а при ошибке рендера статус принудительно переводится в `snapshot_failed`.
- Добавлен безопасный one-time maintenance endpoint `POST /api/ideas/recover-missing-chart-snapshots` для ручного восстановления старых идей без дублирования `idea_id` и без изменения lifecycle TP/SL.
- Основной live-провайдер теперь `TwelveDataProvider`; `YahooProvider` сохранён только как optional historical fallback для свечей (статус `delayed`) и больше не используется как live пользовательская цена.
- Введён строгий контракт market-data ответа: `data_status` (`real | unavailable | delayed`), `source`, `source_symbol`, `last_updated_utc`, `is_live_market_data`.
- Удалены тихие synthetic fallback цены для production-endpoint; при ошибках источника API возвращает `unavailable` и frontend показывает warning по live-ценам.
- Добавлен polling-апдейтер текущих цен в карточках сигналов через `/api/market` (без выдуманных значений).
- Production hardening: `GET/HEAD /` и `GET/HEAD /health` стабилизированы для Render health-check, `/ideas/market` больше не запускает fanout на весь `DEFAULT_PAIRS`, а live-сигналы и market-candles получили TTL-кэш/деградацию без synthetic чисел.
- Для снижения rate-limit в historical fallback реализовано H4-агрегирование из кэшированных H1 свечей (без отдельного внешнего запроса H4), а market-контракт дополнен `current_price` и унифицированным `timeframe`.
- Добавлен backend-only AI forex assistant: новый модуль `backend/chat_service.py` и endpoint `POST /api/chat` с безопасным forex-only prompt, без API-ключей на frontend.
- Добавлен sentiment layer: `backend/sentiment_provider.py`, модель `SentimentSnapshot`, safe mock/external provider схема и интеграция sentiment в analytics response и signal engine как подтверждающий фактор.
- Добавлена persistent trade idea система: идеи теперь имеют `idea_id`, хранятся в JSON storage, не исчезают между обновлениями и обновляются по тем же `symbol + timeframe + setup_type`, пока жизненный цикл активен.
- Backend генерации `/api/ideas` теперь сначала берёт реальные свечи через `ChartDataService` (тот же источник, что и `GET /api/chart/{symbol}?tf={timeframe}`), вычисляет `latest_close`, передаёт в AI последние 40 свечей и валидирует `entry/stopLoss/takeProfit` по deviation-лимитам `M15 0.3% / H1 0.5% / H4 1.0%`; при ошибке AI автоматически включается market-aligned fallback с `levels_source`, `levels_validated`, `entry_deviation_pct` и `meta`.
- Генерация текста trade idea переработана в execution-ready narrative: SMC/ICT, паттерны, объёмы, дивергенции, CumDelta, фундаментал, волны, Wyckoff и sentiment теперь сшиваются в один торговый сценарий с явным trigger, confirmation, invalidation и target без изменения UI и маршрутов `/ideas`.
- Narrative generator полностью переписан: full-text идея теперь всегда формирует единый причинно-следственный абзац (5–8 предложений) с явной логикой "что сделала цена → где ликвидность → что подтверждает bias → чего ждать дальше → что отменяет сценарий", а при `data_status=unavailable` возвращается нейтральное объяснение без выдуманных рыночных данных.
- Endpoint `/ideas/market` остаётся обратносуместимым для текущего UI: старые поля сохранены, но дополнительно всегда возвращаются `idea_id`, `symbol`, `timeframe`, `status`, `sentiment`, `version`, `change_summary`.
- Страница `/ideas` теперь сохраняет прежний layout, но разделяет short scenario в списке и desk-style full card в modal detail-view: краткая карточка остаётся компактной, а полная показывает `detail_brief`, сценарии, аналитические секции и итоговый trading plan.
- Добавлена статистика по закрытым торговым идеям: для каждой archived-идеи автоматически считаются `result`, `entry_price`, `exit_price`, `pnl_percent`, `rr`, `duration`, а в `GET /ideas/market` возвращается агрегированный блок `statistics` (`total_trades`, `wins`, `losses`, `winrate`, `avg_rr`, `avg_pnl`, `max_win`, `max_loss`).
- Усилен pipeline `/api/ideas`: при наличии исторических свечей теперь всегда публикуется минимум один сценарий на symbol/timeframe (включая fallback `range/developing`), убраны жёсткие блокировки по live snapshot/current_price, а debug-логирование дополнено этапами `candles_count / features_built / signal_created / reason_if_skipped`.
- Идеи переведены в stateful lifecycle-модель: `CREATED → WAITING → TRIGGERED → ACTIVE → TP_HIT / SL_HIT → ARCHIVED`, а обновления теперь пишутся в `updates[]` с timestamp/event/explanation и не создают новые карточки, если совпадает `symbol + timeframe`.
- Добавлен structured analysis engine (`smc`, `ict`, `pattern`, `harmonic_pattern`, `volume`, `cum_delta`, `divergence`, `fundamental`) и weighted scoring decision model с полем `decision.weighted_score` и причинно-следственным `current_reasoning`.
- Основной путь narrative для trade ideas переведён на LLM-first сервис `app/services/idea_narrative_llm.py`: backend отправляет в OpenRouter только факты анализа (symbol/timeframe/direction/status/entry/SL/TP/RR + SMC/ICT/pattern/volume/delta/divergence/fundamental + delta изменений), валидирует строгий JSON-ответ, делает один retry при невалидном формате и включает детерминированный fallback только как аварийный режим (`narrative_source: llm|fallback`).
- Для trade ideas добавлен единый `unified_narrative`: Grok/OpenRouter теперь генерирует один связный текст в формате `SITUATION → CAUSE → EFFECT → ACTION → RISK`; frontend рендерит этот блок как основное объяснение с fallback на legacy `full_text`.

## Что обновлено в версии 3.7
- Добавлен отдельный модуль графических паттернов: `backend/pattern_detector.py` и `backend/pattern_visualization.py`.
- Паттерны (`Double Top/Bottom`, `Head and Shoulders`, `Inverse Head and Shoulders`, `Triangle`, `Wedge`, `Flag`) теперь определяются по OHLCV-свечам как дополнительный подтверждающий фактор, а не как замена текущего signal scoring.
- Контракты сигналов и analytics расширены полями `chartPatterns`, `patternSummary`, `patternSignalImpact`, а в analytics feature layer добавлен `patternFeatures` и отдельная pattern-компонента composite score.
- На главной странице в detail-view сигнала появился блок «Графические паттерны», а chart annotations научились рисовать линии, точки, breakout, target и invalidation уровни паттернов.
- Добавлены минимальные тесты detector/API для пустых данных, коротких массивов, пустого результата и mock-сценариев с распознаванием паттернов.

## Что обновлено в версии 3.6
- Главная страница переработана в dashboard с двумя секциями: `Актуальные сигналы` и `Архив сигналов`.
- В контракт сигналов добавлены типы `SignalStatus`, `SignalStats`, `ChartAnnotation`, `LiquidityZone`, `OrderBlockZone` и группировка `activeSignals/archiveSignals`.
- Добавлен блок статистики сигналов с общим количеством, hit/missed и расчётом `successRate` / `failureRate`.
- Карточки сигналов получили новый premium UI, кнопку `Подробнее`, адаптивную компоновку и расширенный detail-view.
- Для каждого сигнала строится отдельная свечная proxy-визуализация сценария с order block, liquidity zone, support/resistance, entry, stop loss, take profit, FVG и imbalance.

## Что обновлено в версии 3.5
- Добавлен отдельный `data/analytics` слой для сигналов: adapters/connectors, normalization models, feature extraction, fundamental scoring и composite signal scoring.
- Для `tick data`, `bid/ask quotes`, `futures data`, `open interest`, `options chain`, `news feed`, `economic calendar` подготовлены отдельные коннекторы, типы и runtime-статусы источников.
- Добавлены новые API-контракты: `GET /api/analytics/capabilities` и `GET /api/analytics/signals/{symbol}`.
- Реально работает RSS news connector и весь вычислительный pipeline поверх mock/stub источников, без подделки отсутствующих live market feeds.

## Что обновлено в версии 3.4
- Главная страница оставляет только поток сигналов и news banner; страницы `/ideas`, `/news`, `/calendar` и `/heatmap/page` сохранены и продолжают работать через прежние маршруты.
- Карточка сигнала расширена и теперь показывает инструмент, дату/время выхода сигнала, направление BUY/SELL, точку входа, Stop Loss, Take Profit, время сигнала, статус, состояние (`active/open/closed`), уникальное описание, вероятность и прогресс до TP/SL.
- На frontend добавлены анимированная шкала вероятности, крупный график сигнала, уровни, зоны liquidity/order block, projected candles и блок важных новостей по инструменту.
- Добавлены отдельные звуки для нового сигнала и нового news alert с защитой от дублей и повторов при ререндере.
- Backend расширен типизированными моделями сигналов и новостей, ручным ingest слоем, fallback/mock-слоем и MT4 export endpoint без реализации самого советника.
- Live-генерация теперь покрывает все стандартные таймфреймы от `M15` до `W1` и расширенный набор major/cross пар: `EURUSD`, `GBPUSD`, `USDJPY`, `USDCHF`, `AUDUSD`, `USDCAD`, `EURGBP`, `EURJPY`, `GBPJPY`, `EURCHF`.

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
- `GET /api/mt4/signals` — упрощённый MT4-friendly контракт: только tradable BUY/SELL с валидными entry/sl/tp, `trade_permission=true` и `data_status` только `real|delayed`.
- В репозиторий добавлен пример советника `AI_Ideas_Trader.mq4` с консервативными проверками spread/confidence/duplicate order и обработкой только ордеров с заданным `MagicNumber`.
- `POST /api/mt4/export` — подготовка export payload для MT4/bridge слоя

### Analytics API
- `GET /api/analytics/capabilities` — показывает, какие наборы данных уже работают и какие пока заглушки
- `GET /api/analytics/signals/{symbol}` — отдаёт нормализованный analytics bundle, вычисленные признаки, данные по графическим паттернам, `sentiment`, `compositeScoreBreakdown`, `sentimentImpact`, fundamental score и composite signal score

### AI Chat API
- `POST /api/chat` — backend-only forex assistant. Ответ имеет контракт:

```json
{
  "reply": "...",
  "source": "openai",
  "dataStatus": "live|fallback",
  "warnings": []
}
```

Если OpenAI недоступен или выключен, сервер честно возвращает fallback-ответ без выдуманных market data.

## Контракты данных
### Расширенная модель сигнала
Сигнал теперь поддерживает:
- `id/signal_id`
- `instrument/symbol`
- `status` (`active`, `hit`, `missed`, `cancelled`, `expired`)
- `status_label_ru`
- `direction`
- `signalDateTime`
- `side/action`
- `entry`
- `stopLoss/stop_loss`
- `takeProfit/take_profit`
- `takeProfits`
- `signalTime`
- `state`
- `description`
- `probability`
- `progressToTP`
- `progressToSL`
- `chartData`
- `annotations`
- `zones`
- `levels`
- `liquidityAreas`
- `projectedCandles`
- `relatedNews`
- `chartPatterns`
- `patternSummary`
- `patternSignalImpact`
- `sentiment`
- `idea_id`
- `createdAt/created_at_utc`
- `updatedAt/updated_at_utc`

### Persistent Trade Idea contract
Каждая идея теперь имеет устойчивую идентичность и lifecycle:
- `idea_id`
- `symbol`
- `timeframe`
- `setup_type`
- `status` (`created`, `waiting`, `triggered`, `active`, `tp_hit`, `sl_hit`, `archived`)
- `bias`
- `confidence`
- `entry_zone`
- `stop_loss`
- `take_profit`
- `sentiment`
- `rationale`
- `created_at`
- `updated_at`
- `version`
- `change_summary`
- `updates[]` (`timestamp`, `event_type`, `explanation`)
- `current_reasoning`
- `decision` (weighted scoring + факторы confluence)
- `entry_explanation_ru` / `stop_explanation_ru` / `target_explanation_ru`
- `short_scenario_ru` / `short_text` — сверхкраткий сценарий для карточки списка
- `headline` / `summary` / `full_text` / `unified_narrative` / `update_explanation` / `narrative_source` — narrative-слой от LLM (единый связный текст + обратная совместимость API)
- `detail_brief.header` — market price / daily change / bias / confidence / confluence
- `detail_brief.scenarios` — primary / swing / invalidation
- `detail_brief.sections[]` — секции (`smc_ict`, `chart_patterns`, `harmonic`, `waves`, `fundamental`, `wyckoff`, `volume_profile`, `divergences`, `cumdelta`, `sentiment`, `liquidity`) только при реально доступных данных
- `detail_brief.trade_plan` — entry / stop / take profits / R:R / primary / alternative scenario
- Для закрытых идей дополнительно:
  - `result` (`win` / `loss` / `breakeven`)
  - `entry_price`
  - `exit_price`
  - `pnl_percent`
  - `rr`
  - `duration`

Важно:
- идея **обновляется**, а не пересоздаётся, если совпадают `symbol` и `timeframe`, пока lifecycle активен;
- при новом lifecycle создаётся новая идея с новым `idea_id`;
- `symbol` и `timeframe` всегда остаются в ответе API;
- текущий UI не менялся, поэтому карточки продолжают рендериться в прежнем дизайне.

### Модель статистики сигналов
Ответ `GET /api/signals` дополнительно содержит:
- `stats.total`
- `stats.active`
- `stats.hit`
- `stats.missed`
- `stats.cancelled`
- `stats.expired`
- `stats.successRate`
- `stats.failureRate`
- `activeSignals`
- `archiveSignals`

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

## Покрытие live-сигналов
- По умолчанию live-лента строится для таймфреймов `M15`, `M30`, `H1`, `H4`, `D1`, `W1`.
- Для каждого инструмента signal engine подбирает стек подтверждения `HTF → MTF → LTF`, чтобы не ограничиваться только `H1`.
- Базовый список инструментов включает major и cross пары: `EURUSD`, `GBPUSD`, `USDJPY`, `USDCHF`, `AUDUSD`, `USDCAD`, `EURGBP`, `EURJPY`, `GBPJPY`, `EURCHF`.

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
- `/api/news` дополнен расширенными полями: `title_original`, `title_ru`, `preview_ru`, `full_text_ru`, `what_happened_ru`, `why_it_matters_ru`, `market_impact_ru`, `humor_ru`, `summary_source`, `image_url`, `image_source`, `image_alt`, `is_real_source`, `data_origin`, `writer`.
- В ответ API добавлен `diagnostics`: `real_items_count`, `fallback_items_count`, `sources_attempted`, `sources_ok`, `sources_failed`, `grok_used_count`, `generated_images_count`.
- Лента `/news` стала компактной: карточки с изображением и preview на русском раскрываются в detail-view с полным разбором, юмором, блоками «что произошло / почему важно / что может отреагировать».
- Реальные изображения берутся из RSS (`media_content`, `media_thumbnail`, enclosures, `<img>` в summary/content). Если картинка отсутствует, сервер использует безопасный placeholder/generate fallback без передачи ключей на frontend.
- Если доступен `XAI_API_KEY`, Grok используется только как server-side rewrite-слой; при недоступности включается локальный writer с вариативными стилями (не шаблонные повторы).

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
- Свечной график на главной странице — это **proxy visualization** логики сигнала по его уровням, а не исторический live-stream биржевых свечей.

## Новый analytics/data слой
Архитектура разбита на отдельные модули:
- `app/schemas/analytics.py` — внутренние нормализованные модели, feature/result контракты и composite/fundamental score типы.
- `app/services/analytics/providers.py` — provider interfaces, mock providers для market microstructure/derivatives и RSS/stub providers для news/calendar.
- `app/services/analytics/connectors.py` — adapters/connectors по типам данных.
- `app/services/analytics/normalizer.py` — normalization layer в единые внутренние модели.
- `app/services/analytics/features.py` — feature extraction layer.
- `app/services/analytics/fundamental.py` — scoring layer для news relevance / impact / direction / time decay.
- `app/services/analytics/composite.py` — composite score из technical/orderflow/derivatives/fundamental.
- `app/services/analytics/service.py` — orchestration service и API-ready output.

### Какие признаки считаются
- `spread`
- `order book imbalance`
- `delta` / `cumulative delta`
- `futures/spot basis`
- `OI change`
- `put/call OI ratio`
- `put/call volume ratio`
- `IV skew`
- `news impact score`
- `macro event impact score`
- `pattern score` + `patternFeatures`
- `sentiment` как supplementary contrarian layer

### Что уже реально работает
- RSS news feed connector через открытые источники, без синтетических новостей.
- Normalization layer, feature extraction, fundamental scoring и composite signal scoring.
- Integration c текущим `backend.signal_engine` для technical component composite score.
- Sentiment layer как дополнительный фактор веса `~0.10–0.15`, который только усиливает или ослабляет confidence, но не создаёт сигнал сам по себе.
- Mock connectors для orderflow/derivatives с явной маркировкой `mock`, чтобы безопасно разрабатывать контракт и downstream-логику.

### Что пока заглушка
- Экономический календарь пока остаётся `stub` до подключения проверенного live source.
- Tick, quotes, futures, OI и options пока работают как `mock providers`, а не как реальные биржевые feed-коннекторы.
- External sentiment provider работает только как безопасная оболочка вокруг реально заданного URL. Никакой выдуманный OANDA API не используется; при ошибке источник помечается как `unavailable`.

## AI Chat
- Чат отвечает только по forex, сигналам, риску, аналитике и самой платформе.
- Ключ OpenRouter хранится только на backend через environment variables Render / `.env`, без хранения реальных секретов в репозитории.
- System prompt запрещает гарантии прибыли и выдумывание котировок/новостей.
- Если live-данных нет, ассистент обязан прямо сообщать об этом.

### Переменные окружения
```env
OPENROUTER_API_KEY=
OPENROUTER_MODEL=deepseek/deepseek-chat
OPENROUTER_TIMEOUT=30
CHAT_ENABLED=true
SENTIMENT_PROVIDER=mock
OANDA_SENTIMENT_BASE_URL=
OANDA_SENTIMENT_API_KEY=
SENTIMENT_WEIGHT=0.12
TWELVEDATA_API_KEY=
TWELVEDATA_TIMEOUT=4
TWELVEDATA_OUTPUTSIZE=50
```

## Sentiment
- Sentiment — это **дополнительный**, а не основной слой принятия решения.
- Используется contrarian-логика:
  - `long >= 65%` → contrarian bearish
  - `short >= 65%` → contrarian bullish
  - `extreme >= 70%`
- В analytics теперь возвращаются:
  - `sentiment`
  - `compositeScoreBreakdown.sentiment`
  - `sentimentImpact`
- Если внешний источник не настроен или недоступен, система безопасно возвращает `unavailable`/`mock` без выдумывания данных.

## Persistent Trade Ideas
- Trade idea карточки теперь backed by persistent storage.
- Идея не исчезает при каждом обновлении backend: система обновляет уже существующую запись, если это тот же lifecycle.
- При сломе сценария идея помечается как `invalidated`, а при новом lifecycle создаётся новая запись.
- Это поведение реализовано без радикального изменения текущего UI: список на `/ideas` оставлен компактным, а depth анализа перенесён в modal full card.
- Sentiment внутри trade idea используется только как дополнительный контекст и не даёт гарантий результата.

## Unified idea pipeline (critical refactor)
- В `TradeIdeaService` добавлена единая оркестрация для идеи: свечи нормализуются один раз и затем переиспользуются в SMC/overlay/chart/narrative этапах.
- Диагностика расширена полями `candles_count_sent`, `candles_count_used`, `data_provider`, `analysis_mode`, `data_quality`, `fallback_used`, `chart_overlays_present`, `chart_snapshot_status`, `chartImageUrl`.
- Рендер карточек `/ideas` переведён в режим «чистого рендера»: приоритет текста `idea_thesis -> unified_narrative -> full_text -> summary`, а при отсутствии PNG используется fallback-линейный chart по свечам из API.
