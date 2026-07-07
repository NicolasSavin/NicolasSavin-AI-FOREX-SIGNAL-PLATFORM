(function initFxPilotTv() {
  const listEl = document.getElementById('tvVideoList');
  const playerEl = document.getElementById('tvPlayerFrame');
  const detailsEl = document.getElementById('tvSelectedDetails');
  const countEl = document.getElementById('tvVideoCount');
  const sidebarEl = document.querySelector('.tv-sidebar');
  if (!listEl || !playerEl || !detailsEl || !window.FXPilotTv) return;

  const { escapeHtml, formatDate, thumbnailUrl, metaItems, PlayerSkeleton, VideoPlayer, CategoryBadges } = window.FXPilotTv;
  let videos = [];
  let filteredVideos = [];
  let selectedId = null;
  let query = '';
  let category = 'all';
  const historyKey = 'fxpilot-tv-watch-history-v1';
  const readHistory = () => { try { return JSON.parse(localStorage.getItem(historyKey) || '{}'); } catch (_) { return {}; } };
  const saveHistory = (history) => localStorage.setItem(historyKey, JSON.stringify(history));
  let watchHistory = readHistory();

  const todayKey = new Date().toISOString().slice(0, 10);
  const yesterdayDate = new Date();
  yesterdayDate.setDate(yesterdayDate.getDate() - 1);
  const yesterdayKey = yesterdayDate.toISOString().slice(0, 10);

  const byNewest = (a, b) => String(b.published_at || '').localeCompare(String(a.published_at || ''));
  const isSameCategory = (video, name) => String(video.category || '').toLowerCase().includes(name.toLowerCase()) || (video.tags || []).some((tag) => String(tag).toLowerCase().includes(name.toLowerCase()));
  const playlistGroups = [
    { title: 'Today', match: (video) => video.published_at === todayKey },
    { title: 'Yesterday', match: (video) => video.published_at === yesterdayKey },
    { title: 'Forex', match: (video) => isSameCategory(video, 'forex') || /^(EURUSD|GBPUSD|USDJPY|DXY)$/i.test(video.symbol || '') },
    { title: 'Macro', match: (video) => isSameCategory(video, 'macro') },
    { title: 'OrderFlow', match: (video) => isSameCategory(video, 'orderflow') || isSameCategory(video, 'order flow') },
    { title: 'Options', match: (video) => isSameCategory(video, 'options') },
  ];
  const getNextVideo = () => { const index = filteredVideos.findIndex((video) => video.id === selectedId); return filteredVideos[index + 1] || filteredVideos[0] || null; };

  function renderPlayer(video) {
    playerEl.innerHTML = PlayerSkeleton('Загрузка выбранного обзора...');
    window.setTimeout(() => { playerEl.innerHTML = VideoPlayer(video, { autoplay: true }); }, 180);
  }

  function renderHeader(video) {
    if (!video) return '';
    return `
      <div class="tv-premium-player-header">
        <div>
          <p class="section-kicker">Premium Player</p>
          <h3>${escapeHtml(video.title || 'Видеообзор FXPilot TV')}</h3>
          <p>${escapeHtml(video.description || 'Описание недоступно.')}</p>
        </div>
        <div class="tv-player-meta-grid">
          ${metaItems(video).map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('')}
        </div>
      </div>
    `;
  }

  function renderUpNext() {
    const next = getNextVideo();
    if (!next || next.id === selectedId) return '<div class="tv-up-next-card"><p class="section-kicker">Next Video</p><strong>Следующего видео нет</strong><span>Измените фильтр или вернитесь к каталогу.</span></div>';
    return `
      <button class="tv-up-next-card" type="button" data-video-id="${escapeHtml(next.id)}">
        <p class="section-kicker">Next Video</p>
        ${thumbnailUrl(next) ? `<span class="tv-up-next-thumb" style="background-image:url('${thumbnailUrl(next)}')"></span>` : `<span class="tv-up-next-thumb"></span>`}
        <strong>${escapeHtml(next.title)}</strong>
        <span>${escapeHtml(next.category || 'Категория не указана')}</span>
      </button>
    `;
  }

  function renderDetails(video) {
    if (!video) {
      detailsEl.innerHTML = '<p class="section-text">Каталог видео пуст или временно недоступен.</p>';
      return;
    }
    const progress = watchHistory[video.id]?.progress || 0;
    detailsEl.innerHTML = `
      ${renderHeader(video)}
      <div class="tv-detail-top">
        <div>${CategoryBadges(video)}</div>
        <div class="tv-detail-meta"><span class="tv-watch-status">${progress >= 100 ? '✓ Watched' : `Просмотрено ${progress}%`}</span><span class="tv-duration-badge">${escapeHtml(video.duration || '—')}</span><time datetime="${escapeHtml(video.published_at)}">${escapeHtml(formatDate(video.published_at))}</time></div>
      </div>
      <div class="tv-watch-progress" aria-label="Прогресс просмотра"><span style="width:${Math.max(0, Math.min(100, progress))}%"></span></div>
      <p class="tv-author">Автор: ${escapeHtml(video.author)} · ${escapeHtml(video.symbol || 'Инструмент не указан')} · ${escapeHtml(video.category || 'Категория не указана')} · ${escapeHtml(video.timeframe || 'Таймфрейм не указан')}</p>
      <a class="tv-check-button" href="/tv/review/${encodeURIComponent(video.id)}" aria-label="Открыть AI-разбор обзора ${escapeHtml(video.title)}">Проверить обзор</a>
      ${renderUpNext()}
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
      listEl.innerHTML = '<div class="tv-player-empty"><strong>No videos imported</strong><span>Запустите Import Now в Media Admin, чтобы загрузить реальные ролики YouTube.</span></div>';
      return;
    }
    listEl.innerHTML = playlistGroups.map((group) => {
      const items = filteredVideos.filter(group.match);
      if (!items.length) return '';
      return `
      <section class="tv-video-group" aria-label="${escapeHtml(group.title)}">
        <h4>${escapeHtml(group.title)}</h4>
        ${items.map((video) => {
          const progress = watchHistory[video.id]?.progress || 0;
          return `
          <button class="tv-video-item ${video.id === selectedId ? 'is-active' : ''}" type="button" data-video-id="${escapeHtml(video.id)}" aria-pressed="${video.id === selectedId ? 'true' : 'false'}">
            <span class="tv-video-item__meta">${CategoryBadges(video)}<span class="tv-duration-badge">${escapeHtml(video.duration || '—')}</span></span>
            <strong>${escapeHtml(video.title)}</strong>
            <span class="tv-video-item__info"><b>${escapeHtml(video.symbol || '—')}</b><span>${escapeHtml(video.category || 'Без категории')}</span><time datetime="${escapeHtml(video.published_at)}">${escapeHtml(formatDate(video.published_at))}</time></span>
            <span class="tv-mini-progress"><i style="width:${Math.max(0, Math.min(100, progress))}%"></i></span>
            <small>${progress >= 100 ? '✓ Watched' : progress ? `Просмотрено ${progress}%` : `${escapeHtml(video.author)} · не просмотрено`}</small>
          </button>`;
        }).join('')}
      </section>`;
    }).join('');
  }

  function selectVideo(id) {
    const video = videos.find((item) => item.id === id) || filteredVideos[0] || videos[0];
    selectedId = video ? video.id : null;
    if (video) { watchHistory[video.id] = { progress: Math.max(watchHistory[video.id]?.progress || 0, 35), updatedAt: new Date().toISOString() }; saveHistory(watchHistory); }
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
  fetch('/api/media', { headers: { Accept: 'application/json' }, cache: 'no-store' })
    .then((response) => { if (!response.ok) throw new Error('videos_unavailable'); return response.json(); })
    .then((payload) => {
      videos = (Array.isArray(payload) ? payload : []).sort(byNewest);
      filteredVideos = videos;
      renderFilters();
      document.getElementById('tvVideoSearch')?.addEventListener('input', (event) => { query = event.target.value; renderList(); });
      document.getElementById('tvCategoryFilter')?.addEventListener('change', (event) => { category = event.target.value; renderList(); selectVideo(filteredVideos[0]?.id); });
      if (!videos.length) { renderList(); renderPlayer(null); renderDetails(null); return; }
      selectVideo(videos[0] && videos[0].id);
    })
    .catch(() => { videos = []; filteredVideos = []; if (countEl) countEl.textContent = '0 видео'; playerEl.innerHTML = '<div class="tv-player-empty"><strong>Каталог временно недоступен</strong><span>Не удалось загрузить локальный список видео. Попробуйте обновить страницу.</span></div>'; detailsEl.innerHTML = '<p class="section-text">Видеообзоры не загружены. Реальные рыночные данные не подменяются демо-контентом.</p>'; listEl.innerHTML = '<div class="tv-player-empty">Нет доступных видео.</div>'; });
})();
