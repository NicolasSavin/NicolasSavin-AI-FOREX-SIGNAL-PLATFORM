(function initMediaAdmin() {
  const sourcesBody = document.getElementById('mediaSourcesBody');
  const newestBody = document.getElementById('mediaNewestBody');
  const importButton = document.getElementById('mediaImportNow');
  const importResult = document.getElementById('mediaImportResult');
  const sourceForm = document.getElementById('mediaSourceForm');
  const resolveResult = document.getElementById('mediaResolveResult');
  if (!sourcesBody || !newestBody) return;
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value || '—'; };
  const formatValue = (value) => value ? escapeHtml(value) : '—';
  const showImportResult = (payload) => { if (importResult) importResult.textContent = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2); };

  const formPayload = () => { const fd = new FormData(sourceForm); return { id: fd.get('id'), name: fd.get('name'), provider: fd.get('provider') || 'youtube', channel_url: fd.get('channel_url'), language: fd.get('language') || 'ru', categories: String(fd.get('categories') || '').split(',').map((v) => v.trim()).filter(Boolean), priority: Number(fd.get('priority') || 1), enabled: Boolean(fd.get('enabled')) }; };
  const showResolve = (payload) => { if (resolveResult) resolveResult.textContent = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2); };
  function implementationNotice(action, sourceName) { window.alert(`${action}: Implementation in next sprint${sourceName ? ` для ${sourceName}` : ''}.`); }

  function renderSources(sources) {
    setText('mediaStatSources', String(sources.length));
    if (!sources.length) { sourcesBody.innerHTML = '<tr><td colspan="6">Источники не настроены.</td></tr>'; return; }
    sourcesBody.innerHTML = sources.map((source) => {
      const isManualSource = source.status === 'manual_source' || source.provider === 'youtube_manual';
      const needsChannelId = source.status === 'needs_channel_id' || (source.provider === 'youtube' && !source.channel_id);
      const statusLabel = isManualSource ? 'Manual source — API not connected' : (needsChannelId ? 'Нужен YouTube channel_id для RSS-импорта' : (source.enabled ? 'Enabled' : 'Disabled'));
      const statusClass = (needsChannelId || isManualSource) ? 'is-disabled' : (source.enabled ? 'is-enabled' : 'is-disabled');
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

  sourcesBody.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    if (button.dataset.action === 'Import Now') { runImport(); return; }
    implementationNotice(button.dataset.action, button.dataset.source);
  });

  function runImport() {
    setText('mediaImportStatus', 'Import running...');
    showImportResult('Import running...');
    fetch('/api/media/import', { method: 'POST', headers: { Accept: 'application/json' } })
      .then((response) => response.json().catch(() => ({ success: false, errors: [{ reason: 'Invalid JSON response' }] })).then((payload) => {
        if (!response.ok) throw payload;
        return payload;
      }))
      .then((payload) => {
        showImportResult(payload);
        setText('mediaImportStatus', payload.success ? `Imported: ${payload.imported ?? payload.new_items ?? 0} new · catalog: ${payload.catalog_size ?? '—'}` : `Import failed: ${(payload.errors || []).map((e) => e.reason || e).join('; ') || 'unknown error'}`);
        return refresh();
      })
      .catch((error) => {
        showImportResult(error);
        setText('mediaImportStatus', `Import failed: ${(error && (error.detail || error.message)) || 'request error'}`);
      });
  }

  importButton?.addEventListener('click', runImport);
  document.getElementById('mediaImportNowForm')?.addEventListener('click', runImport);
  document.getElementById('mediaResolveSource')?.addEventListener('click', () => {
    const payload = formPayload();
    showResolve('Resolving...');
    fetch('/api/media/resolve-source', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify({ provider: payload.provider, channel_url: payload.channel_url }) })
      .then((r) => r.json().then((data) => { if (!r.ok) throw data; return data; }))
      .then(showResolve).catch(showResolve);
  });
  sourceForm?.addEventListener('submit', (event) => {
    event.preventDefault();
    showResolve('Saving...');
    fetch('/api/media/sources', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(formPayload()) })
      .then((r) => r.json().then((data) => { if (!r.ok) throw data; return data; }))
      .then((payload) => { showResolve(payload); sourceForm.reset(); return refresh(); }).catch(showResolve);
  });
  document.getElementById('mediaResolveAll')?.addEventListener('click', () => {
    showResolve('Re-resolving all YouTube sources...');
    fetch('/api/media/resolve-all', { method: 'POST', headers: { Accept: 'application/json' } })
      .then((r) => r.json().then((data) => { if (!r.ok) throw data; return data; }))
      .then((payload) => { showResolve(payload); return refresh(); }).catch(showResolve);
  });
  refresh();
})();
