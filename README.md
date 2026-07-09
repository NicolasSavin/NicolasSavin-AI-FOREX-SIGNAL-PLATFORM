## Stage 9 — Author Intelligence Engine

- Добавлен пакет `app/services/author_intelligence/` с `AuthorIntelligenceEngine`: он агрегирует Media Catalog, Transcript/Review payload, Knowledge Layer, LLM Review, Investment Committee и Consensus-compatible метрики по каждому YouTube автору.
- Новые endpoint’ы `GET /api/authors` и `GET /api/authors/{author}` возвращают leaderboard и полный author report с rating, tier, proxy accuracy, win rate placeholder, activity, consistency, institutional score, favorite symbols/timeframes и latest opinion.
- Добавлена страница `/authors` с тёмным responsive leaderboard UI и сортировками Highest Accuracy, Highest Rating, Most Active и Best Institutional Score. Accuracy явно помечена как proxy metric до подключения реальных market outcomes.


## FXPilot TV Stage 7: Institutional Investment Committee

- Добавлен финальный слой `app/services/investment_committee/`: `InvestmentCommitteeEngine` собирает video metadata, transcript, Rule AI Review, Knowledge Layer и LLM Review в единый provider-independent отчёт.
- Новый endpoint `GET /api/media/committee/{video_id}` возвращает стабильный JSON-контракт: `video`, `summary`, `overall_score`, `decision`, `signal_quality`, `risk_level`, `agreement_score`, `institutional_bias`, `pros`, `cons`, `conflicts`, `committee_verdict`.
- Добавлена страница `/committee` с русским dark trading UI для карточек Overall Score, Decision, Risk, Agreement, Institutional Bias, Pros, Cons, Conflicts и Committee Verdict.
- Provider architecture изолирует финальный расчёт: текущий локальный rule provider можно заменить на OpenAI, Gemini, Claude, DeepSeek, OpenRouter или Local LLM без изменения frontend-контракта.

# NicolasSavin AI FOREX SIGNAL PLATFORM — Версия 4.0
- Добавлен диагностический endpoint `GET /api/media/rss-test/{source_id}`: он возвращает итоговый RSS URL, HTTP-статус, headers, content-type, размер и первые 500 байт ответа, title/entry count для XML, диагностику пустых feedparser entries, проверку YouTube RSS URL/channel_id для 404 и traceback для неожиданных ошибок.
- Media Import execution hardened: `/admin/media` now displays the raw `POST /api/media/import` JSON/error result, refreshes sources/catalog after success, and `/api/media/debug` reads the last import run log with per-source RSS URL, HTTP status, entries found, imported count and exact error reason.

Платформа на **FastAPI** с модульным backend, тёмным профессиональным frontend и подготовленными API-контрактами для live-сигналов, news alert и будущей интеграции с MT4.



## Что обновлено в версии 4.5
- Добавлен модульный FXPilot AI Review Engine Stage 3: `app/services/llm_review/` содержит контракт `LLMReview`, интерфейс `LLMReviewProvider`, OpenAI-провайдер, prompt builder, engine и JSON-cache в `data/llm_reviews/`.
- Новый endpoint `GET /api/media/llm-review/{video_id}` возвращает `video`, `analysis`, `knowledge` и `llm_review`; существующий `GET /api/media/review/{video_id}` обратносовместимо расширен полем `llm_review`.
- Архитектура построена через dependency injection: API и frontend зависят от `ReviewEngine -> LLMReviewProvider`, поэтому Gemini/Claude/DeepSeek/Local LLM можно добавить заменой провайдера без изменения UI-контракта.
- OpenAI-провайдер использует только env `OPENAI_API_KEY`, `FXPILOT_OPENAI_MODEL` (default `gpt-4.1`) и `FXPILOT_LLM_TIMEOUT`, запрашивает structured JSON и валидирует ответ через `LLMReview`.
- На странице review добавлен блок `AI Expert Verdict` с Summary, Agreement, Recommended Action, Reasoning, Risks, Contradictions, Institutional View, News Impact, Market Bias и Confidence.

## Что обновлено в версии 4.4
- YouTube-импорт FXPilot TV переведён на `yt-dlp`: backend больше не требует Google Cloud и YouTube Data API, не парсит HTML вручную и не скачивает видеофайлы — импортируются только метаданные публичных каналов.
- Добавлен провайдер `youtube_ytdlp`, который принимает `@handle`, `/channel/`, `/user/` и `/c/`, кэширует ответы по каналу на 30 минут, нормализует видео в `MediaItem` и сохраняет каталог с дедупликацией по `youtube_id`.
- `/admin/media` показывает provider `YouTube (yt-dlp)`, статус Online, Last import, Videos imported, Import duration и Errors; кнопка Import Now запускает yt-dlp import и после сохранения обновляет UI.
- `/api/media/debug` возвращает `yt-dlp` version, channel URL, resolved URL, videos found, imported, errors и execution time для диагностики последнего запуска. При ошибке `yt-dlp` существующий каталог сохраняется и импортированные ранее видео не удаляются.

### Установка yt-dlp
```bash
pip install yt-dlp
```
В проекте зависимость добавлена в `pyproject.toml`; на Render она установится вместе с backend dependencies.

### Как работает автоматический YouTube import
1. Источники в `data/media_sources.json` используют provider `youtube_ytdlp`. Legacy/manual источники не удаляются из каталогов и продолжат дедуплицироваться по `youtube_id`.
2. `POST /api/media/import` вызывает `YouTubeYtDlpProvider.fetch_latest()` для каждого enabled источника, получает только metadata через `yt-dlp`, нормализует поля `youtube_id`, `title`, `description`, `thumbnail`, `duration`, `published_at`, `author`, `channel`, `url`, `tags`, `language`, `provider="youtube_ytdlp"`.
3. `MediaImportEngine` объединяет новые записи с `data/media_catalog.json`, дедуплицирует по `youtube_id` и никогда не удаляет уже импортированные видео при ошибке провайдера.
4. Повторные обращения к одному каналу кэшируются на 30 минут, чтобы не делать лишние запросы.

### Поддерживаемые URL каналов
- `https://www.youtube.com/@channel_handle` и коротко `@channel_handle`
- `https://www.youtube.com/channel/UC...`
- `https://www.youtube.com/user/legacyUser`
- `https://www.youtube.com/c/customName`

## Что обновлено в версии 4.3
- Исторический этап YouTube Data API сохранён только как legacy provider `youtube`; актуальный автоматический импорт описан выше в версии 4.4 и работает через `youtube_ytdlp` без Google Cloud.

## Что обновлено в версии 4.2
- YouTube источники FXPilot TV теперь содержат явные `channel_id` и `rss_url`; импорт строит RSS по `channel_id`, а источники без `channel_id` получают статус `needs_channel_id` и понятную диагностику в `/api/media/debug` и `/admin/media`. Добавлен помощник `python scripts/resolve_youtube_channels.py` для ручного поиска отсутствующих ID без YouTube Data API и без HTML scraping в приложении.
- FXPilot Media Import Engine теперь строит YouTube RSS только из поддерживаемых публичных RSS-идентификаторов: `channel_id`, `/channel/UC...` и legacy `/user/...`; для `@handle` и `/c/...` без `channel_id` возвращается явная диагностика без HTML scraping и без YouTube Data API.
- `POST /api/media/import` возвращает детальный контракт `processed/imported/updated/failed/errors`, продолжает импорт остальных источников при ошибке одного источника и сохраняет `channel_id`, `rss_url`, `last_import`, `last_success`, `last_error`, `videos_count` в `data/media_sources.json`.
- Добавлен `GET /api/media/debug` для проверки provider, RSS URL, channel id, HTTP-статуса запроса, количества найденных видео и последней ошибки источника.

## Что обновлено в версии 4.1
- FXPilot TV Sprint 3 Automatic Media Import Engine: добавлены `data/media_sources.json`, `data/media_catalog.json`, нормализованная модель `MediaItem` и модульный `MediaImportEngine` для provider-independent импорта YouTube RSS / будущих Telegram, RSS, Podcast, Vimeo, FXPilot, News и Articles без изменения API.
- Добавлены API `GET /api/media`, `GET /api/media/sources`, `POST /api/media/import` и scheduler abstraction `GET /api/media/scheduler`; импорт обрабатывает ошибки по источникам изолированно, дедуплицирует каталог по provider + external id и сохраняет newest-first порядок.
- Страница `/tv` теперь потребляет `/api/media`, а скрытая админ-страница `/admin/media` показывает источники, статус импорта, newest media и кнопки Import Now / Enable / Disable без UI-редизайна и без AI Summary, transcript analysis, Reality Check или Trust Score.

## Что обновлено в версии 4.0
- FXPilot TV Sprint 3: добавлен внутренний Source Manager на `/tv/sources` и API `GET /api/tv/sources`; источники берутся из `data/tv_sources.json`, валидируются сервисом `TvSourceManager`, а кнопки Import now / Disable / Enable пока показывают статус разработки без scraping, YouTube API и реального импорта.
- FXPilot TV на `/tv` доведён до production-quality video portal: responsive YouTube player со skeleton-загрузкой и autoplay выбранного ролика, duration/publish/category badges, подсветка текущего видео, поиск, фильтр категорий, сортировка newest-first, smooth-scroll sidebar и группировка Today / Yesterday / Archive.
- Review-навигация закреплена как стабильный UX-контракт: «Проверить обзор» открывает `/tv/review/<video_id>`, а reusable frontend-компоненты `tv-components.js` позволяют будущему `/api/tv/review/<video_id>` наполнять страницу без переработки UI.

## Что обновлено в версии 3.9
- FXPilot TV на `/tv` превращён в рабочий видео-раздел: добавлен локальный JSON-каталог `data/tv_videos.json`, endpoint `GET /api/tv/videos`, YouTube iframe-плеер, sidebar со списком обзоров и preview будущей AI-проверки без YouTube API, scraping и внешних ключей.
- Добавлена первая рабочая Review page для FXPilot TV: кнопка «Проверить обзор» ведёт на `/tv/review/<video_id>`, страница получает подготовленный JSON с `/api/tv/review/<video_id>` и показывает плеер, метаданные, placeholder-секции AI Summary, FXPilot Opinion, Agreement Score, Main Conclusions, Reality Check и Trust Score без подключения AI-логики.
- Добавлен optional provider FXPilot OrderFlow Engine для `/api/ideas/market`: при `ORDERFLOW_ENABLED=true` backend запрашивает `ORDERFLOW_URL/api/orderflow/latest?symbol=...` с timeout 2 секунды и отдаёт отдельные поля отображения Order Flow без изменения scoring, BUY/SELL-логики, MT4 bridge и советника.
- Интегрирован HFT Stop Hunt layer в `/api/ideas` и `/ideas/market`: поля MT4 Bridge v4 `hft_object_available`, `hft_point_type`, `hft_point_side`, `hft_point_price` теперь формируют `hft_layer`, расстояние до точки, bias, strength и ограниченную корректировку Score ±8 без создания самостоятельного сигнала.
- HFT-диагностика добавлена в `learning_snapshot`, `advisor_filter_debug` и карточку `/ideas`; старые слои MZ, Heatmap, DPOC, CumDelta, Future Volume/Delta, Options, News/Fundamental и Lifecycle сохранены.
- Добавлен prop-desk execution layer для `/api/ideas/market` и `/api/ideas` без удаления legacy MT4-полей: killzone, ATR(14), RVOL, VWAP, news lock, correlation risk, cooldown after losses, dynamic risk и market regime.
- Новые поля включают `base_score`, `execution_score`, `final_score`, `killzone_status`, `atr_pips`, `rvol`, `vwap_alignment`, `news_lock_active`, `correlation_block`, `cooldown_active`, `risk_per_trade_pct`, `recommended_lot`, `market_regime`.
- Старые поля `entry`, `sl`, `tp`, `signal`, `action`, `trade_permission`, `advisor_allowed`, `score`, `grade`, `mode` сохранены; при блокировках исполнения выставляется `mode=NO TRADE`.
- На странице `/ideas` добавлен блок `Execution Analysis` с русскими пояснениями по Killzone, ATR, RVOL, VWAP, News Lock, Correlation, Regime и Dynamic Risk.

## Что обновлено в версии 3.8
- Добавлен отдельный Confluence Engine (`services/confluence/confluenceEngine.ts`): объединяет SMC + Liquidity + Options + Volume в единый institutional score, даёт fallback при отсутствии слоя, учитывает conflict/pinning warnings и возвращает финальный `signal/confidence/summary` breakdown.
- Добавлен единый `CanonicalMarketService` и интерфейс `RealMarketDataProvider` для всех рыночных endpoint: `GET /price/{symbol}`, `GET /market`, `GET /chart/{symbol}/{tf}`, а также `GET /api/price/{symbol}`, `GET /api/market`, `GET /api/canonical/chart/{symbol}/{tf}`.
- Добавлен MT4 Candle Bridge: `POST /api/mt4/push-candles` принимает реальные broker OHLC, хранит ограниченный in-memory буфер (до 600 баров на symbol/timeframe) и делает MT4 первичным источником в `fetch_candles()` с безопасным fallback на cache/TwelveData/Dukascopy при stale/empty bridge.
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


### Volume Delta priority chain
MT4/FutureVolume слой теперь передаёт стабильный объект `volume_delta` в payload и prop score:
- `source` — `FutureDelta`, `FutureVolume`, `tick_volume` или `unavailable`;
- `delta` — дельта текущей свечи/буфера;
- `cumdelta` — накопленная дельта;
- `is_proxy` — `false` только для primary-данных FutureDelta, `true` для расчётов от FutureVolume/tick volume;
- `priority_used` — 1 для FutureDelta, 2 для расчёта от FutureVolume, 3 для расчёта от MT4 tick volume.

Приоритет расчёта: сначала реальные буферы FutureDelta (`delta`/`cumdelta`), затем proxy-дельта `future_volume * body_ratio`, затем proxy-дельта `tick_volume * body_ratio`, где `body_ratio = (close - open) / max(high - low, tiny)`. Prop engine подтверждает BUY только при росте цены и CumDelta, SELL — при падении цены и CumDelta; при расхождении выставляется `delta_divergence=true` и score уменьшается.

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

## Проверка ingest опционных уровней для /ideas

Endpoint `POST /api/options/levels` позволяет загрузить реальные/ручные/MT4 уровни опционного слоя в Ideas без synthetic-данных. Прямой scraping CME остаётся отдельной задачей, но ingest-слой уже позволяет питать `/ideas/market` и `/api/ideas` актуальными уровнями из `manual | mt4_optionsfx | cme_public`.

Проверка вручную:

```bash
curl -X POST http://127.0.0.1:8000/api/options/levels \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "EURUSD",
    "underlying_price": 1.085,
    "timestamp": "2026-05-03T12:00:00Z",
    "source": "mt4_optionsfx",
    "levels": [
      {"type": "max_pain", "price": 1.08},
      {"type": "put", "price": 1.075},
      {"type": "call", "price": 1.095}
    ],
    "metadata": {}
  }'

curl http://127.0.0.1:8000/api/options/levels/EURUSD
curl http://127.0.0.1:8000/ideas/market
```

## External Telegram options confirmation
- `CME_OptionsFX` подключён как дополнительный `options_flow_source`, а не как источник прямого открытия сделок и не как замена существующего CME/options модуля.
- Endpoint `GET /api/external-signals/cme-optionsfx` возвращает последние распарсенные Telegram-сообщения по `EURUSD`, `GBPUSD`, `USDJPY`, `XAUUSD`, `DXY` с полями `option_bias`, `key_strikes`, `max_pain`, `expiry`, `gamma_zone`, `put_call_bias`, `raw_text`, `source`.
- Если Telegram credentials не заданы (`TELEGRAM_API_ID`/`TELEGRAM_API_HASH` или `TELEGRAM_BOT_TOKEN`, также поддерживаются `TG_*` aliases), endpoint возвращает `available=false`, а scoring работает как раньше без блокировки сделок.
- В Prop Signal Engine слой `CME_OptionsFX` используется только как confirmation layer: совпадение options bias с направлением сделки даёт `+4` к score, явный конфликт даёт `-4`, а high-confidence conflict может стать blocker.
- В payload идеи добавлены `external_options_ru`, `external_options_bias`, `external_options_key_strikes`, `external_options_max_pain`, а `advisor_signal` содержит `external_options_used`, `external_options_alignment`, `external_options_source="CME_OptionsFX"`.

## Статистика и архив сигналов

- Страница `/stats` показывает общие показатели жизненного цикла идей, WinRate, средний плановый RR и результаты TP/SL за текущий UTC-день из `/api/stats`.
- Страница `/archive` показывает журнал идей из `/api/archive`, поддерживает фильтры по основным инструментам и открывает подробности архивной идеи в модальном окне.
- Существующие поля и маршруты API сохранены; в `/api/stats` добавлены совместимые поля `total_ideas`, `average_rr`, `today_tp` и `today_sl`.

### DPOC из Future_Volume_v5.00

Существующий MT4 bridge принимает текущий дневной DPOC без отдельного маршрута: передайте `dpoc_price` (также поддерживаются aliases `dpoc`, `daily_dpoc`, `daily_dpoc_price`) в `GET /api/mt4/ingest-get`, `POST /api/mt4/volume-clusters` или legacy `POST /ideas/market` вместе с FutureVolume payload.

Backend сохраняет реальный уровень индикатора как `dpoc_price`, рассчитывает подписанное поле `distance_to_dpoc_pips` от текущей цены и публикует оба поля на верхнем уровне идеи и внутри `market_structure`. DPOC используется только как подтверждение: BUY получает +3 score при цене выше DPOC, SELL — +3 при цене ниже DPOC. Отсутствующий или неподтверждающий DPOC не блокирует сделку.

## Диагностика таймаутов `/ideas/market`

Маршрут `/ideas/market` использует stale-while-revalidate кэш (TTL задаётся через `MARKET_IDEAS_CACHE_TTL_SECONDS`, по умолчанию 60 секунд). Построение рынка запускается в фоне при старте приложения и при устаревании кэша, поэтому внешний HTTP/LLM provider больше не блокирует основной запрос Render.

Для потенциально медленных этапов пишутся парные структурированные записи `START` / `END` с `elapsed_ms` и флагом `slow_over_5s`. В Render logs можно фильтровать `timing operation=` и отдельно проверять `build_market`, `generate_trade_ideas`, `enrich_ideas_with_prop_scores`, `enrich_idea_with_openai_narrative`, `options_analysis`, `CME_OptionsFX` и `chart_endpoint`.

## Диагностика LLM OpenRouter / Grok

- Backend публикует runtime-статус модели через `GET /api/ai/status`: включение OpenRouter, модель, наличие `OPENROUTER_API_KEY`, последние request/success/error timestamps, счётчики запросов и `llm_available`.
- Debug endpoint `GET /api/ai/test` отправляет короткий запрос `Reply with OK` в текущую модель OpenRouter и возвращает `success`, `provider`, `model`, `response`, `latency_ms` и, при ошибке, текст причины.
- При старте FastAPI выполняется неблокирующий health-check OpenRouter. Если ключ отсутствует или провайдер недоступен, приложение не падает: причина сохраняется в runtime status, а существующая fallback-логика продолжает работать.
- Каждый интегрированный LLM-вызов логирует модель, время запроса, latency, успех или ошибку. UI на главной странице и `/ideas` показывает блок `AI Status`, а карточки идей отображают источник: `Grok`, `Fallback Engine` или `Rule Engine`.

## FXPilot 2.0

FXPilot 2.0 starts with the **FXPilot Intelligence** philosophy: not just signals, but market understanding backed by facts. The homepage now states this direction, and the public navigation includes the new **FXPilot TV** foundation at `/tv`.

FXPilot TV is currently a premium UX/UI foundation only. It intentionally does not implement YouTube ingestion, backend video processing, or AI video analysis yet. The page describes the future roadmap for video cataloging, AI summaries, FXPilot comparison, Reality Check, Trust Score, Market Memory, and AI Mentor.

## Media Import: YouTube Source Resolver

FXPilot Media Admin can add YouTube sources from a channel URL only. The backend resolves the real `channel_id` from `/channel/UC...`, `@handle`, `/c/...`, or `/user/...` public channel URLs without the YouTube Data API, validates the generated RSS feed, and stores `channel_id`, `rss_url`, feed title, entry count, and resolver diagnostics in `data/media_sources.json`.

Admin endpoints:

- `POST /api/media/resolve-source` — resolve and validate one YouTube channel URL.
- `POST /api/media/sources` — resolve, validate, duplicate-check, and save a source.
- `POST /api/media/resolve-all` — re-resolve enabled YouTube sources and refresh stored RSS metadata.


## FXPilot TV media catalog balancing

- Automatic media import now keeps source diversity by limiting each enabled source to `FXPILOT_MEDIA_MAX_PER_SOURCE` newest valid videos; default is `5`.
- Rebuilt `data/media_catalog.json` is sorted newest-first by `published_at`, falling back to `imported_at`, and `/api/media/stats` exposes `sources_with_videos` plus `videos_by_source` for catalog health checks.
- yt-dlp metadata dates are normalized from `upload_date` (`YYYY-MM-DD`) or `timestamp` (UTC ISO datetime); when no published date is available, `imported_at` is used.
- The `/tv` catalog list renders all videos returned by `/api/media` so visible videos are not restricted to one source group.

## FXPilot TV yt-dlp catalog migration

- `/api/media` and `/tv` now read the video catalog from `data/media_catalog.json` only; `data/manual_youtube_videos.json` is excluded by default and can be enabled only for local development with `FXPILOT_DEV_MANUAL_MEDIA=1`.
- `POST /api/media/import` rebuilds and saves `media_catalog.json` immediately after provider import, filters out records without a valid 11-character `youtube_id`, deduplicates by `youtube_id`, and records `duplicates_removed` in the import debug log.
- Added `GET /api/media/stats` for catalog health (`catalog_items`, `real_videos`, `manual_demo`, `duplicates_removed`, `last_import`); the TV player shows `No videos imported` when the automatic catalog is empty and always uses `https://www.youtube.com/embed/{youtube_id}` for playback.

## FXPilot AI Transcript Engine v1

- `GET /api/media/transcript/{video_id}` получает YouTube transcript без OpenAI/GPT, нормализует сегменты (`text`, `start`, `duration`, optional `speaker`) и возвращает контракт `status`, `provider`, `language`, `duration`, `segments`, `text`.
- Pipeline построен вокруг `TranscriptEngine` и provider-интерфейса: текущий provider использует `youtube-transcript-api` для `ru`, `en`, `auto` и переводимого transcript, а `WhisperTranscriptProvider` пока является placeholder и возвращает `WHISPER_REQUIRED`.
- Локальный кеш хранится в `data/transcripts/{video_id}.json` с `video_id`, `created_at`, `provider`, `language`, `segments`, `full_text`, `duration`; повторный запрос читает файл и не обращается к YouTube повторно.
- `/api/media/debug` дополнительно показывает `transcripts_cached`, `transcript_requests`, `transcript_errors`, `provider_used`.
- Страница `/tv/review/{video_id}` показывает секцию Transcript: первые абзацы при `FOUND`, `Transcript unavailable` при недоступности и `Whisper processing required`, если нужен будущий Whisper-процессинг.


## FXPilot TV Stage 8 — AI Consensus Engine

Stage 8 adds `/api/consensus/{symbol}` and `/api/consensus/{symbol}/{timeframe}` plus the `/consensus` page. The consensus engine aggregates all imported Media Catalog videos for the requested market, reusing Transcript Engine, Rule Analyzer, Knowledge Layer, LLM Review, and Investment Committee contracts through existing service builders. It returns bullish/bearish/neutral distribution, average confidence, average committee score, author leaderboard, detected conflicts, and a provider-independent consensus report. Historical author accuracy is intentionally exposed as a placeholder until verified performance data is available.
