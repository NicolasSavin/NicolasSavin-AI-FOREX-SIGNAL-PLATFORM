(function initMediaAdmin() {
  const sourcesBody = document.getElementById('mediaSourcesBody');
  const newestBody = document.getElementById('mediaNewestBody');
  const importButton = document.getElementById('mediaImportNow');
  if (!sourcesBody || !newestBody) return;
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value || '—'; };
  const formatValue = (value) => value ? escapeHtml(value) : '—';

  function implementationNotice(action, sourceName) { window.alert(`${action}: Implementation in next sprint${sourceName ? ` для ${sourceName}` : ''}.`); }

  function renderSources(sources) {
    setText('mediaStatSources', String(sources.length));
    if (!sources.length) { sourcesBody.innerHTML = '<tr><td colspan="6">Источники не настроены.</td></tr>'; return; }
    sourcesBody.innerHTML = sources.map((source) => {
      const needsChannelId = source.status === 'needs_channel_id' || (source.provider === 'youtube' && !source.channel_id);
      const statusLabel = needsChannelId ? 'Нужен YouTube channel_id для RSS-импорта' : (source.enabled ? 'Enabled' : 'Disabled');
      const statusClass = needsChannelId ? 'is-disabled' : (source.enabled ? 'is-enabled' : 'is-disabled');
      return `
      <tr>
        <td><strong>${escapeHtml(source.name)}</strong><span>${escapeHtml(source.channel_url || '')}</span></td>
        <td><span class="tv-provider-pill">${escapeHtml(source.provider)}</span></td>
        <td><span class="tv-status-pill ${statusClass}">${escapeHtml(statusLabel)}</span></td>
        <td>${formatValue(source.last_import)}</td>
        <td>${escapeHtml(source.videos_count ?? 0)}</td>
        <td><div class="tv-source-actions"><button data-action="Import Now" data-source="${escapeHtml(source.name)}">Import Now</button><button data-action="Disable" data-source="${escapeHtml(source.name)}">Disable</button><button data-action="Enable" data-source="${escapeHtml(source.name)}">Enable</button></div></td>
      </tr>`;
    }).join('');
  }

  function renderMedia(items) {
    setText('mediaStatVideos', String(items.length));
    const newest = items[0] || {};
    setText('mediaStatNewest', newest.title || '—');
    setText('mediaStatLastUpdate', newest.published_at || newest.imported_at || '—');
    newestBody.innerHTML = items.slice(0, 12).map((item) => `
      <tr><td><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.author || item.source_id || '')}</span></td><td>${escapeHtml(item.symbol || 'MARKET')}</td><td>${escapeHtml(item.category || '—')}</td><td>${escapeHtml(item.published_at || '—')}</td><td><span class="tv-status-pill is-enabled">${escapeHtml(item.status || 'imported')}</span></td></tr>
    `).join('') || '<tr><td colspan="5">Каталог пуст.</td></tr>';
  }

  function refresh() {
    Promise.all([
      fetch('/api/media/sources', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then((r) => r.ok ? r.json() : []),
      fetch('/api/media', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then((r) => r.ok ? r.json() : []),
    ]).then(([sources, media]) => { renderSources(Array.isArray(sources) ? sources : []); renderMedia(Array.isArray(media) ? media : []); })
      .catch(() => { sourcesBody.innerHTML = '<tr><td colspan="6">Media Engine временно недоступен.</td></tr>'; newestBody.innerHTML = '<tr><td colspan="5">Каталог временно недоступен.</td></tr>'; });
  }

  sourcesBody.addEventListener('click', (event) => { const button = event.target.closest('button[data-action]'); if (button) implementationNotice(button.dataset.action, button.dataset.source); });
  importButton?.addEventListener('click', () => {
    setText('mediaImportStatus', 'Import running...');
    fetch('/api/media/import', { method: 'POST', headers: { Accept: 'application/json' } }).then((r) => r.json()).then((payload) => {
      setText('mediaImportStatus', payload.success ? `Imported: ${payload.new_items} new` : 'Import failed'); refresh();
    }).catch(() => setText('mediaImportStatus', 'Import failed'));
  });
  refresh();
})();
