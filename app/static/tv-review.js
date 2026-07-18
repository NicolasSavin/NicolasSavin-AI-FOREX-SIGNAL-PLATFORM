(function initFxPilotReviewPage() {
  const root = document.getElementById('tvReviewPage');
  if (!root || !window.FXPilotTv) return;

  const { escapeHtml, formatDate, thumbnailUrl, formatDuration, reviewLabel, reviewClass, badge } = window.FXPilotTv;
  const getVideoId = () => {
    const parts = window.location.pathname.split('/').filter(Boolean);
    return parts[0] === 'tv' && parts[1] === 'review' ? decodeURIComponent(parts[2] || '') : '';
  };
  const has = (v) => !(v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length));
  const value = (input, fallback = 'Не определено') => has(input) ? input : fallback;
  const percent = (input) => has(input) && Number.isFinite(Number(input)) ? `${Math.round(Number(input))}%` : 'Не определено';
  const compact = (items) => items.filter(([_, v]) => has(v));
  const youtubeUrl = (video) => video.youtube_id ? `https://www.youtube.com/watch?v=${encodeURIComponent(video.youtube_id)}` : '#';
  const directionRu = (direction) => ({ BUY: 'Покупка', SELL: 'Продажа', WAIT: 'Ожидание', NEUTRAL: 'Нейтрально' }[String(direction || '').toUpperCase()] || value(direction));
  const biasRu = (bias) => ({ bullish: 'Бычий', bearish: 'Медвежий', neutral: 'Нейтральный', mixed: 'Смешанный' }[String(bias || '').toLowerCase()] || value(bias));
  const riskLevel = (review) => {
    const risks = [...(review.llm_review?.risks || []), ...(review.analysis?.risks || []), ...(review.warnings || [])].filter(Boolean).length;
    if (risks >= 4) return 'Высокий';
    if (risks >= 2) return 'Средний';
    return risks ? 'Умеренный' : 'Низкий';
  };

  function metricGrid(rows, cls = '') {
    return `<div class="tv-report-metrics ${cls}">${rows.map(([label, item]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value(item))}</strong></div>`).join('')}</div>`;
  }
  function section(id, title, content, cls = '') {
    return `<section id="${escapeHtml(id)}" class="tv-report-card ${escapeHtml(cls)}"><div class="tv-report-card__head"><p class="section-kicker">Institutional Review</p><h2>${escapeHtml(title)}</h2></div>${content}</section>`;
  }
  function pillList(items, empty = '') {
    const arr = Array.isArray(items) ? items.filter(has) : [];
    if (!arr.length) return empty;
    return `<div class="tv-report-pill-list">${arr.map((item) => `<span>${escapeHtml(item)}</span>`).join('')}</div>`;
  }
  function paragraph(text, fallback = 'Не определено') {
    return `<p class="tv-report-copy">${escapeHtml(value(text, fallback))}</p>`;
  }

  function renderHeader(review) {
    const video = review.video || {};
    const llm = review.llm_review || {};
    const thumb = thumbnailUrl(video);
    const status = review.review_status || (llm.summary || review.primary_symbol || review.direction ? 'ready' : 'missing');
    const chips = [
      badge(String(review.direction || llm.direction || 'NEUTRAL').toUpperCase(), `direction ${String(review.direction || llm.direction || 'neutral').toLowerCase()}`),
      badge(review.primary_symbol || review.symbol || video.symbol, 'symbol'),
      badge(review.timeframe || llm.timeframe || video.timeframe, 'timeframe'),
      badge(percent(review.confidence || llm.confidence), 'confidence'),
      `<span class="${reviewClass(status)}">${escapeHtml(status === 'ready' ? 'Review Ready' : reviewLabel(status))}</span>`,
    ].filter(Boolean).join('');
    return `<section class="tv-report-hero panel">
      <div class="tv-report-thumb" style="${thumb ? `background-image:url('${escapeHtml(thumb)}')` : ''}" aria-label="Превью видео"></div>
      <div class="tv-report-hero__body">
        <div class="tv-report-actions tv-report-actions--top"><a class="tv-back-link" href="/tv">← Назад в TV</a>${chips}</div>
        <h1>${escapeHtml(video.title || 'AI Review FXPilot TV')}</h1>
        <p>${escapeHtml(video.description || 'Профессиональный read-only отчёт по сохранённому AI Review.')}</p>
        ${metricGrid([
          ['Автор', video.author || video.channel || review.author_source?.author],
          ['Публикация', formatDate(video.published_at)],
          ['Длительность', formatDuration(video.duration)],
          ['Источник', video.source || video.provider || review.author_source?.provider || 'YouTube'],
          ['Категория', video.category],
          ['Основной символ', review.primary_symbol || video.symbol],
          ['Таймфрейм', review.timeframe || video.timeframe],
          ['Направление', directionRu(review.direction)],
          ['Confidence', percent(review.confidence)],
        ], 'tv-report-metrics--hero')}
      </div>
    </section>`;
  }

  function renderSummary(review) {
    const llm = review.llm_review || {};
    return section('executiveSummary', 'Executive Summary', `
      <div class="tv-summary-grid">
        <article><span>Общее мнение</span>${paragraph(llm.summary || review.analysis?.summary || review.ai_review?.summary, 'Резюме не сформировано.')}</article>
        <article><span>Ключевая идея</span>${paragraph(llm.market_overview || llm.recommended_action || review.comparison?.video_says)}</article>
        <article><span>Институциональная интерпретация</span>${paragraph(llm.institutional_view || review.knowledge_context?.institutional_narrative || review.current_fxpilot_idea?.institutional_narrative)}</article>
      </div>
      ${metricGrid([
        ['Confidence', percent(llm.confidence || review.confidence)],
        ['Market bias', biasRu(llm.market_bias || review.knowledge_context?.market_bias)],
        ['Риск', riskLevel(review)],
        ['Agreement', has(llm.agreement_score || review.agreement_score) ? `${llm.agreement_score || review.agreement_score}/100` : null],
      ])}`, 'tv-report-card--wide');
  }

  function renderTradeSetup(review) {
    const rows = [
      ['Entry', review.entry], ['Entry zone', Array.isArray(review.entry_zone) ? review.entry_zone.join(' — ') : review.entry_zone],
      ['Stop Loss', review.stop_loss], ['Take Profit', review.take_profit], ['Targets', Array.isArray(review.targets) ? review.targets.join(', ') : review.targets],
      ['Risk/Reward', review.risk_reward || review.llm_review?.risk_reward],
    ];
    const ideas = Array.isArray(review.trade_ideas) ? review.trade_ideas : [];
    const cards = ideas.map((idea, i) => `<article class="tv-trade-idea-card"><strong>Trade Setup ${i + 1}: ${escapeHtml(value(idea.symbol || review.primary_symbol))}</strong>${metricGrid([
      ['Direction', directionRu(idea.direction)], ['Timeframe', idea.timeframe], ['Entry', idea.entry_zone?.length ? idea.entry_zone.join(' — ') : idea.entry], ['SL', idea.stop_loss], ['TP', idea.take_profit || (idea.targets || []).join(', ')], ['Confidence', percent(idea.confidence)]
    ])}${paragraph(idea.reasoning)}</article>`).join('');
    return section('tradeSetup', 'Trade Setup', `${metricGrid(rows)}${cards || '<div class="tv-report-empty">Trade setup: Не определено</div>'}`);
  }

  function renderMarketContext(review) {
    const levels = Array.isArray(review.detected_levels) ? review.detected_levels.map((l) => [l.name || l.type || 'Level', l.price || l.value || l.zone].filter(has).join(': ')) : [];
    const rows = compact([
      ['Primary symbol', review.primary_symbol || review.symbol], ['Additional symbols', (review.symbols || []).filter((s) => s !== review.primary_symbol).join(', ')],
      ['Detected levels', levels.join(' · ')], ['Trend', review.llm_review?.trend || review.analysis?.trend], ['Timeframe', review.timeframe], ['Market structure', review.llm_review?.market_structure || review.analysis?.market_structure],
    ]);
    return rows.length ? section('marketContext', 'Market Context', metricGrid(rows)) : '';
  }

  function renderInsights(review) {
    const llm = review.llm_review || {}, analysis = review.analysis || {};
    const groups = [
      ['Бычьи аргументы', llm.bullish_arguments || analysis.opportunities], ['Медвежьи аргументы', llm.bearish_arguments || analysis.risks],
      ['Ключевые риски', llm.risks || review.warnings], ['Важные события', llm.important_events || analysis.events],
      ['Ошибки трейдинга', llm.trading_mistakes || analysis.trading_mistakes], ['Психология', llm.psychology || analysis.psychology],
    ].map(([title, items]) => pillList(items) ? `<article><strong>${escapeHtml(title)}</strong>${pillList(items)}</article>` : '').filter(Boolean).join('');
    return groups ? section('aiInsights', 'AI Insights', `<div class="tv-insights-grid">${groups}</div>`) : '';
  }

  function renderTranscript(transcript) {
    const available = transcript && transcript.status === 'FOUND' && has(transcript.text);
    const reason = transcript?.status === 'WHISPER_REQUIRED' ? 'Нужна обработка Whisper; сохранённый текст пока отсутствует.' : transcript?.status === 'ERROR' ? 'Источник транскрипта вернул ошибку.' : 'Транскрипт отсутствует в сохранённых данных review.';
    const preview = available ? String(transcript.text).split(/\n{2,}/).map((p) => p.trim()).filter(Boolean).slice(0, 3).map((p) => `<p>${escapeHtml(p)}</p>`).join('') : '';
    return section('transcript', 'Transcript', `${metricGrid([
      ['Источник', transcript?.provider], ['Язык', transcript?.language], ['Доступность', available ? 'Доступен' : 'Недоступен'],
    ])}${available ? `<div class="tv-transcript-preview">${preview}</div>` : `<div class="tv-report-empty">${escapeHtml(reason)}</div>`}`);
  }

  function renderQuickActions(review) {
    const video = review.video || {};
    const committee = `/investment-committee?video_id=${encodeURIComponent(video.id || getVideoId())}`;
    const sym = review.primary_symbol || review.symbol || review.llm_review?.primary_symbol;
    const symbolLink = sym ? `<a class="tv-check-button" href="/symbols/${encodeURIComponent(sym)}">Открыть аналитику символа</a>` : '';
    return `<section class="tv-report-actions panel"><a class="tv-check-button" target="_blank" rel="noopener" href="${escapeHtml(youtubeUrl(video))}">Смотреть на YouTube</a><a class="tv-check-button" href="/tv">Назад в TV</a><a class="tv-check-button" href="${escapeHtml(committee)}">Открыть Committee</a>${symbolLink}<button class="tv-check-button" type="button" id="copyReviewLink">Скопировать ссылку</button></section>`;
  }

  function ReviewPage(review, transcript) {
    return `${renderHeader(review)}${renderQuickActions(review)}<div class="tv-report-layout">${renderSummary(review)}${renderTradeSetup(review)}${renderMarketContext(review)}${renderInsights(review)}${renderTranscript(transcript)}</div>`;
  }

  async function loadReview() {
    const response = await fetch(`/api/media/review/${encodeURIComponent(getVideoId())}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error(response.status === 404 ? 'missing' : 'failed');
    const review = await response.json();
    root.innerHTML = ReviewPage(review, review.transcript || { status: 'NOT_AVAILABLE' });
    document.getElementById('copyReviewLink')?.addEventListener('click', async () => navigator.clipboard?.writeText(window.location.href));
  }

  loadReview().catch((error) => {
    const status = error.message === 'missing' ? 'missing' : 'failed';
    root.innerHTML = `<section class="panel tv-report-status"><span class="${reviewClass(status)}">${escapeHtml(reviewLabel(status))}</span><h2>${status === 'missing' ? 'Review отсутствует' : 'Review недоступен'}</h2><p>Отчёт ещё не готов или не найден в сохранённом хранилище. Backend exceptions не отображаются пользователю.</p><a class="tv-check-button" href="/tv">Вернуться в TV</a></section>`;
  });
})();
