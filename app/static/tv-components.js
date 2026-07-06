window.FXPilotTv = (() => {
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[char]));

  const formatDate = (value) => {
    if (!value) return 'Дата не указана';
    const date = new Date(`${value}T00:00:00Z`);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' }).format(date);
  };

  const embedUrl = (youtubeId, { autoplay = false } = {}) => {
    const params = new URLSearchParams({ rel: '0', modestbranding: '1', playsinline: '1' });
    if (autoplay) params.set('autoplay', '1');
    return `https://www.youtube.com/embed/${encodeURIComponent(youtubeId)}?${params.toString()}`;
  };

  const PlayerSkeleton = (label = 'Загрузка видео...') => `
    <div class="tv-player-skeleton" aria-live="polite">
      <span></span><strong>${escapeHtml(label)}</strong><small>Подготавливаем YouTube-плеер</small>
    </div>
  `;

  const VideoPlayer = (video, { autoplay = false, titleFallback = 'FXPilot TV' } = {}) => {
    if (!video || !video.youtube_id) return '<div class="tv-player-empty">Видео пока недоступно.</div>';
    return `<iframe src="${embedUrl(video.youtube_id, { autoplay })}" title="${escapeHtml(video.title || titleFallback)}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen loading="lazy"></iframe>`;
  };

  const CategoryBadges = (video = {}) => [
    video.category && `<span class="tv-category-badge">${escapeHtml(video.category)}</span>`,
    video.symbol && `<span class="tv-symbol-badge">${escapeHtml(video.symbol)}</span>`,
    video.timeframe && `<span class="tv-timeframe-badge">${escapeHtml(video.timeframe)}</span>`,
  ].filter(Boolean).join('');

  const ReviewSection = ({ id, className = '', title, content }) => `
    <section id="${escapeHtml(id)}" class="tv-review-section ${escapeHtml(className)}" data-review-section="${escapeHtml(id)}" aria-labelledby="${escapeHtml(id)}Title">
      <div class="tv-review-section__head"><p class="section-kicker">Review Module</p><h2 id="${escapeHtml(id)}Title">${escapeHtml(title)}</h2></div>
      <div class="tv-review-section__body">${content}</div>
    </section>
  `;

  return { escapeHtml, formatDate, PlayerSkeleton, VideoPlayer, CategoryBadges, ReviewSection };
})();
