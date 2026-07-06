(function initTvSourcesAdmin() {
  const bodyEl = document.getElementById('tvSourcesBody');
  if (!bodyEl) return;

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  const formatValue = (value) => value ? escapeHtml(value) : '—';
  const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value || '—'; };

  function showDevelopmentNotice(action, sourceName) {
    window.alert(`${action}: функция в разработке для источника ${sourceName}.`);
  }

  function renderStats(stats) {
    setText('tvStatSources', String(stats.sources ?? 0));
    setText('tvStatVideos', String(stats.videos ?? 0));
    setText('tvStatLastUpdate', stats.last_update || '—');
    setText('tvStatNewestVideo', stats.newest_video || '—');
  }

  function renderSources(sources) {
    if (!sources.length) {
      bodyEl.innerHTML = '<tr><td colspan="7">Источники не настроены.</td></tr>';
      return;
    }
    bodyEl.innerHTML = sources.map((source) => `
      <tr>
        <td><strong>${escapeHtml(source.name)}</strong><span>${escapeHtml((source.categories || []).join(' · '))}</span></td>
        <td><span class="tv-provider-pill">${escapeHtml(source.provider)}</span></td>
        <td>${escapeHtml(source.priority)}</td>
        <td><span class="tv-status-pill ${source.enabled ? 'is-enabled' : 'is-disabled'}">${source.enabled ? 'Enabled' : 'Disabled'}</span></td>
        <td>${formatValue(source.last_import)}</td>
        <td>${escapeHtml(source.videos_count ?? 0)}</td>
        <td>
          <div class="tv-source-actions">
            <button type="button" data-action="Import now" data-source="${escapeHtml(source.name)}">Import now</button>
            <button type="button" data-action="Disable" data-source="${escapeHtml(source.name)}">Disable</button>
            <button type="button" data-action="Enable" data-source="${escapeHtml(source.name)}">Enable</button>
          </div>
        </td>
      </tr>
    `).join('');
  }

  bodyEl.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    showDevelopmentNotice(button.dataset.action, button.dataset.source);
  });

  Promise.all([
    fetch('/api/tv/sources', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then((response) => { if (!response.ok) throw new Error('sources_unavailable'); return response.json(); }),
    fetch('/api/tv/sources/stats', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then((response) => response.ok ? response.json() : { stats: {} }),
  ])
    .then(([sources, statsPayload]) => { renderStats(statsPayload.stats || {}); renderSources(Array.isArray(sources) ? sources : []); })
    .catch(() => { renderStats({}); bodyEl.innerHTML = '<tr><td colspan="7">Source Manager временно недоступен.</td></tr>'; });
})();
