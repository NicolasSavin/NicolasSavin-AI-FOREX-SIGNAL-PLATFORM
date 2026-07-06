(function initFxPilotTv() {
  const listEl = document.getElementById('tvVideoList');
  const playerEl = document.getElementById('tvPlayerFrame');
  const detailsEl = document.getElementById('tvSelectedDetails');
  const countEl = document.getElementById('tvVideoCount');
  const sidebarEl = document.querySelector('.tv-sidebar');
  if (!listEl || !playerEl || !detailsEl || !window.FXPilotTv) return;

  const { escapeHtml, formatDate, PlayerSkeleton, VideoPlayer, CategoryBadges } = window.FXPilotTv;
  let videos = [];
  let filteredVideos = [];
  let selectedId = null;
  let query = '';
  let category = 'all';

  const todayKey = new Date().toISOString().slice(0, 10);
  const yesterdayDate = new Date();
  yesterdayDate.setDate(yesterdayDate.getDate() - 1);
  const yesterdayKey = yesterdayDate.toISOString().slice(0, 10);

  const byNewest = (a, b) => String(b.published_at || '').localeCompare(String(a.published_at || ''));
  const groupTitle = (value) => value === todayKey ? 'Сегодня' : value === yesterdayKey ? 'Вчера' : 'Архив';
  const groupOrder = (value) => value === todayKey ? 0 : value === yesterdayKey ? 1 : 2;

  function renderPlayer(video) {
    playerEl.innerHTML = PlayerSkeleton('Загрузка выбранного обзора...');
    window.setTimeout(() => { playerEl.innerHTML = VideoPlayer(video, { autoplay: true }); }, 180);
  }

  function renderDetails(video) {
    if (!video) {
      detailsEl.innerHTML = '<p class="section-text">Каталог видео пуст или временно недоступен.</p>';
      return;
    }
    detailsEl.innerHTML = `
      <div class="tv-detail-top">
        <div>${CategoryBadges(video)}</div>
        <div class="tv-detail-meta"><span class="tv-duration-badge">${escapeHtml(video.duration || '—')}</span><time datetime="${escapeHtml(video.published_at)}">${escapeHtml(formatDate(video.published_at))}</time></div>
      </div>
      <h3>${escapeHtml(video.title)}</h3>
      <p class="tv-author">Автор: ${escapeHtml(video.author)}</p>
      <p>${escapeHtml(video.description)}</p>
      <a class="tv-check-button" href="/tv/review/${encodeURIComponent(video.id)}" aria-label="Открыть AI-разбор обзора ${escapeHtml(video.title)}">Проверить обзор</a>
    `;
  }

  function renderFilters() {
    const categories = [...new Set(videos.map((video) => video.category).filter(Boolean))].sort();
    const filters = `
      <div class="tv-sidebar-controls">
        <label class="sr-only" for="tvVideoSearch">Поиск видео</label>
        <input id="tvVideoSearch" type="search" value="${escapeHtml(query)}" placeholder="Поиск по названию, автору, инструменту..." autocomplete="off" />
        <label class="sr-only" for="tvCategoryFilter">Категория</label>
        <select id="tvCategoryFilter">
          <option value="all">Все категории</option>
          ${categories.map((item) => `<option value="${escapeHtml(item)}" ${item === category ? 'selected' : ''}>${escapeHtml(item)}</option>`).join('')}
        </select>
        <span class="tv-sort-note">Сортировка: сначала новые</span>
      </div>
    `;
    if (sidebarEl && !sidebarEl.querySelector('.tv-sidebar-controls')) {
      sidebarEl.querySelector('.tv-sidebar__head')?.insertAdjacentHTML('afterend', filters);
    }
  }

  function applyFilters() {
    const normalized = query.trim().toLowerCase();
    filteredVideos = videos
      .filter((video) => category === 'all' || video.category === category)
      .filter((video) => !normalized || [video.title, video.author, video.symbol, video.category, video.description].join(' ').toLowerCase().includes(normalized))
      .sort(byNewest);
  }

  function renderList() {
    applyFilters();
    if (countEl) countEl.textContent = `${filteredVideos.length} из ${videos.length} видео`;
    if (!filteredVideos.length) {
      listEl.innerHTML = '<div class="tv-player-empty">По выбранным фильтрам видео не найдены.</div>';
      return;
    }
    const groups = filteredVideos.reduce((acc, video) => {
      const key = groupTitle(video.published_at);
      acc[key] = acc[key] || [];
      acc[key].push(video);
      return acc;
    }, {});
    listEl.innerHTML = Object.entries(groups).sort(([a], [b]) => groupOrder(a === 'Сегодня' ? todayKey : a === 'Вчера' ? yesterdayKey : '') - groupOrder(b === 'Сегодня' ? todayKey : b === 'Вчера' ? yesterdayKey : '')).map(([title, items]) => `
      <section class="tv-video-group" aria-label="${escapeHtml(title)}">
        <h4>${escapeHtml(title)}</h4>
        ${items.map((video) => `
          <button class="tv-video-item ${video.id === selectedId ? 'is-active' : ''}" type="button" data-video-id="${escapeHtml(video.id)}" aria-pressed="${video.id === selectedId ? 'true' : 'false'}">
            <span class="tv-video-item__meta">${CategoryBadges(video)}<span class="tv-duration-badge">${escapeHtml(video.duration || '—')}</span></span>
            <strong>${escapeHtml(video.title)}</strong>
            <small>${escapeHtml(video.author)} · ${escapeHtml(formatDate(video.published_at))}</small>
          </button>
        `).join('')}
      </section>
    `).join('');
  }

  function selectVideo(id) {
    const video = videos.find((item) => item.id === id) || filteredVideos[0] || videos[0];
    selectedId = video ? video.id : null;
    renderPlayer(video);
    renderDetails(video);
    renderList();
    listEl.querySelector('.tv-video-item.is-active')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  listEl.addEventListener('click', (event) => {
    const button = event.target.closest('[data-video-id]');
    if (button) selectVideo(button.getAttribute('data-video-id'));
  });

  playerEl.innerHTML = PlayerSkeleton();
  fetch('/api/tv/videos', { headers: { Accept: 'application/json' }, cache: 'no-store' })
    .then((response) => response.ok ? response.json() : [])
    .then((payload) => {
      videos = (Array.isArray(payload) ? payload : []).sort(byNewest);
      filteredVideos = videos;
      renderFilters();
      document.getElementById('tvVideoSearch')?.addEventListener('input', (event) => { query = event.target.value; renderList(); });
      document.getElementById('tvCategoryFilter')?.addEventListener('change', (event) => { category = event.target.value; renderList(); selectVideo(filteredVideos[0]?.id); });
      selectVideo(videos[0] && videos[0].id);
    })
    .catch(() => { videos = []; selectVideo(null); });
})();
