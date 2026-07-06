(function initFxPilotReviewPage() {
  const root = document.getElementById('tvReviewPage');
  if (!root || !window.FXPilotTv) return;

  const { escapeHtml, formatDate, VideoPlayer, CategoryBadges, ReviewSection } = window.FXPilotTv;

  const getVideoId = () => {
    const parts = window.location.pathname.split('/').filter(Boolean);
    return parts[0] === 'tv' && parts[1] === 'review' ? decodeURIComponent(parts[2] || '') : '';
  };

  function ReviewPage(payload) {
    const video = payload.video || {};
    const review = payload.review || {};
    const player = VideoPlayer(video, { autoplay: false, titleFallback: 'FXPilot TV Review' });
    const conclusions = Array.isArray(review.main_conclusions) ? review.main_conclusions : [];

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
          <p class="tv-author" id="reviewAuthor">Автор: ${escapeHtml(video.author || 'Не указан')}</p>
          <p id="reviewDescription">${escapeHtml(video.description || 'Описание будет добавлено позже.')}</p>
        </article>
      </section>
      <div class="tv-review-grid" id="reviewSections">
        ${ReviewSection({ id: 'aiSummary', className: 'tv-review-section--summary', title: 'AI Summary', content: `<p id="aiSummaryText">${escapeHtml(review.ai_summary)}</p>` })}
        ${ReviewSection({ id: 'fxpilotOpinion', className: 'tv-review-section--opinion', title: 'FXPilot Opinion', content: `<p id="fxpilotOpinionText">${escapeHtml(review.fxpilot_opinion)}</p>` })}
        ${ReviewSection({ id: 'agreementScore', className: 'tv-review-section--score', title: 'Agreement Score', content: `<div class="tv-review-score" id="agreementScoreValue">${escapeHtml(review.agreement_score ?? 0)}%</div><p>Placeholder-процент совпадения с будущей логикой FXPilot.</p>` })}
        ${ReviewSection({ id: 'mainConclusions', className: 'tv-review-section--conclusions', title: 'Main Conclusions', content: `<ul id="mainConclusionsList">${conclusions.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` })}
        ${ReviewSection({ id: 'realityCheck', className: 'tv-review-section--coming', title: 'Reality Check', content: '<p id="realityCheckStatus" class="tv-coming-soon">Coming Soon</p>' })}
        ${ReviewSection({ id: 'trustScore', className: 'tv-review-section--coming', title: 'Trust Score', content: '<p id="trustScoreStatus" class="tv-coming-soon">Coming Soon</p>' })}
      </div>
    `;
  }

  const videoId = getVideoId();
  fetch(`/api/tv/review/${encodeURIComponent(videoId)}`, { headers: { Accept: 'application/json' }, cache: 'no-store' })
    .then((response) => {
      if (!response.ok) throw new Error('review_not_found');
      return response.json();
    })
    .then((payload) => { root.innerHTML = ReviewPage(payload); })
    .catch(() => { root.innerHTML = '<section class="panel"><div class="tv-player-empty">Review не найден. Вернитесь в каталог FXPilot TV и выберите другой обзор.</div></section>'; });
})();
