(function initFxPilotTv() {
  const listEl = document.getElementById('tvVideoList');
  const playerEl = document.getElementById('tvPlayerFrame');
  const detailsEl = document.getElementById('tvSelectedDetails');
  const countEl = document.getElementById('tvVideoCount');
  if (!listEl || !playerEl || !detailsEl) return;

  let videos = [];
  let selectedId = null;

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[char]));

  const formatDate = (value) => {
    if (!value) return 'Дата не указана';
    const date = new Date(`${value}T00:00:00Z`);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' }).format(date);
  };

  function renderPlayer(video) {
    if (!video || !video.youtube_id) {
      playerEl.innerHTML = '<div class="tv-player-empty">Видео пока недоступно.</div>';
      return;
    }
    const src = `https://www.youtube.com/embed/${encodeURIComponent(video.youtube_id)}`;
    playerEl.innerHTML = `<iframe src="${src}" title="${escapeHtml(video.title)}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen loading="lazy"></iframe>`;
  }

  function renderDetails(video) {
    if (!video) {
      detailsEl.innerHTML = '<p class="section-text">Каталог видео пуст или временно недоступен.</p>';
      return;
    }
    detailsEl.innerHTML = `
      <div class="tv-detail-top">
        <div><span class="tv-category-badge">${escapeHtml(video.category)}</span><span class="tv-symbol-badge">${escapeHtml(video.symbol)}</span></div>
        <time datetime="${escapeHtml(video.published_at)}">${escapeHtml(formatDate(video.published_at))}</time>
      </div>
      <h3>${escapeHtml(video.title)}</h3>
      <p class="tv-author">Автор: ${escapeHtml(video.author)}</p>
      <p>${escapeHtml(video.description)}</p>
      <button class="tv-check-button" type="button" disabled title="AI-проверка будет добавлена позже">Проверить обзор · скоро</button>
    `;
  }

  function renderList() {
    if (countEl) countEl.textContent = `${videos.length} видео`;
    if (!videos.length) {
      listEl.innerHTML = '<div class="tv-player-empty">Каталог видео пуст.</div>';
      return;
    }
    listEl.innerHTML = videos.map((video) => `
      <button class="tv-video-item ${video.id === selectedId ? 'is-active' : ''}" type="button" data-video-id="${escapeHtml(video.id)}">
        <span class="tv-video-item__meta"><span>${escapeHtml(video.category)}</span><span>${escapeHtml(video.symbol)}</span></span>
        <strong>${escapeHtml(video.title)}</strong>
        <small>${escapeHtml(video.author)} · ${escapeHtml(formatDate(video.published_at))}</small>
      </button>
    `).join('');
  }

  function selectVideo(id) {
    const video = videos.find((item) => item.id === id) || videos[0];
    selectedId = video ? video.id : null;
    renderPlayer(video);
    renderDetails(video);
    renderList();
  }

  listEl.addEventListener('click', (event) => {
    const button = event.target.closest('[data-video-id]');
    if (button) selectVideo(button.getAttribute('data-video-id'));
  });

  fetch('/api/tv/videos', { headers: { Accept: 'application/json' }, cache: 'no-store' })
    .then((response) => response.ok ? response.json() : [])
    .then((payload) => {
      videos = Array.isArray(payload) ? payload : [];
      selectVideo(videos[0] && videos[0].id);
    })
    .catch(() => {
      videos = [];
      selectVideo(null);
    });
})();
