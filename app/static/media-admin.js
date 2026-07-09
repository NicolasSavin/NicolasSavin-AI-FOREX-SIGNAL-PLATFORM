(function initMediaAdmin() {
  const sourcesBody = document.getElementById('mediaSourcesBody');
  const newestBody = document.getElementById('mediaNewestBody');
  const importButton = document.getElementById('mediaImportNow');
  const schedulerState = document.getElementById('mediaSchedulerState');
  const importResult = document.getElementById('mediaImportResult');
  const sourceForm = document.getElementById('mediaSourceForm');
  const resolveResult = document.getElementById('mediaResolveResult');
  if (!sourcesBody || !newestBody) return;
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value || '—'; };
  const formatValue = (value) => value ? escapeHtml(value) : '—';
  const showImportResult = (payload) => { if (importResult) importResult.textContent = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2); };

  const formPayload = () => { const fd = new FormData(sourceForm); return { id: fd.get('id'), name: fd.get('name'), source_type: fd.get('source_type') || 'youtube', provider: fd.get('provider') || 'youtube_api', url: fd.get('channel_url'), channel_url: fd.get('channel_url'), language: fd.get('language') || 'ru', categories: String(fd.get('categories') || '').split(',').map((v) => v.trim()).filter(Boolean), symbols: String(fd.get('symbols') || '').split(',').map((v) => v.trim()).filter(Boolean), priority: Number(fd.get('priority') || 1), enabled: Boolean(fd.get('enabled')) }; };
  const showResolve = (payload) => { if (resolveResult) resolveResult.textContent = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2); };
  function implementationNotice(action, sourceName) { window.alert(`${action}: Implementation in next sprint${sourceName ? ` для ${sourceName}` : ''}.`); }
  const providerLabel = (provider) => provider === 'youtube_ytdlp' ? 'YouTube (yt-dlp)' : (provider === 'youtube_api' ? 'YouTube API' : provider);
  const statusLabel = (source) => { if (source.provider === 'youtube_ytdlp' && source.status === 'online') return 'Online'; if (source.status === 'manual_source' || source.provider === 'youtube_manual') return 'Manual source — API not connected'; if (source.status === 'needs_channel_id' || (source.provider === 'youtube' && !source.channel_id)) return 'Нужен YouTube channel_id для RSS-импорта'; return source.enabled ? 'Online' : 'Disabled'; };
  const sourceDuration = (source) => source.last_run?.execution_time ? `${source.last_run.execution_time}s` : (source.import_duration ? `${source.import_duration}s` : '—');
  const sourceErrors = (source) => source.last_error || (source.last_run?.errors || []).filter(Boolean).join('; ') || '—';

  function renderSources(sources) {
    setText('mediaStatSources', String(sources.length));
    if (!sources.length) { sourcesBody.innerHTML = '<tr><td colspan="8">Источники не настроены.</td></tr>'; return; }
    sourcesBody.innerHTML = sources.map((source) => {
      const label = statusLabel(source);
      const statusClass = source.enabled && !source.last_error && label !== 'Manual source — API not connected' ? 'is-enabled' : 'is-disabled';
      return `
      <tr>
        <td><strong>${escapeHtml(source.name)}</strong><span>${escapeHtml(source.url || source.channel_url || '')}</span></td>
        <td>${escapeHtml(source.source_type || 'youtube')}</td>
        <td><span class="tv-provider-pill">${escapeHtml(providerLabel(source.provider))}</span></td>
        <td><span class="tv-status-pill ${statusClass}">${escapeHtml(source.enabled ? 'Enabled' : 'Disabled')}</span></td>
        <td>${formatValue(source.last_success || source.last_import)}</td>
        <td>${escapeHtml(source.items_count ?? source.videos_count ?? 0)}</td>
        <td>${escapeHtml(sourceErrors(source))}</td>
        <td><div class="tv-source-actions"><button data-action="Test" data-id="${escapeHtml(source.id)}">Health</button><button data-action="Disable" data-id="${escapeHtml(source.id)}">Disable</button><button data-action="Enable" data-id="${escapeHtml(source.id)}">Enable</button><button data-action="Delete" data-id="${escapeHtml(source.id)}">Delete</button></div></td>
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

  function renderAutomation(stats, scheduler) {
    setText('mediaStatToday', String(stats.imported_today ?? 0));
    setText('mediaStatWeek', String(stats.imported_this_week ?? 0));
    setText('mediaStatOnline', String(stats.sources_online ?? 0));
    setText('mediaStatFailed', String(stats.sources_failed ?? 0));
    setText('mediaStatAnalyzed', String(stats.videos_analyzed ?? 0));
    setText('mediaStatAwaiting', String(stats.videos_awaiting_ai ?? 0));
    setText('mediaStatAvgDuration', `${stats.average_import_duration ?? 0}s`);
    setText('mediaStatProviders', Object.entries(stats.provider_usage || {}).map(([k, v]) => `${k}:${v}`).join(' · ') || '—');
    if (schedulerState) schedulerState.textContent = JSON.stringify({ scheduler: scheduler.status, jobs: scheduler.jobs, notifications: scheduler.notifications, queue: scheduler.state?.last_payload, retry_policy_minutes: scheduler.retry_policy_minutes }, null, 2);
  }

  function refresh() {
    Promise.all([
      fetch('/api/media/sources', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then((r) => r.ok ? r.json() : []),
      fetch('/api/media', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then((r) => r.ok ? r.json() : []),
      fetch('/api/media/stats', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then((r) => r.ok ? r.json() : {}),
      fetch('/api/media/scheduler', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then((r) => r.ok ? r.json() : {}),
    ]).then(([sources, media, stats, scheduler]) => { renderSources(Array.isArray(sources) ? sources : []); renderMedia(Array.isArray(media) ? media : []); renderAutomation(stats || {}, scheduler || {}); })
      .catch(() => { sourcesBody.innerHTML = '<tr><td colspan="8">Media Engine временно недоступен.</td></tr>'; newestBody.innerHTML = '<tr><td colspan="5">Каталог временно недоступен.</td></tr>'; });
  }

  sourcesBody.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const id = button.dataset.id;
    if (button.dataset.action === 'Import Source') { fetch(`/api/media/sources/${id}/import`, { method: 'POST', headers: { Accept: 'application/json' } }).then((r) => r.json()).then((p) => { showImportResult(p); refresh(); }); return; }
    if (button.dataset.action === 'Test') { fetch(`/api/media/sources/${id}/test`, { method: 'POST', headers: { Accept: 'application/json' } }).then((r) => r.json()).then(showImportResult); return; }
    if (button.dataset.action === 'Delete') { fetch(`/api/media/sources/${id}`, { method: 'DELETE', headers: { Accept: 'application/json' } }).then((r) => r.json()).then((p) => { showImportResult(p); refresh(); }); return; }
    if (button.dataset.action === 'Disable' || button.dataset.action === 'Enable') { fetch(`/api/media/sources/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify({ enabled: button.dataset.action === 'Enable' }) }).then((r) => r.json()).then((p) => { showImportResult(p); refresh(); }); return; }
  });

  function runImport() {
    setText('mediaImportStatus', 'Import running...');
    showImportResult('Import running...');
    fetch('/api/media/import-all', { method: 'POST', headers: { Accept: 'application/json' } })
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
