(function initFxPilotReviewPage() {
  const root = document.getElementById('tvReviewPage');
  if (!root || !window.FXPilotTv) return;

  const { escapeHtml, formatDate, thumbnailUrl, CategoryBadges, ReviewSection } = window.FXPilotTv;

  const getVideoId = () => {
    const parts = window.location.pathname.split('/').filter(Boolean);
    return parts[0] === 'tv' && parts[1] === 'review' ? decodeURIComponent(parts[2] || '') : '';
  };

  const asArray = (value) => Array.isArray(value) ? value : [];
  const firstDefined = (...values) => values.find((value) => value !== undefined && value !== null && value !== '');
  const normalizeSymbol = (value) => String(value || '').replace(/[^A-Z0-9]/gi, '').toUpperCase();
  const normalizeText = (value) => String(value || '').trim();
  const formatValue = (value) => firstDefined(value, '—');
  const toNumber = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const percent = (value) => {
    const number = toNumber(value);
    if (number === null) return '—';
    return `${Math.max(0, Math.min(100, Math.round(number)))}%`;
  };

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

  function directionOf(idea) {
    const raw = normalizeText(firstDefined(idea?.action, idea?.direction, idea?.signal, idea?.final_signal, idea?.recommendation, idea?.bias, 'WAIT'));
    const upper = raw.toUpperCase();
    if (['BUY', 'ПОКУПКА', 'LONG', 'BULLISH', 'БЫЧИЙ'].includes(upper)) return 'BUY';
    if (['SELL', 'ПРОДАЖА', 'SHORT', 'BEARISH', 'МЕДВЕЖИЙ'].includes(upper)) return 'SELL';
    return upper || 'WAIT';
  }

  function marketModel(idea, symbol) {
    const entry = firstDefined(idea?.entry, idea?.entry_price, idea?.entryPrice, idea?.levels?.entry, idea?.setup?.entry);
    const sl = firstDefined(idea?.sl, idea?.stop_loss, idea?.stopLoss, idea?.levels?.sl, idea?.levels?.stop_loss);
    const tp = firstDefined(idea?.tp, idea?.take_profit, idea?.takeProfit, idea?.levels?.tp, idea?.levels?.take_profit);
    const confidence = firstDefined(idea?.confidence, idea?.score, idea?.confidence_score, idea?.prop_score, idea?.total_score, idea?.confluence);
    return {
      symbol: firstDefined(idea?.symbol, idea?.pair, idea?.instrument, symbol, '—'),
      price: firstDefined(idea?.current_price, idea?.price, idea?.market_price, idea?.market_data?.price, idea?.snapshot?.price),
      direction: idea ? directionOf(idea) : 'WAIT',
      entry,
      sl,
      tp,
      confidence,
      grade: firstDefined(idea?.grade, idea?.rating, idea?.quality_grade, idea?.prop_grade),
      mode: firstDefined(idea?.mode, idea?.signal_mode, idea?.data_mode, idea?.market_data?.mode, idea?.data_source_label),
      updatedAt: firstDefined(idea?.updated_at, idea?.timestamp, idea?.created_at, idea?.market_data?.updated_at),
      trend: firstDefined(idea?.trend, idea?.trend_regime, idea?.regime, idea?.market_regime, idea?.market_context?.trend),
      structure: firstDefined(idea?.market_structure, idea?.structure, idea?.smc?.market_structure, idea?.market_context?.structure),
      newsRisk: firstDefined(idea?.news_risk, idea?.newsRisk, idea?.news?.risk, idea?.news_context?.risk),
      orderflowAvailable: firstDefined(idea?.orderflow_available, idea?.order_flow_available, idea?.orderflow?.available),
      orderflowBias: firstDefined(idea?.orderflow_bias, idea?.orderFlowBias, idea?.orderflow?.bias),
      optionsAvailable: firstDefined(idea?.options_available, idea?.optionsAvailable, idea?.options?.available),
      optionsBias: firstDefined(idea?.options_bias, idea?.optionsBias, idea?.external_options_bias, idea?.options?.bias),
      institutional: firstDefined(idea?.institutional_narrative, idea?.narrative, idea?.market_context?.institutional_narrative),
      sourceQuality: firstDefined(idea?.data_source_quality, idea?.source_quality, idea?.market_data?.data_source_quality),
    };
  }

  function statusFrom(value, direction) {
    const text = normalizeText(value).toUpperCase();
    if (!text) return 'neutral';
    if (direction && text.includes(direction)) return 'agree';
    if (['BUY', 'SELL'].includes(text) && text !== direction) return 'conflict';
    if (text.includes('BEAR') && direction === 'BUY') return 'conflict';
    if (text.includes('BULL') && direction === 'SELL') return 'conflict';
    if (text.includes('RISK') || text.includes('CONFLICT')) return 'conflict';
    if (text.includes('NEUTRAL') || text.includes('WAIT') || text.includes('ОЖИД')) return 'neutral';
    return 'agree';
  }

  function intelligenceCards(model) {
    const direction = model.direction;
    return [
      { icon: '◇', title: 'Smart Money', status: statusFrom(model.structure, direction), summary: model.structure ? `Структура рынка: ${model.structure}` : 'SMC-контекст не найден в текущем payload FXPilot.', confidence: model.confidence },
      { icon: '⇄', title: 'OrderFlow', status: model.orderflowAvailable ? statusFrom(model.orderflowBias || direction, direction) : 'neutral', summary: model.orderflowAvailable ? `OrderFlow bias: ${formatValue(model.orderflowBias || direction)}` : 'OrderFlow недоступен или отключён; подтверждение не подменяется proxy-значением.', confidence: model.orderflowAvailable ? model.confidence : null },
      { icon: '🏛', title: 'Institutional Narrative', status: statusFrom(model.institutional || direction, direction), summary: model.institutional || `Институциональный сценарий следует текущему направлению FXPilot: ${direction}.`, confidence: model.confidence },
      { icon: '⌁', title: 'Options', status: model.optionsAvailable ? statusFrom(model.optionsBias, direction) : 'neutral', summary: model.optionsAvailable ? `Options bias: ${formatValue(model.optionsBias)}. Метрики помечены как доступные из текущего FXPilot.` : 'Опционные данные недоступны; proxy metrics не используются.', confidence: model.optionsAvailable ? model.confidence : null },
      { icon: '📰', title: 'News', status: statusFrom(model.newsRisk, direction), summary: model.newsRisk ? `Новостной риск: ${model.newsRisk}` : 'Новостной фон не содержит явного конфликта в текущем payload.', confidence: model.confidence },
      { icon: '▣', title: 'Market Structure', status: statusFrom(model.structure, direction), summary: model.structure ? `Текущая структура: ${model.structure}` : 'Структурный слой пока не вернул детализированное состояние.', confidence: model.confidence },
      { icon: '↗', title: 'Trend', status: statusFrom(model.trend || direction, direction), summary: model.trend ? `Трендовый режим: ${model.trend}` : `Тренд читается через направление текущего сигнала: ${direction}.`, confidence: model.confidence },
      { icon: '⚖', title: 'Risk / Reward', status: model.entry && model.sl && model.tp ? 'agree' : 'neutral', summary: model.entry && model.sl && model.tp ? `План уровней: вход ${model.entry}, SL ${model.sl}, TP ${model.tp}.` : 'Полный набор Entry / SL / TP не найден, оценка R/R нейтральная.', confidence: model.confidence },
    ];
  }

  const statusLabel = (status) => ({ agree: 'Agree', neutral: 'Neutral', conflict: 'Conflict' }[status] || 'Neutral');

  function renderVideoSection(video) {
    const thumb = thumbnailUrl(video);
    return ReviewSection({ id: 'videoIntel', className: 'tv-review-section--summary', title: 'Видео', content: `
      <div class="tv-intel-video">
        <div class="tv-intel-thumb" style="${thumb ? `background-image:url('${escapeHtml(thumb)}')` : ''}" aria-label="Thumbnail"></div>
        <div class="tv-intel-video__body">
          <div class="tv-detail-top"><div>${CategoryBadges(video)}<span class="tv-duration-badge">${escapeHtml(formatValue(video.duration))}</span></div><time datetime="${escapeHtml(video.published_at)}">${escapeHtml(formatDate(video.published_at))}</time></div>
          <h2>${escapeHtml(video.title || 'Видеообзор FXPilot TV')}</h2>
          <div class="tv-player-meta-grid tv-review-meta-grid">
            ${[['Автор', video.author], ['Дата публикации', formatDate(video.published_at)], ['Длительность', video.duration], ['Категория', video.category], ['Символ', video.symbol], ['Таймфрейм', video.timeframe]].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(formatValue(value))}</strong></div>`).join('')}
          </div>
        </div>
      </div>` });
  }

  function renderAuthorThesis() {
    return ReviewSection({ id: 'authorThesis', className: 'tv-review-section--summary tv-author-thesis', title: 'Тезис автора', content: '<div class="tv-premium-placeholder"><strong>No transcript available.</strong><p>AI Summary will appear after transcript analysis.</p></div>' });
  }

  function renderFxPilotAnalysis(cards) {
    return ReviewSection({ id: 'fxpilotAnalysis', className: 'tv-review-section--summary', title: 'FXPilot Analysis', content: `<div class="tv-intel-card-grid">${cards.map((card) => `<article class="tv-intel-card is-${escapeHtml(card.status)}"><div class="tv-intel-card__icon">${escapeHtml(card.icon)}</div><div><span>${escapeHtml(statusLabel(card.status))}</span><h3>${escapeHtml(card.title)}</h3><p>${escapeHtml(card.summary)}</p><small>Confidence: ${escapeHtml(percent(card.confidence))}</small></div></article>`).join('')}</div>` });
  }

  function renderSnapshot(model) {
    const rows = [['Current price', model.price], ['Direction', model.direction], ['Entry', model.entry], ['SL', model.sl], ['TP', model.tp], ['Confidence', percent(model.confidence)], ['Grade', model.grade], ['Mode', model.mode], ['Update time', model.updatedAt]];
    return ReviewSection({ id: 'marketSnapshot', className: 'tv-review-section--summary', title: 'Market Snapshot', content: `<div class="tv-snapshot-grid">${rows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(formatValue(value))}</strong></div>`).join('')}</div><p class="tv-context-note">Снимок построен из /api/ideas/market и не является анализом transcript.</p>` });
  }

  function confluence(model) {
    const direction = model.direction;
    const items = [
      ['Direction', ['BUY', 'SELL'].includes(direction) ? 'agree' : 'neutral'],
      ['Entry zone', model.entry ? 'agree' : 'neutral'],
      ['Trend', statusFrom(model.trend || direction, direction)],
      ['Structure', statusFrom(model.structure, direction)],
      ['News', statusFrom(model.newsRisk, direction)],
      ['OrderFlow', model.orderflowAvailable ? statusFrom(model.orderflowBias || direction, direction) : 'neutral'],
    ];
    const score = Math.round(items.reduce((sum, [, status]) => sum + (status === 'agree' ? 100 : status === 'neutral' ? 50 : 0), 0) / items.length);
    return { items, score };
  }

  function renderConfluence(result) {
    return ReviewSection({ id: 'confluence', className: 'tv-review-section--summary', title: 'Confluence', content: `<div class="tv-confluence-score"><strong>${result.score}</strong><span>Agreement Score</span></div><div class="tv-confluence-list">${result.items.map(([label, status]) => `<div class="is-${escapeHtml(status)}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(statusLabel(status))}</strong></div>`).join('')}</div>` });
  }

  function renderVerdict(model, confluenceResult) {
    const directionText = model.direction === 'BUY' ? 'BUY' : model.direction === 'SELL' ? 'SELL' : 'WAIT';
    const orderflow = confluenceResult.items.find(([label]) => label === 'OrderFlow')?.[1];
    const news = confluenceResult.items.find(([label]) => label === 'News')?.[1];
    const tone = confluenceResult.score >= 70 ? 'поддерживает текущий сценарий' : confluenceResult.score >= 45 ? 'даёт смешанную картину' : 'конфликтует с текущим сценарием';
    const lines = [
      `FXPilot сейчас ${tone} по ${model.symbol}: направление ${directionText}.`,
      `OrderFlow: ${statusLabel(orderflow)}.`,
      `News: ${statusLabel(news)}.`,
      model.institutional ? `Institutional Narrative: ${model.institutional}.` : `Institutional Narrative поддерживает ${directionText}, если текущий сигнал не WAIT.`,
    ];
    return ReviewSection({ id: 'preliminaryVerdict', className: 'tv-review-section--summary tv-verdict-section', title: 'Preliminary Verdict', content: `<div class="tv-verdict"><strong>${confluenceResult.score >= 70 ? 'Scenario Supported' : confluenceResult.score >= 45 ? 'Needs Confirmation' : 'Scenario Conflict'}</strong>${lines.map((line) => `<p>${escapeHtml(line)}</p>`).join('')}</div>` });
  }

  function renderComingSoon() {
    const modules = ['Reality Check', 'Trust Score', 'AI Summary', 'Transcript', 'Historical Similarity'];
    return ReviewSection({ id: 'comingSoon', className: 'tv-review-section--summary', title: 'Coming Soon', content: `<div class="tv-coming-grid">${modules.map((item) => `<div class="tv-premium-placeholder"><strong>${escapeHtml(item)}</strong><p>Будущий AI-модуль подключится к этому reusable блоку без редизайна страницы.</p></div>`).join('')}</div>` });
  }

  function ReviewPage(payload, marketPayload) {
    const video = payload.video || {};
    const marketIdea = findMarketContext(marketPayload, video.symbol);
    const model = marketModel(marketIdea, video.symbol);
    const cards = intelligenceCards(model);
    const confluenceResult = confluence(model);
    return `<section class="panel tv-review-watch" data-video-id="${escapeHtml(video.id)}"><a class="tv-back-link" href="/tv">← Вернуться к каталогу FXPilot TV</a><p class="tv-review-slogan">AI Review Engine v1: текущая разведка FXPilot без LLM, transcript parsing и YouTube API.</p></section><div class="tv-review-grid" id="reviewSections">${renderVideoSection(video)}${renderAuthorThesis()}${renderFxPilotAnalysis(cards)}${renderSnapshot(model)}${renderConfluence(confluenceResult)}${renderVerdict(model, confluenceResult)}${renderComingSoon()}</div>`;
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
