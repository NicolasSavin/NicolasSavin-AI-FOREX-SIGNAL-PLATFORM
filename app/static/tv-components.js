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

  const isValidYouTubeId = (youtubeId) => /^[A-Za-z0-9_-]{11}$/.test(String(youtubeId || ''));

  const embedUrl = (youtubeId) => `https://www.youtube.com/embed/${encodeURIComponent(youtubeId)}`;

  const PlayerSkeleton = (label = 'Загрузка видео...') => `
    <div class="tv-player-skeleton" aria-live="polite">
      <span></span><strong>${escapeHtml(label)}</strong><small>Подготавливаем YouTube-плеер</small>
    </div>
  `;

  const VideoPlayer = (video, { autoplay = false, titleFallback = 'FXPilot TV' } = {}) => {
    if (!video || !isValidYouTubeId(video.youtube_id)) return '<div class="tv-player-empty">Каталог пока пуст. Запустите Import Now.</div>';
    return `<iframe src="${embedUrl(video.youtube_id, { autoplay })}" title="${escapeHtml(video.title || titleFallback)}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen loading="lazy"></iframe>`;
  };

  const thumbnailUrl = (video = {}) => video.youtube_id ? `https://i.ytimg.com/vi/${encodeURIComponent(video.youtube_id)}/hqdefault.jpg` : '';

  const metaItems = (video = {}) => [
    ['Автор', video.author],
    ['Символ', video.symbol],
    ['Категория', video.category],
    ['Таймфрейм', video.timeframe],
    ['Длительность', video.duration],
    ['Дата публикации', formatDate(video.published_at)],
  ].filter(([, value]) => value);

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

  return { escapeHtml, formatDate, thumbnailUrl, metaItems, PlayerSkeleton, VideoPlayer, CategoryBadges, ReviewSection };
})();
