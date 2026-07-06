(function initFxPilotReviewPage() {
  const root = document.getElementById('tvReviewPage');
  if (!root || !window.FXPilotTv) return;

  const { escapeHtml, formatDate, VideoPlayer, CategoryBadges, ReviewSection } = window.FXPilotTv;

  const getVideoId = () => {
    const parts = window.location.pathname.split('/').filter(Boolean);
    return parts[0] === 'tv' && parts[1] === 'review' ? decodeURIComponent(parts[2] || '') : '';
  };

  const asArray = (value) => Array.isArray(value) ? value : [];
  const firstDefined = (...values) => values.find((value) => value !== undefined && value !== null && value !== '');
  const normalizeSymbol = (value) => String(value || '').replace(/[^A-Z0-9]/gi, '').toUpperCase();
  const formatValue = (value) => firstDefined(value, '—');

  function flattenIdeas(payload) {
    if (!payload || typeof payload !== 'object') return [];
    return [...asArray(payload.ideas), ...asArray(payload.signals), ...asArray(payload.active), ...asArray(payload.archive)]
      .filter((item) => item && typeof item === 'object');
  }

  function findMarketContext(payload, symbol) {
    const wanted = normalizeSymbol(symbol);
    if (!wanted) return null;
    return flattenIdeas(payload).find((idea) => normalizeSymbol(firstDefined(idea.symbol, idea.pair, idea.instrument, idea.ticker)) === wanted) || null;
  }

  function marketContextCard(idea, symbol) {
    if (!idea) {
      return `<p class="tv-context-empty">Текущий контекст FXPilot по инструменту ${escapeHtml(symbol || '—')} пока недоступен.</p>`;
    }

    const action = firstDefined(idea.action, idea.direction, idea.signal, idea.recommendation, idea.bias, 'WAIT');
    const confidence = firstDefined(idea.confidence, idea.score, idea.confidence_score, idea.prop_score, idea.total_score);
    const grade = firstDefined(idea.grade, idea.rating, idea.quality_grade);
    const entry = firstDefined(idea.entry, idea.entry_price, idea.entryPrice, idea.levels?.entry);
    const sl = firstDefined(idea.sl, idea.stop_loss, idea.stopLoss, idea.levels?.sl, idea.levels?.stop_loss);
    const tp = firstDefined(idea.tp, idea.take_profit, idea.takeProfit, idea.levels?.tp, idea.levels?.take_profit);
    const rows = [
      ['Действие / направление', action],
      ['Confidence / score', confidence],
      ['Grade', grade],
      ['Entry / SL / TP', [entry, sl, tp].map(formatValue).join(' / ')],
      ['Источник данных', firstDefined(idea.data_source_label, idea.source_label, idea.market_data?.data_source_label)],
      ['Качество данных', firstDefined(idea.data_source_quality, idea.source_quality, idea.market_data?.data_source_quality)],
      ['OrderFlow доступен', firstDefined(idea.orderflow_available, idea.order_flow_available, idea.orderflow?.available)],
      ['Market structure', firstDefined(idea.market_structure, idea.structure, idea.smc?.market_structure)],
      ['Trend regime', firstDefined(idea.trend_regime, idea.regime, idea.market_regime)],
      ['News risk', firstDefined(idea.news_risk, idea.newsRisk, idea.news?.risk)],
      ['Options доступны', firstDefined(idea.options_available, idea.options?.available)],
    ];

    return `
      <div class="tv-market-context-card">
        <div class="tv-context-head"><span>${escapeHtml(firstDefined(idea.symbol, symbol))}</span><strong>${escapeHtml(action)}</strong></div>
        <div class="tv-context-grid">
          ${rows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(formatValue(value))}</strong></div>`).join('')}
        </div>
        <p class="tv-context-note">Контекст загружен из /api/ideas/market. Это не анализ видео и не новый торговый сигнал.</p>
      </div>
    `;
  }

  function PremiumList(items) {
    return `<ul class="tv-premium-list">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
  }

  function ReviewPage(payload, marketPayload) {
    const video = payload.video || {};
    const review = payload.review || {};
    const marketIdea = findMarketContext(marketPayload, video.symbol);
    const player = VideoPlayer(video, { autoplay: false, titleFallback: 'FXPilot TV Review' });

    return `
      <section class="panel tv-review-watch" id="tvReviewWatch" data-video-id="${escapeHtml(video.id)}">
        <a class="tv-back-link" href="/tv">← Вернуться к каталогу FXPilot TV</a>
        <div class="tv-player-frame tv-review-player" id="reviewPlayer">${player}</div>
        <article class="tv-selected-card tv-review-meta" id="reviewVideoMeta">
          <div class="tv-detail-top">
            <div id="reviewBadges">${CategoryBadges(video)}<span class="tv-duration-badge" id="reviewDuration">${escapeHtml(video.duration || '—')}</span></div>
            <time id="reviewPublishDate" datetime="${escapeHtml(video.published_at)}">${escapeHtml(formatDate(video.published_at))}</time>
          </div>
          <h2 id="reviewTitle">${escapeHtml(video.title || 'Видеообзор FXPilot TV')}</h2>
          <p class="tv-author" id="reviewAuthor">Автор: ${escapeHtml(video.author || 'Не указан')} · ${escapeHtml(video.category || 'Категория не указана')}</p>
          <p id="reviewDescription">${escapeHtml(video.description || 'Описание будет добавлено позже.')}</p>
          <p class="tv-review-slogan">Не просто сигналы. Понимание рынка. Проверяем торговые идеи фактами.</p>
        </article>
      </section>
      <div class="tv-review-grid" id="reviewSections">
        ${ReviewSection({ id: 'videoOverview', className: 'tv-review-section--summary', title: 'Видеообзор', content: `<p>${escapeHtml(review.ai_summary)}</p><p class="tv-coming-soon">AI transcript analysis будет подключен позже</p>` })}
        ${ReviewSection({ id: 'analysisScope', title: 'Что будет анализироваться', content: PremiumList(['Тезисы автора, уровни, invalidation и риск-менеджмент.', 'Совпадение сценариев с текущим контекстом FXPilot.', 'Фактическая проверка прогноза после движения рынка без изменения торговой логики.']) })}
        ${ReviewSection({ id: 'currentFxpilotView', className: 'tv-review-section--summary', title: 'Текущий взгляд FXPilot', content: marketContextCard(marketIdea, video.symbol) })}
        ${ReviewSection({ id: 'smartMoney', title: 'Smart Money', content: '<p>Будущий модуль сопоставит заявленные sweep, BOS/CHOCH, imbalance и premium/discount зоны с фактической структурой рынка.</p>' })}
        ${ReviewSection({ id: 'orderFlow', title: 'OrderFlow', content: '<p>Будет проверяться наличие подтверждения объемом, реакция у уровней, признаки поглощения и доступность OrderFlow-данных.</p>' })}
        ${ReviewSection({ id: 'options', title: 'Options', content: '<p>Опционный блок будет отмечать контекстные уровни, доступность метрик и ограничения: опционы — это фильтр вероятности, а не самостоятельный сигнал.</p>' })}
        ${ReviewSection({ id: 'newsMacro', title: 'News / Macro', content: '<p>Макро-блок подготовлен для проверки календарных рисков, долларового режима, ставок, доходностей и чувствительности выбранного инструмента.</p>' })}
        ${ReviewSection({ id: 'realityCheck', title: 'Reality Check', content: '<p>После подключения факт-чека FXPilot сравнит прогноз с последующим рынком: что подтвердилось, где сценарий был отменен и насколько четко автор обозначил риск.</p>' })}
        ${ReviewSection({ id: 'trustScore', className: 'tv-review-section--score', title: 'Trust Score', content: `<div class="tv-review-score">${escapeHtml(review.agreement_score ?? 72)}%</div><p>Премиальный placeholder доверия. Оценка демонстрирует будущий формат и не является реальным рейтингом автора.</p>` })}
      </div>
    `;
  }

  async function loadReview() {
    const videoId = getVideoId();
    const reviewResponse = await fetch(`/api/tv/review/${encodeURIComponent(videoId)}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!reviewResponse.ok) throw new Error('review_not_found');
    const reviewPayload = await reviewResponse.json();
    let marketPayload = null;
    try {
      const marketResponse = await fetch('/api/ideas/market', { headers: { Accept: 'application/json' }, cache: 'no-store' });
      if (marketResponse.ok) marketPayload = await marketResponse.json();
    } catch (error) {
      marketPayload = null;
    }
    root.innerHTML = ReviewPage(reviewPayload, marketPayload);
  }

  loadReview().catch(() => {
    root.innerHTML = '<section class="panel"><div class="tv-player-empty">Review не найден. Вернитесь в каталог FXPilot TV и выберите другой обзор.</div></section>';
  });
})();
