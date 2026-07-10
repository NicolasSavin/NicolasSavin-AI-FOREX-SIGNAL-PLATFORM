(function initFxPilotTv() {
  const listEl = document.getElementById('tvVideoList');
  const playerEl = document.getElementById('tvPlayerFrame');
  const detailsEl = document.getElementById('tvSelectedDetails');
  const countEl = document.getElementById('tvVideoCount');
  const filtersEl = document.getElementById('tvCatalogFilters');
  const retryEl = document.getElementById('tvRetryButton');
  if (!listEl || !playerEl || !detailsEl || !window.FXPilotTv) return;
  const tv = window.FXPilotTv;
  let videos = [], visible = [], selectedId = null;
  const state = { q: '', category: 'all', symbol: 'all', direction: 'all', timeframe: 'all', review: 'all', sort: 'newest' };
  const debounce = (fn, ms = 220) => { let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); }; };
  const val = (v, fallback = 'Не определено') => (v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)) ? fallback : (Array.isArray(v) ? v.join(', ') : String(v));
  const options = (field) => [...new Set(videos.flatMap((v) => Array.isArray(v[field]) ? v[field] : [v[field]]).filter(Boolean))].sort();
  const reviewText = (v) => v.review_status === 'ready' ? (v.ai_summary || 'AI summary пока не заполнен') : tv.reviewLabel(v.review_status);
  const youtubeUrl = (v) => `https://www.youtube.com/watch?v=${encodeURIComponent(v.youtube_id || '')}`;
  const reviewUrl = (v) => `/tv/review/${encodeURIComponent(v.id || v.youtube_id || '')}`;
  const committeeUrl = (v) => `/committee/${encodeURIComponent(v.id || v.youtube_id || '')}`;

  function renderFilters() {
    if (!filtersEl) return;
    filtersEl.innerHTML = `<div class="tv-filter-grid">
      <label>Поиск<input id="tvSearch" type="search" placeholder="Название, автор, символ" autocomplete="off"></label>
      <label>Категория<select id="tvCategory"><option value="all">Все категории</option>${options('category').map(x=>`<option>${tv.escapeHtml(x)}</option>`).join('')}</select></label>
      <label>Символ<select id="tvSymbol"><option value="all">Все символы</option>${options('symbols').map(x=>`<option>${tv.escapeHtml(x)}</option>`).join('')}</select></label>
      <label>Направление<select id="tvDirection"><option value="all">Все</option>${['BUY','SELL','WAIT','NEUTRAL'].map(x=>`<option>${x}</option>`).join('')}</select></label>
      <label>Таймфрейм<select id="tvTimeframe"><option value="all">Все</option>${options('timeframe').map(x=>`<option>${tv.escapeHtml(x)}</option>`).join('')}</select></label>
      <label>Статус<select id="tvReview"><option value="all">Все статусы</option><option value="ready">Review ready</option><option value="processing">Анализ выполняется</option><option value="missing">Анализ ещё не создан</option><option value="failed">Ошибка анализа</option></select></label>
      <label>Сортировка<select id="tvSort"><option value="newest">Сначала новые</option><option value="oldest">Сначала старые</option><option value="confidence">Высокий confidence</option><option value="author">Автор</option><option value="symbol">Символ</option></select></label>
      <button id="tvResetFilters" type="button">Сбросить фильтры <span id="tvActiveFilters">0</span></button>
    </div>`;
    const bind = (id, key) => document.getElementById(id)?.addEventListener('change', e => { state[key] = e.target.value; render(); });
    document.getElementById('tvSearch')?.addEventListener('input', debounce(e => { state.q = e.target.value; render(); }));
    bind('tvCategory','category'); bind('tvSymbol','symbol'); bind('tvDirection','direction'); bind('tvTimeframe','timeframe'); bind('tvReview','review'); bind('tvSort','sort');
    document.getElementById('tvResetFilters')?.addEventListener('click', () => { Object.assign(state, { q:'', category:'all', symbol:'all', direction:'all', timeframe:'all', review:'all', sort:'newest' }); renderFilters(); render(); });
  }

  function applyFilters() {
    const q = state.q.trim().toLowerCase();
    visible = videos.filter(v =>
      (!q || [v.title, v.author, v.channel, v.primary_symbol, ...(v.symbols || [])].join(' ').toLowerCase().includes(q)) &&
      (state.category === 'all' || v.category === state.category) &&
      (state.symbol === 'all' || (v.symbols || []).includes(state.symbol) || v.primary_symbol === state.symbol) &&
      (state.direction === 'all' || v.direction === state.direction) &&
      (state.timeframe === 'all' || v.timeframe === state.timeframe) &&
      (state.review === 'all' || v.review_status === state.review)
    );
    const sorters = { newest:(a,b)=>String(b.published_at||'').localeCompare(String(a.published_at||'')), oldest:(a,b)=>String(a.published_at||'').localeCompare(String(b.published_at||'')), confidence:(a,b)=>(Number(b.confidence)||0)-(Number(a.confidence)||0), author:(a,b)=>String(a.author||'').localeCompare(String(b.author||''),'ru'), symbol:(a,b)=>String(a.primary_symbol||'').localeCompare(String(b.primary_symbol||'')) };
    visible.sort(sorters[state.sort] || sorters.newest);
    const active = Object.entries(state).filter(([k,v]) => k !== 'sort' && v && v !== 'all').length;
    const activeEl = document.getElementById('tvActiveFilters'); if (activeEl) activeEl.textContent = String(active);
  }

  function renderCard(v) {
    const thumb = tv.thumbnailUrl(v);
    return `<button class="tv-video-item tv-catalog-card ${v.id === selectedId ? 'is-active' : ''}" type="button" data-video-id="${tv.escapeHtml(v.id)}" aria-pressed="${v.id === selectedId}">
      <span class="tv-thumb-wrap">${thumb ? `<img src="${tv.escapeHtml(thumb)}" alt="Превью: ${tv.escapeHtml(v.title)}" loading="lazy">` : '<span>Нет превью</span>'}</span>
      <span class="tv-video-item__meta">${tv.badge(v.category,'category')}${tv.signalBadges(v)}${tv.badge(tv.formatDuration(v.duration),'duration')}<span class="${tv.reviewClass(v.review_status)}">${v.review_status === 'processing' ? '<i></i>' : ''}${tv.escapeHtml(tv.reviewLabel(v.review_status))}</span></span>
      <strong>${tv.escapeHtml(v.title || 'Без названия')}</strong>
      <span class="tv-video-item__info"><b>${tv.escapeHtml(v.author || v.channel || 'Автор не указан')}</b><time>${tv.escapeHtml(tv.formatDate(v.published_at))}</time></span>
      <small class="tv-card-summary">${tv.escapeHtml(reviewText(v))}</small>
    </button>`;
  }

  function renderDetails(v) {
    if (!v) { detailsEl.innerHTML = '<div class="tv-player-empty">Каталог пока пуст. Импортируйте материалы через Operations. <a href="/ops">Открыть /ops</a></div>'; return; }
    const reviewReady = v.review_status === 'ready';
    detailsEl.innerHTML = `<div class="tv-premium-player-header"><div><p class="section-kicker">Выбранный обзор</p><h3>${tv.escapeHtml(v.title)}</h3><p>${tv.escapeHtml(v.author || v.channel || 'Автор не указан')} · ${tv.escapeHtml(tv.formatDate(v.published_at))}</p></div><div class="tv-player-meta-grid"><div><span>Категория</span><strong>${tv.escapeHtml(val(v.category))}</strong></div><div><span>Длительность</span><strong>${tv.escapeHtml(val(tv.formatDuration(v.duration)))}</strong></div><div><span>Символы</span><strong>${tv.escapeHtml(val(v.symbols))}</strong></div><div><span>Confidence</span><strong>${Number(v.confidence) ? Number(v.confidence)+'%' : 'Не определено'}</strong></div></div></div>
    <div class="tv-detail-top"><div>${tv.signalBadges(v)}<span class="${tv.reviewClass(v.review_status)}">${tv.escapeHtml(tv.reviewLabel(v.review_status))}</span></div></div>
    <p class="tv-ai-summary-full">${tv.escapeHtml(reviewText(v))}</p>
    <div class="tv-trade-levels"><span>Entry: <b>${tv.escapeHtml(val(v.entry))}</b></span><span>Entry zone: <b>${tv.escapeHtml(val(v.entry_zone))}</b></span><span>Stop loss: <b>${tv.escapeHtml(val(v.stop_loss))}</b></span><span>Targets: <b>${tv.escapeHtml(val(v.targets))}</b></span></div>
    <div class="tv-detail-actions"><a class="tv-check-button" target="_blank" rel="noopener" href="${youtubeUrl(v)}">Смотреть на YouTube</a><a class="tv-check-button ${reviewReady ? '' : 'is-disabled'}" ${reviewReady ? `href="${reviewUrl(v)}"` : 'aria-disabled="true"'}>Открыть AI Review</a>${reviewReady ? `<a class="tv-check-button" href="${committeeUrl(v)}">Открыть Committee</a>` : ''}</div>`;
  }

  function selectVideo(id) { const old = selectedId; const v = visible.find(x => x.id === id) || videos.find(x => x.id === id) || visible[0] || null; selectedId = v?.id || null; if (v && old !== selectedId) playerEl.innerHTML = tv.VideoPlayer(v); renderDetails(v); renderList(); }
  function renderList() { if (!visible.length) { listEl.innerHTML = videos.length ? '<div class="tv-player-empty">По выбранным фильтрам ничего не найдено.</div>' : '<div class="tv-player-empty">Каталог пока пуст. Импортируйте материалы через Operations. <a href="/ops">Открыть /ops</a></div>'; return; } listEl.innerHTML = visible.map(renderCard).join(''); }
  function render() { applyFilters(); if (countEl) countEl.textContent = `${visible.length} из ${videos.length} видео`; if (!visible.some(v => v.id === selectedId)) selectedId = visible[0]?.id || null; renderList(); renderDetails(visible.find(v => v.id === selectedId)); if (selectedId) playerEl.innerHTML = tv.VideoPlayer(visible.find(v => v.id === selectedId)); }
  async function loadCatalog() { playerEl.innerHTML = tv.PlayerSkeleton('Загрузка каталога FXPilot TV…'); listEl.innerHTML = '<div class="tv-player-empty">Загрузка каталога FXPilot TV…</div>'; const r = await fetch('/api/media/catalog', { headers:{Accept:'application/json'}, cache:'no-store' }); if (!r.ok) throw new Error('catalog_unavailable'); const payload = await r.json(); videos = Array.isArray(payload.items) ? payload.items.filter(v => v.youtube_id) : []; renderFilters(); selectedId = selectedId && videos.some(v=>v.id===selectedId) ? selectedId : videos[0]?.id; render(); }
  listEl.addEventListener('click', e => { const b = e.target.closest('[data-video-id]'); if (b) selectVideo(b.dataset.videoId); });
  retryEl?.addEventListener('click', () => loadCatalog().catch(showError));
  function showError() { if (countEl) countEl.textContent = '0 видео'; playerEl.innerHTML = '<div class="tv-player-empty"><strong>Не удалось загрузить каталог.</strong><button type="button" id="tvInlineRetry">Повторить</button></div>'; detailsEl.innerHTML = '<p class="section-text">Проверьте доступность API каталога.</p>'; listEl.innerHTML = '<div class="tv-player-empty">Не удалось загрузить каталог. Повторить</div>'; document.getElementById('tvInlineRetry')?.addEventListener('click', () => loadCatalog().catch(showError)); }
  loadCatalog().catch(showError);
})();
