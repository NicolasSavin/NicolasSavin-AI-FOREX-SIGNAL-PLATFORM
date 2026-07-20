## Stage 25 — Market State Engine (Production Grade)

- Добавлена подсистема `app/services/market_state/`, которая без LLM агрегирует Consensus, Structured Reviews, Signal Validation, Author Intelligence, Knowledge Graph, Performance и Historical Metrics в объективное состояние рынка по каждому символу.
- Новая модель `MarketState` вычисляет `direction`, `trend_strength`, `confidence`, `agreement`, `validation_score`, `author_score`, `performance_score`, `market_quality`, `review_count`, `author_count` и `updated_at`; недоступные реальные данные не подменяются фейковыми значениями.
- Состояние сохраняется atomic write в `DATA_DIR/market_state.json`, а runtime использует TTL-cache и debug-метрики cache hit/cache age.
- Новые API: `GET /api/market-state` для всех символов, `GET /api/market-state/{symbol}` для одного символа, `GET /api/market-state/debug` для диагностики вычислений.
- OPS-страница `/ops/market` показывает тёмную responsive таблицу Symbol, Direction, Trend, Confidence, Agreement, Validation, Authors, Performance, Quality и Updated с поиском, фильтром, сортировкой и автообновлением.
- После OPS Consensus rebuild и завершения Performance outcome Market State пересчитывается автоматически, сохраняя существующие маршруты и контракты.

Пример ответа `GET /api/market-state/{symbol}`:

```json
{
  "symbol": "EURUSD",
  "direction": "Bullish",
  "trend_strength": "Strong",
  "confidence": 82,
  "agreement": 91,
  "validation_score": 80,
  "author_score": 76,
  "performance_score": 78,
  "market_quality": "Excellent",
  "review_count": 9,
  "author_count": 4,
  "updated_at": "2026-07-20T12:00:00+00:00"
}
```

## Stage 23 — Author Intelligence & Trust Engine

- Author Intelligence повышен до первого класса: `AuthorProfile` хранит канонический `id`, aliases, review/source/trade/symbol counts, first/last seen, confidence/agreement/consensus alignment, trust/accuracy/activity/quality/signal scores, language/categories/status и proxy performance поля.
- `AuthorIntelligenceEngine` строит профили из Media Catalog, AI Review, Investment Committee и Consensus, объединяет очевидные aliases через конфигурируемые правила, рассчитывает weighted trust 0–100 и сохраняет incremental cache в `data/author_profiles.json`.
- Добавлены endpoint’ы `GET /api/authors/top`, `GET /api/authors/stats`, `GET /api/authors/debug`; существующие `GET /api/authors` и `GET /api/authors/{author}` сохранены и обратносовместимо расширены новыми полями.
- Consensus теперь учитывает author weight: high-trust авторы получают больший вес, experimental — меньший; одновременно сохранены обычные count-поля для совместимости.
- Страница `/authors` и ops alias `/ops/authors` показывают таблицу авторов с Trust, Accuracy, Reviews, Trade Ideas, Symbols, Activity, Quality, Last Review, Status, поиском и детальным профилем. Accuracy/performance явно отмечены как proxy до подключения реальных market outcomes.


## Stage 16 — Structured AI Review entity extraction

- AI Review now uses a strict structured JSON contract for trading entities: `symbols`, `primary_symbol`, `timeframe`, `direction`, confidence, entry/entry zone, stop loss, take profits/targets, detected levels and per-symbol `trade_ideas`. Missing market facts are stored as `null` or empty arrays instead of fake prices.
- The Grok/OpenRouter prompt explicitly requires valid JSON only, forbids invented instruments/prices, distinguishes broad market commentary from actionable trade ideas, and includes common aliases such as золото/gold → `XAUUSD`, euro dollar → `EURUSD`, Bitcoin → `BTCUSD`, Brent → `UKOIL`.
- A deterministic fallback extractor validates and supplements LLM symbols from video title, description, tags, existing media symbol, transcript and LLM summaries so real instruments no longer collapse to the `MARKET` compatibility fallback.
- `GET /api/media/review/{video_id}` and `GET /api/tv/review/{video_id}` now return structured top-level fields while keeping backward-compatible `symbol` and `llm_review` fields.
- Added manual reprocessing endpoint: `POST /api/media/reviews/reprocess?force=false&limit=<number>` regenerates reviews with `MARKET`/missing symbols or empty trade ideas; the same JSON contract is available in a browser via `GET /api/media/reviews/reprocess?force=false&limit=<number>`, and `force=true` rebuilds already structured reviews.
- `/api/media/debug` now reports review entity coverage: total reviews, reviews with primary symbol, trade ideas, MARKET fallback, direction, levels and last extraction error.

## Stage 12 — YouTube Data API Primary Provider

- FXPilot TV теперь использует официальный YouTube Data API v3 как первичный YouTube-провайдер при наличии `YOUTUBE_API_KEY`; `yt-dlp` остаётся автоматическим fallback только для источников, где API-импорт завершился ошибкой или ключ отсутствует.
- Новый модуль `app/services/providers/youtube_api_provider.py` поддерживает `channelId`, `@handle`, legacy `username`, playlist URL и uploads playlist из `provider_config`, резолвит `https://youtube.com/@xxx` в `channel_id` и кэширует результат.
- Импорт строится на `channels.list`, `search.list`, `videos.list` и `playlistItems.list`, возвращает тот же `MediaItem`-контракт для `/api/media`, а существующий каталог не очищается при ошибке провайдера.
- `/api/media/debug` расширен полями `provider_selected`, `provider_used`, `provider_fallback`, `youtube_api_enabled`, `youtube_api_quota_used`, `youtube_api_remaining_estimate`, `resolved_channel_id`, `api_errors`, `fallback_reason`; `/api/media/stats` считает videos per provider, quota usage, API latency и fallback count.
- Админ-панель `/admin/media` показывает диагностику API / yt-dlp / RSS / Telegram и фактический provider used без изменения публичных API routes.

```mermaid
flowchart TD
  A[Admin Import Now / Scheduler] --> B[MediaImportEngine]
  B --> C{YouTube source?}
  C -->|YOUTUBE_API_KEY exists| D[YouTubeApiProvider]
  C -->|No key| E[YouTubeYtDlpProvider]
  D --> F[channels.list / search.list / videos.list / playlistItems.list]
  F --> G{API success?}
  G -->|yes| H[Normalize to MediaItem]
  G -->|no| E
  E --> I[yt-dlp metadata fallback]
  I --> H
  B --> J[RSS Provider]
  B --> K[Telegram Provider]
  H --> L[Deduplicate + merge catalog]
  J --> L
  K --> L
  L --> M[/api/media compatible catalog]
  B --> N[/api/media/debug + /api/media/stats diagnostics]
```

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

## FXPilot TV Stage 10 — Performance Engine / Truth Engine

Stage 10 adds a provider-neutral post-analysis engine at `app/services/performance/` for verified YouTube idea outcomes. It reuses Media Catalog, Transcript/Rule/Knowledge/LLM Review, Investment Committee, Consensus and Author Intelligence builders instead of duplicating services.

New endpoints:

- `GET /api/performance` — evaluates imported videos and returns outcomes plus leaderboards.
- `GET /api/performance/{video_id}` — returns one video outcome.
- `GET /api/performance/author/{author}` — returns author-level performance summary and evaluated videos.

The engine stores and returns explicit outcome fields: `entry_price`, `stop_loss`, `take_profit`, `entry_time`, `evaluation_start`, `evaluation_end`, `market_high`, `market_low`, `max_profit`, `max_drawdown`, `profit`, `loss`, `rr`, `mfe`, `mae`, `holding_time_hours`, `result`, `status`, provider metadata and Russian warnings. Supported results are `WIN`, `LOSS`, `PARTIAL`, `BREAKEVEN`, `EXPIRED`, and `UNKNOWN`.

Market replay is intentionally provider-neutral: the default adapter uses the existing canonical market service / MT4 bridge path, while the interface can be backed by MT5, Databento, Polygon, TwelveData or future providers. If candles or explicit Entry/SL/TP levels are missing, the engine returns `UNKNOWN` and does not replace real outcomes with proxy metrics.

Frontend page `/performance` shows leaderboard blocks for Best Authors, Worst Authors, Most Accurate and Most Profitable plus per-video Prediction, Reality, Difference, Profit/Loss and Result cards. Completed outcomes call a hook point for refreshing Author Intelligence, Consensus and Institutional Rating without changing existing public APIs.


## FXPilot TV Source Manager v1

FXPilot TV imports content through a unified source registry (`data/media_sources.json`) with stable fields for `source_type`, `provider`, diagnostics, counters, tags, symbols and `provider_config`. Existing catalog items are merged and deduplicated by YouTube ID or URL; a failed source does **not** clear the media catalog. Runtime uses `DATA_DIR/media_sources.json` as the canonical source registry for MediaImportEngine, OPS imports, scheduler diagnostics and the FXPilot TV manager; `tv_sources.json` is legacy compatibility/template input only and is converted deterministically on first run when the canonical file is missing or empty. Valid persistent administrator settings in `media_sources.json` are never overwritten by repository templates.

### YouTube Data API setup

Preferred production mode uses YouTube Data API v3 when a key is available:

```bash
YOUTUBE_API_KEY=your_google_api_key
FXPILOT_YOUTUBE_PROVIDER=auto
```

Provider mode values:

- `auto` — try `youtube_api` when `YOUTUBE_API_KEY` exists, then fallback to `youtube_ytdlp` if the API fails.
- `api` — use YouTube Data API provider.
- `ytdlp` — force yt-dlp fallback provider.

Admins can add a YouTube source by URL in `/admin/media`; channel IDs are resolved automatically where possible and saved in `provider_config.channel_id`.

### Telegram public source setup

Telegram v1 supports public preview pages without a bot token. Add a source with provider `telegram_public` and a URL such as:

- `https://t.me/channelname`
- `https://t.me/s/channelname`

The importer normalizes public post metadata into Media Catalog items. `telegram_bot_provider.py` is present as a future placeholder and activates only when `TELEGRAM_BOT_TOKEN` is configured.

### RSS setup

Add an RSS source with provider `rss_feed` and the feed URL. The provider reads title, link, summary, published date, author and thumbnail/enclosure metadata via `feedparser`.

### Render environment variables

Configure these in Render when enabling production ingestion:

```bash
YOUTUBE_API_KEY=...
FXPILOT_YOUTUBE_PROVIDER=auto
TELEGRAM_BOT_TOKEN=... # optional future bot provider
```

## FXPilot TV Autonomous Platform (Stage 12)

FXPilot TV now runs as an autonomous FastAPI-managed media platform. After an administrator adds a YouTube, Telegram, or RSS source in `/admin/media`, the application starts APScheduler with the API process and runs provider-specific jobs without requiring Render Cron:

- YouTube sources: every 15 minutes.
- Telegram public sources: every 10 minutes.
- RSS sources: every 30 minutes.
- Nightly maintenance: rebuild author, consensus, and performance layers, publish the TV catalog, and clean temporary caches.

The automatic pipeline is Import → Transcript → Rule AI → Knowledge Layer → LLM Review → Committee → Consensus → Author Intelligence → Performance → Publish to TV. Manual import endpoints remain for backward compatibility, but normal daily operation is source-once/autopilot afterwards.

The admin interface shows Sources, Statistics, Scheduler, Import Queue/last run, Logs, Health, and Notifications in one dark Russian-language interface. Source health uses explicit states (`Healthy`, `Warning`, `Broken`, `Disabled`) and tracks last successful import, last failed import, last error, imported item counts, average import duration, provider usage, and AI backlog metrics.

## Operations panel

Production operations panel:

https://fxpilot.ru/ops

Required Render environment variable:

```text
FXPILOT_OPS_TOKEN=<a long random secret>
```

Render setup steps:

1. Open Render service `AI-FOREX-SIGNAL-PLATFORM`.
2. Open **Environment**.
3. Add `FXPILOT_OPS_TOKEN` with a long random secret value.
4. Save changes and deploy.
5. Open `/ops` in the browser.
6. Enter the same token in the panel.

Security notes:

- The token is never included in source code or documentation.
- The browser stores the token only in `sessionStorage` for the current tab/session.
- Mutating operations use the `X-FXPILOT-OPS-TOKEN` header and are rejected without a valid token.
- `/api/ops/status` returns only a safe consolidated status payload and does not expose API keys, authorization headers, environment values, prompts or full exception traces.
- `/api/ops/audit` is token-protected and stores only safe operation metadata in `data/ops_audit.json` with the latest 200 records.

### Stage 20 Knowledge Graph data loading

Knowledge Graph reads the same canonical media catalog as `/api/media`, `/api/media/catalog`, and OPS media counts through `load_canonical_media_catalog()` / `create_media_import_engine().load_catalog()`. It also enumerates stored AI Review JSON files from the configured `LLMReviewStorage` directory so structured reviews can still be indexed when catalog metadata is temporarily unavailable. Diagnostics distinguish catalog items, review files scanned, loaded reviews, indexed structured reviews, orphan indexed reviews, malformed review files, and cache timing without exposing filesystem paths or raw LLM data.

## Stage 20.2 — production storage durability on Render

Stage 20.2 centralizes all mutable application data under one configurable root so media catalogs, TV catalogs, AI Review JSON, transcripts, automation state and OPS audit records are not split between `<repo>/data` and process working-directory relative `data` folders.

### Root cause fixed

The production failure mode was consistent with generated files being written inside the deployed Render container/repository filesystem or an inconsistent relative `data` directory. Those files can appear immediately after import/review generation and then disappear after a Render restart or redeploy because the container filesystem is ephemeral unless a Render persistent disk is mounted. The Knowledge Graph also cached an empty graph, which made missing storage look stable until cache expiry or manual invalidation.

### Canonical storage paths

Configure one data root with:

```bash
FXPILOT_DATA_DIR=/var/data/fxpilot
FXPILOT_STORAGE_MODE=persistent
```

If `FXPILOT_DATA_DIR` is absent, local development uses the repository `data/` directory. Production should not rely on that fallback. Canonical files/directories now resolve from the shared storage module:

- `media_sources.json`
- `media_catalog.json`
- `tv_sources.json`
- `tv_videos.json`
- `manual_youtube_videos.json` (local/manual mode only)
- `media_import_debug.json`
- `media_automation_state.json`
- `ops_audit.json`
- `llm_reviews/*.json`
- `transcripts/*`

### Render persistent disk setup

1. Open Render Dashboard.
2. Select web service `AI-FOREX-SIGNAL-PLATFORM`.
3. Open **Disk**.
4. Add a persistent disk.
5. Suggested disk name: `fxpilot-data`.
6. Suggested mount path: `/var/data/fxpilot`.
7. Add environment variable: `FXPILOT_DATA_DIR=/var/data/fxpilot`.
8. Add environment variable: `FXPILOT_STORAGE_MODE=persistent`.
9. Save and redeploy.
10. Open `/ops` and inspect **Storage diagnostics**.

Files written inside the deployed repository/container are ephemeral on Render. Redeploying can remove locally generated media catalogs, AI Reviews and transcripts. A mounted persistent disk is required for durable production file storage; do not claim persistence without the mounted disk and environment variables above.

### OPS storage diagnostics and migration

Protected endpoints require the existing `FXPILOT_OPS_TOKEN` header and never expose API keys, tokens, file contents or full absolute host paths.

- `GET /api/ops/storage` returns storage mode, data-root source, process instance id, process start time, media catalog counts, TV catalog counts, AI Review file diagnostics, transcript counts and health warnings.
- `POST /api/ops/storage/migrate?dry_run=true&execute=false` performs a free dry-run migration report.
- `POST /api/ops/storage/migrate?dry_run=false&execute=true` copies only known application files from legacy data locations into the configured data root.

Migration is conservative: it never calls an LLM, never regenerates reviews, never deletes source files, never copies unknown files, validates `llm_reviews/*.json`, and never overwrites newer destination files with older source files.

### Storage health warnings

When Render-like production is detected without a persistent configured data root, OPS storage health returns `ephemeral_storage_risk` with a clear warning that imported media and generated reviews may disappear after redeploy. If `FXPILOT_STORAGE_MODE=persistent` is set but the configured directory is missing or not writable, health is reported as degraded.

### Knowledge Graph cache behavior

Knowledge Graph keeps the normal healthy TTL, but empty graphs with zero catalog items and zero review files use a short TTL so startup races or late-mounted storage do not preserve an empty graph for the full cache duration. OPS media import, review generation/reprocessing, migration, aggregation rebuilds and safe cache clear invalidate the graph cache.

## Stage 20.4 Media Import persistence diagnostics

- Media Import now records canonical catalog path, storage root, catalog existence, before/after item counts, loaded source items, written items, write success/error state and per-filter removal counters in `/api/media/debug`.
- The import pipeline refuses a successful zero-item catalog write when providers returned importable videos, so fetched YouTube media is persisted to the canonical `media_catalog.json` or the write error is surfaced in diagnostics.
- Added a regression test that imports one mock YouTube video, verifies catalog file persistence and subsequent catalog loading, then confirms the Knowledge Graph indexes the imported item with a stored review.

## Stage 22 — FXPilot Source Manager

- Browser Source Manager is available at `/ops/sources` and `/tv/sources` for runtime YouTube, Telegram and RSS source administration.
- Source CRUD APIs are available under `/api/sources`: list, read one, create, update, delete, test, import one source, bulk actions/import, export and debug.
- Source changes are written only to the canonical `DATA_DIR` runtime registry (`media_sources.json`) and are picked up by Scheduler / Media Import on the next run without redeploy or service restart.
- Supported provider keys include `youtube_channel`, `youtube_playlist`, `telegram_public`, `telegram_rss` and `rss_feed`; the engine still maps them to the existing provider architecture for backward compatibility.
- Source changes append audit records to the existing OPS audit log while preserving imported media and review history.

## Stage 24 — Signal Validation & Performance Engine

FXPilot includes a production-oriented Signal Validation Engine that assigns every extracted trading idea an objective outcome when real historical OHLC candles are available. The engine does not substitute proxy candles or fake market results: unavailable history keeps a validation pending/expired with an explicit Russian warning.

### Validation scope

- Assets: Forex, metals (gold/silver), indices and crypto through the abstract historical market data provider.
- Inputs: `symbol`, `direction`, `entry`, `entry_zone`, `stop_loss`, `take_profit`, `targets[]`, `timeframe`, `published_at`.
- Outputs: status, outcome, RR, profit/loss points, holding time, entry/exit time, MFE and MAE.
- Rules: BUY entries trigger when price trades at or below entry; SELL entries trigger when price trades at or above entry; validation stops on TP, SL or expiration.

### API and OPS

- `GET /api/validation` validates current catalog ideas incrementally and returns persisted results.
- `GET /api/validation/{id}` returns one validation by validation id or signal id.
- `GET /api/validation/stats` returns pending/running/validated/failed/expired totals.
- `GET /api/validation/authors` returns win rate, loss rate, average RR, holding time, drawdown and streak metrics.
- `GET /api/validation/symbols` returns symbol accuracy, frequency, average RR and best/worst authors.
- `GET /api/validation/debug` exposes storage/provider diagnostics and labels real historical OHLC usage.
- `/ops/validation` provides a dark Russian Operations screen for the latest validations.

Consensus now uses validated historical author performance as its author weighting source, so authors with better objective outcomes can influence consensus more than unvalidated reputation-only signals.


## Stage 26 — Multi-Timeframe Intelligence Engine

FXPilot includes a deterministic Multi-Timeframe Intelligence subsystem that aggregates all supported timeframes (`M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`, `W1`, `MN`) into a single `MarketTimeframeProfile` per symbol. The engine does not call any LLM and computes its output from existing Market State, Consensus, Signal Validation, Structured Reviews, Knowledge Graph, Performance and Author Intelligence data.

Default timeframe weights are higher for higher timeframes: `M1=1`, `M5=2`, `M15=3`, `M30=4`, `H1=5`, `H4=8`, `D1=13`, `W1=21`, `MN=34`. Results are persisted atomically to `DATA_DIR/multi_timeframe.json`.

API and OPS routes:

- `GET /api/multi-timeframe` — all symbol profiles.
- `GET /api/multi-timeframe/{symbol}` — one symbol profile.
- `GET /api/multi-timeframe/debug` — diagnostics, source list, weights and storage path.
- `POST /api/ops/multi-timeframe/rebuild` — authenticated rebuild with OPS audit logging.
- `/ops/multi-timeframe` — dark OPS table with sorting and timeframe detail modal.

The home dashboard also shows a `MULTI TIMEFRAME` widget with top aligned symbols, highest conflict and strongest trend.

## Stage 27 — Confluence Engine

FXPilot now includes a deterministic Confluence Engine that aggregates stored subsystem outputs into one explainable per-symbol assessment. It does **not** call an LLM and its `confluence_score` represents strength of deterministic agreement, not expected profit or win probability.

Default factor weights are normalized to 100 across available data: Market State 20, Multi-Timeframe 20, Consensus 20, Signal Validation 15, Author Intelligence 10, Performance 10, Structured Reviews 5, and reserved `order_flow` 0. Missing factors are listed explicitly and their configured weight is redistributed across available factors instead of being treated as confirmation or conflict.

Public read-only endpoints:

- `GET /api/confluence`
- `GET /api/confluence/{symbol}`
- `GET /api/confluence/debug`

Operations endpoint and UI:

- `POST /api/ops/confluence/rebuild` with the existing `X-FXPILOT-OPS-TOKEN` header
- `/ops/confluence` for sortable/filterable dark-theme diagnostics

Persistence is stored atomically in `DATA_DIR/confluence.json`; if a rebuild fails, the previous valid file is preserved. Upstream rebuild flows for consensus, authors, performance, market state, multi-timeframe, validation scheduler updates, and performance completion hooks trigger a safe confluence refresh without recursive rebuild loops.

## Stage 28 — Opportunity Scanner

Opportunity Scanner builds `DATA_DIR/opportunities.json` from persisted Confluence, structured review ideas, validation and performance data. It is deterministic, does not call an LLM, and the Opportunity Score is a ranking score only — not financial advice, expected return, or probability of profit.

Public endpoints: `GET /api/opportunities`, `/api/opportunities/top`, `/api/opportunities/{symbol}`, `/api/opportunities/stats`, `/api/opportunities/debug`. Protected OPS rebuild: `POST /api/ops/opportunities/rebuild` with the existing OPS token. UI pages: `/opportunities` and `/ops/opportunities`.

## Stage 29 — Explainable Decision Engine

FXPilot now includes a deterministic Explainable Decision Engine that converts the ranked Opportunity Scanner output into one final machine-readable decision per symbol. It does not call an LLM, does not execute trades and keeps Opportunity Scanner eligibility as the primary upstream source.

Decision Score is a deterministic ranking and readiness score only. It is not a probability of profit, expected return or financial advice. Missing validation, risk levels and future extension factors (`order_flow`, `news_risk`, `liquidity`, `portfolio_risk`, `execution_readiness`) are reported honestly instead of being fabricated.

The engine persists atomically to `DATA_DIR/decisions.json` and preserves the previous valid file if a rebuild fails. Each decision includes action, readiness, score, confidence, stability, risk context, evidence, supporting/conflicting reasons, blockers, missing data, upgrade/downgrade conditions, deterministic explanations, source timestamps and an execution-candidate contract only for `READY` / `READY_WITH_WARNINGS` states.

Public read-only endpoints:

- `GET /api/decisions`
- `GET /api/decisions/top`
- `GET /api/decisions/actionable`
- `GET /api/decisions/{symbol}`
- `GET /api/decisions/stats`
- `GET /api/decisions/debug`

Protected OPS endpoint and UI:

- `POST /api/ops/decisions/rebuild` with the existing OPS token and audit/lock path.
- `/ops/decisions` for summary cards, filters, decision table and full audit detail panel.
- `/decisions` for a safe public read-only top-decisions page without hidden OPS diagnostics.

Successful Opportunity Scanner rebuilds trigger a safe Decision rebuild after opportunities are persisted. A Decision rebuild failure does not roll back Opportunities or upstream subsystem outputs.

## Stage 30 — Strategy Builder & Policy Engine

FXPilot now includes a deterministic Strategy Builder layer that evaluates existing Explainable Decision Engine output and Execution Candidate risk context against operator-defined policies. It does not call an LLM, does not place trades, and does not call MT4/MT5 or external execution systems.

- OPS UI: `/ops/strategies`
- Protected CRUD/test/rebuild API: `/api/ops/strategies*` using the existing `X-FXPILOT-OPS-TOKEN` header
- Public read-only API: `/api/strategies/active`, `/api/strategies/approved-signals`, `/api/strategies/approved-signals/{symbol}`, `/api/strategies/stats`
- Persistent runtime files: `DATA_DIR/strategies.json`, `DATA_DIR/strategy_evaluations.json`, `DATA_DIR/approved_signals.json`
- First-run templates live in `data/templates/default_strategies.json` and are not overwritten at runtime.

Supported policy fields include action, direction, readiness, decision/confidence/stability scores, opportunity/confluence/agreement/conflict/data-quality/freshness/validation/author/performance scores, dominant timeframe, urgency, execution risk fields, blocker/warning counts, missing data, symbol and timeframe. Supported operators are strict enums only: EQ, NE, GT, GTE, LT, LTE, IN, NOT_IN, EXISTS, NOT_EXISTS, CONTAINS, NOT_CONTAINS and BETWEEN.
