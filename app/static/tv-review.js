(function initFxPilotReviewPage() {
  const root = document.getElementById('tvReviewPage');
  if (!root || !window.FXPilotTv) return;

  const { escapeHtml, formatDate, metaItems, VideoPlayer, CategoryBadges, ReviewSection } = window.FXPilotTv;

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

  function placeholder(text) {
    return `<p>${escapeHtml(text)}</p><p class="tv-coming-soon">Данные будут доступны после будущего AI / Reality Check слоя</p>`;
  }

  function reviewTimeline(video) {
    const items = [
      ['Video Published', formatDate(video.published_at), 'Реальная дата из локального каталога видео.'],
      ['Author Thesis', 'Placeholder', 'Тезис автора не извлекается: transcript parsing не подключен.'],
      ['Current FXPilot View', 'Live context / unavailable', 'Показывается только доступный текущий контекст платформы без изменения торговой логики.'],
      ['Reality Check', 'Coming Soon', 'Факт-чек будет добавлен позже.'],
      ['Historical Result', 'Coming Soon', 'Исторический результат не рассчитывается на этом этапе.'],
    ];
    return `<div class="tv-review-timeline">${items.map(([title, value, text]) => `<div><span>${escapeHtml(title)}</span><strong>${escapeHtml(value)}</strong><p>${escapeHtml(text)}</p></div>`).join('')}</div>`;
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
          <div class="tv-player-meta-grid tv-review-meta-grid">${metaItems(video).map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('')}</div>
          <p id="reviewDescription">${escapeHtml(video.description || 'Описание будет добавлено позже.')}</p>
          <p class="tv-review-slogan">Не просто сигналы. Понимание рынка. Проверяем торговые идеи фактами.</p>
        </article>
      </section>
      <div class="tv-review-grid" id="reviewSections">
        ${ReviewSection({ id: 'aiSummary', className: 'tv-review-section--summary', title: 'AI Summary', content: placeholder(review.ai_summary || 'AI-резюме недоступно: AI implementation не подключен в этом спринте.') })}
        ${ReviewSection({ id: 'mainThesis', title: 'Main Thesis', content: placeholder('Главный тезис автора будет показан после подключения безопасного анализа transcript.') })}
        ${ReviewSection({ id: 'currentFxpilotView', className: 'tv-review-section--summary', title: 'Current FXPilot View', content: marketContextCard(marketIdea, video.symbol) })}
        ${ReviewSection({ id: 'smartMoney', title: 'Smart Money', content: placeholder('SMC-контекст зарезервирован для будущей сверки sweep, BOS/CHOCH, imbalance и зон ликвидности.') })}
        ${ReviewSection({ id: 'orderFlow', title: 'OrderFlow', content: placeholder('OrderFlow-блок будет показывать только доступные подтверждения и ограничения данных.') })}
        ${ReviewSection({ id: 'options', title: 'Options', content: placeholder('Опционный блок будет явно отделять proxy metrics от реальных доступных метрик.') })}
        ${ReviewSection({ id: 'macro', title: 'Macro', content: placeholder('Макро-карта будет учитывать календарные риски, режим доллара, ставки и доходности после подключения данных.') })}
        ${ReviewSection({ id: 'institutionalNarrative', className: 'tv-review-section--summary', title: 'Institutional Narrative', content: PremiumList(['Видео: тезис автора будет добавлен после AI-слоя.', 'Платформа: текущий FXPilot-контекст показан отдельно и не является анализом видео.', 'Риск: недоступные данные не подменяются сгенерированными значениями.']) })}
        ${ReviewSection({ id: 'realityCheck', title: 'Reality Check (Coming Soon)', content: placeholder('Факт-чек прогноза после публикации появится на следующем этапе.') })}
        ${ReviewSection({ id: 'trustScore', className: 'tv-review-section--score', title: 'Trust Score (Coming Soon)', content: `<div class="tv-review-score">—</div><p>Рейтинг доверия не рассчитывается в этом спринте, чтобы не создавать фиктивные оценки автора.</p>` })}
        ${ReviewSection({ id: 'reviewTimeline', className: 'tv-review-section--summary', title: 'Timeline', content: reviewTimeline(video) })}
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
