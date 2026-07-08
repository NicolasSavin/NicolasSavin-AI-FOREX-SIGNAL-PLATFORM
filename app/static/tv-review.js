(function initFxPilotReviewPage() {
  const root = document.getElementById('tvReviewPage');
  if (!root || !window.FXPilotTv) return;

  const { escapeHtml, formatDate, thumbnailUrl, CategoryBadges, ReviewSection } = window.FXPilotTv;
  const getVideoId = () => {
    const parts = window.location.pathname.split('/').filter(Boolean);
    return parts[0] === 'tv' && parts[1] === 'review' ? decodeURIComponent(parts[2] || '') : '';
  };
  const value = (input, fallback = '—') => (input === undefined || input === null || input === '' ? fallback : input);
  const percent = (input) => input === undefined || input === null || input === '' ? '—' : `${Math.round(Number(input))}%`;
  const statusRu = (status) => status === 'available' ? 'Доступен' : 'Недоступен';
  const directionRu = (direction) => direction === 'BUY' ? 'Покупка' : direction === 'SELL' ? 'Продажа' : value(direction, 'Нет направления');
  const verdictRu = (verdict) => ({
    'FXPilot currently supports this market context.': 'FXPilot сейчас поддерживает этот рыночный контекст.',
    'FXPilot has insufficient data.': 'У FXPilot недостаточно данных.',
    'FXPilot warns that confirmation is weak.': 'FXPilot предупреждает: подтверждение слабое.',
  }[verdict] || verdict || 'Вердикт недоступен.');

  function renderVideoInfo(video) {
    const thumb = thumbnailUrl(video);
    return ReviewSection({ id: 'videoInfo', className: 'tv-review-section--summary', title: 'Video info', content: `
      <div class="tv-intel-video">
        <div class="tv-intel-thumb" style="${thumb ? `background-image:url('${escapeHtml(thumb)}')` : ''}" aria-label="Thumbnail"></div>
        <div class="tv-intel-video__body">
          <div class="tv-detail-top"><div>${CategoryBadges(video)}</div><time datetime="${escapeHtml(video.published_at)}">${escapeHtml(formatDate(video.published_at))}</time></div>
          <h2>${escapeHtml(video.title || 'Видеообзор FXPilot TV')}</h2>
          <p>${escapeHtml(video.description || 'Описание недоступно.')}</p>
          <div class="tv-player-meta-grid tv-review-meta-grid">
            ${[['Длительность', video.duration], ['Категория', video.category], ['Таймфрейм', video.timeframe], ['YouTube ID', video.youtube_id]].map(([label, item]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value(item))}</strong></div>`).join('')}
          </div>
        </div>
      </div>` });
  }

  function renderSource(review) {
    const source = review.author_source || {};
    return ReviewSection({ id: 'authorSource', className: 'tv-review-section--summary', title: 'Author/source', content: `
      <div class="tv-snapshot-grid">
        ${[['Автор', source.author], ['Provider', source.provider], ['Source ID', source.source_id], ['Detected symbol', review.detected_symbol]].map(([label, item]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value(item))}</strong></div>`).join('')}
      </div>` });
  }

  function renderIdea(review) {
    const idea = review.current_fxpilot_idea || {};
    const rows = [
      ['Detected symbol', review.detected_symbol],
      ['Current FXPilot idea for that symbol', idea.symbol],
      ['Direction', directionRu(idea.direction)],
      ['Entry', idea.entry],
      ['SL', idea.sl],
      ['TP', idea.tp],
      ['Confidence', percent(idea.confidence)],
      ['OrderFlow status', `${statusRu(idea.orderflow_status)}${idea.orderflow_bias ? ` · ${idea.orderflow_bias}` : ''}`],
      ['Options status', `${statusRu(idea.options_status)}${idea.options_bias ? ` · ${idea.options_bias}` : ''}`],
      ['News status', idea.news_status || 'neutral'],
      ['Institutional Narrative', idea.institutional_narrative],
    ];
    return ReviewSection({ id: 'fxpilotIdea', className: 'tv-review-section--summary', title: 'Current FXPilot idea', content: `<div class="tv-snapshot-grid tv-review-wide-grid">${rows.map(([label, item]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value(item))}</strong></div>`).join('')}</div><p class="tv-context-note">Контекст построен фактически из /api/ideas/market. Transcript и LLM не используются.</p>` });
  }

  function transcriptMessage(status) {
    if (status === 'WHISPER_REQUIRED') return 'Whisper processing required';
    if (status === 'ERROR') return 'Transcript unavailable';
    return 'Transcript unavailable';
  }

  function transcriptParagraphs(text) {
    return String(text || '').split(/\n{2,}/).map((item) => item.trim()).filter(Boolean).slice(0, 3);
  }

  function renderTranscript(transcript) {
    const paragraphs = transcriptParagraphs(transcript && transcript.text);
    const meta = transcript ? `${value(transcript.provider)} · ${value(transcript.language)} · ${value(transcript.duration ? Math.round(transcript.duration) + ' сек.' : null)}` : '—';
    const body = transcript && transcript.status === 'FOUND' && paragraphs.length
      ? `<div class="tv-transcript-preview">${paragraphs.map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join('')}</div>`
      : `<div class="tv-player-empty">${escapeHtml(transcriptMessage(transcript && transcript.status))}</div>`;
    return ReviewSection({ id: 'transcript', className: 'tv-review-section--summary tv-transcript-section', title: 'Transcript', content: `<div class="tv-context-note">${escapeHtml(meta)}</div>${body}` });
  }


  function list(items) {
    const values = Array.isArray(items) ? items.filter((item) => item !== null && item !== undefined && item !== '') : [];
    return values.length ? values.map((item) => `<span class="tv-analysis-pill">${escapeHtml(item)}</span>`).join('') : '<span class="tv-context-note">—</span>';
  }

  function renderAIAnalysis(review) {
    const analysis = review.analysis || {};
    const rows = [
      ['Symbol', analysis.symbol],
      ['Direction', directionRu(analysis.direction)],
      ['Confidence', percent(analysis.confidence)],
      ['Entry', analysis.entry],
      ['SL', analysis.sl],
      ['TP', analysis.tp],
    ];
    return ReviewSection({ id: 'aiAnalysis', className: 'tv-review-section--summary tv-ai-analysis-section', title: 'AI Analysis', content: `
      <p class="tv-context-note">Rule Engine: без OpenAI/GPT/Gemini/Claude. Провайдер можно заменить без изменения API и Frontend.</p>
      <div class="tv-premium-placeholder"><strong>Summary</strong><p>${escapeHtml(analysis.summary || 'Недостаточно данных транскрипта для резюме.')}</p></div>
      <div class="tv-snapshot-grid tv-review-wide-grid">${rows.map(([label, item]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value(item))}</strong></div>`).join('')}</div>
      <div class="tv-analysis-lists">
        <div><strong>Targets</strong><p>${list(analysis.targets)}</p></div>
        <div><strong>Detected Levels</strong><p>${list(analysis.levels)}</p></div>
        <div><strong>Indicators</strong><p>${list(analysis.indicators)}</p></div>
        <div><strong>Concepts</strong><p>${list(analysis.concepts)}</p></div>
        <div><strong>Risks</strong><p>${list(analysis.risks)}</p></div>
        <div><strong>Opportunities</strong><p>${list(analysis.opportunities)}</p></div>
      </div>` });
  }

  function renderComparison(review) {
    const idea = review.current_fxpilot_idea || {};
    return ReviewSection({ id: 'comparison', className: 'tv-review-section--summary', title: 'Comparison', content: `
      <div class="tv-comparison-grid">
        <article class="tv-premium-placeholder"><strong>Что говорит видео</strong><p>${escapeHtml(review.comparison?.video_says || 'No transcript yet. AI summary will appear later.')}</p></article>
        <article class="tv-premium-placeholder"><strong>Что говорит FXPilot</strong><p>${escapeHtml(`Символ: ${value(idea.symbol)} · направление: ${directionRu(idea.direction)} · вход: ${value(idea.entry)} · SL: ${value(idea.sl)} · TP: ${value(idea.tp)} · confidence: ${percent(idea.confidence)}`)}</p></article>
      </div>` });
  }

  function renderScore(review) {
    return ReviewSection({ id: 'confluenceScore', className: 'tv-review-section--summary tv-verdict-section', title: 'Confluence Score', content: `<div class="tv-confluence-score"><strong>${escapeHtml(value(review.confluence_score, 0))}</strong><span>0-100</span></div><p class="tv-context-note">Скоринг: совпадение символа, наличие направления, OrderFlow, options, нейтральный/позитивный news-фон и confidence.</p>` });
  }

  function renderVerdict(review) {
    return ReviewSection({ id: 'preliminaryVerdict', className: 'tv-review-section--summary tv-verdict-section', title: 'Preliminary verdict', content: `<div class="tv-verdict"><strong>${escapeHtml(verdictRu(review.preliminary_verdict))}</strong><p>Это предварительная проверка рыночного контекста, а не анализ тезисов автора видео.</p></div>` });
  }

  function ReviewPage(review, transcript) {
    const video = review.video || {};
    return `<section class="panel tv-review-watch" data-video-id="${escapeHtml(video.id)}"><a class="tv-back-link" href="/tv">← Вернуться к каталогу FXPilot TV</a><p class="tv-review-slogan">FXPilot TV AI Review v1: фактический контекст без OpenAI, с архитектурой transcript pipeline и локальным кешем.</p></section><div class="tv-review-grid" id="reviewSections">${renderVideoInfo(video)}${renderTranscript(transcript)}${renderAIAnalysis(review)}${renderSource(review)}${renderIdea(review)}${renderComparison(review)}${renderScore(review)}${renderVerdict(review)}</div>`;
  }

  async function loadReview() {
    const response = await fetch(`/api/media/review/${encodeURIComponent(getVideoId())}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error('review_not_found');
    const review = await response.json();
    const transcript = review.transcript || { status: 'NOT_AVAILABLE' };
    root.innerHTML = ReviewPage(review, transcript);
  }

  loadReview().catch(() => {
    root.innerHTML = '<section class="panel"><div class="tv-player-empty">Review не найден. Вернитесь в каталог FXPilot TV и выберите другой обзор.</div></section>';
  });
})();
