(() => {
  const TOKEN_KEY = 'fxpilot_ops_token';
  const $ = (id) => document.getElementById(id);
  const tokenInput = $('opsToken');
  const consoleBox = $('opsConsole');
  let statusPayload = null;
  let storagePayload = null;

  function getToken() { return window.sessionStorage.getItem(TOKEN_KEY) || ''; }
  function headers() { return { Accept: 'application/json', 'Content-Type': 'application/json', 'X-FXPILOT-OPS-TOKEN': getToken() }; }
  function setBusy(button, busy) { button.disabled = busy; button.dataset.originalText ||= button.textContent; button.textContent = busy ? '⏳ Выполняется…' : button.dataset.originalText; }
  function logResult(entry) {
    const node = document.createElement('article'); node.className = 'ops-log-entry';
    node.innerHTML = `<strong>${entry.name}</strong><span>${entry.started} → ${entry.finished} · ${entry.duration} ms · HTTP ${entry.httpStatus} · ${entry.ok ? 'success' : 'failure'}</span><pre></pre>`;
    node.querySelector('pre').textContent = JSON.stringify(entry.payload, null, 2);
    consoleBox.prepend(node);
  }
  async function loadStatus() {
    const r = await fetch('/api/ops/status', { headers: { Accept: 'application/json' }, cache: 'no-store' });
    statusPayload = await r.json(); renderStatus(statusPayload); renderStorageFromStatus(statusPayload);
  }
  function reviewStructuredCount(p) {
    return p.reviews?.structured ?? p.reviews?.structured_reviews ?? p.structured_reviews ?? p.reviews_structured;
  }
  function renderStatus(p) {
    const cards = [
      ['Media catalog', p.media?.catalog_items], ['Sources', p.media?.sources], ['Reviews', p.reviews?.total], ['Structured reviews', reviewStructuredCount(p)],
      ['LLM provider/model', `${p.llm?.provider || '—'} / ${p.llm?.model || '—'}`], ['Scheduler', p.scheduler?.running ? 'running' : 'stopped'],
      ['Pipeline', `run:${p.pipeline?.running || 0} ok:${p.pipeline?.completed || 0} fail:${p.pipeline?.failed || 0}`], ['Knowledge Graph', `symbols:${p.knowledge_graph?.symbols ?? 0} reviews:${p.knowledge_graph?.reviews_indexed ?? 0} conflicts:${p.knowledge_graph?.conflicts ?? 0}`], ['KG build time', `${p.knowledge_graph?.build_time_ms ?? 0} ms`], ['Last import', p.media?.last_import || '—'], ['Last error', '—']
    ];
    $('statusCards').innerHTML = cards.map(([label, value]) => `<article class="stats-page-card"><span>${label}</span><strong>${value ?? '—'}</strong></article>`).join('');
  }
  function renderStorageFromStatus(p) {
    const s = p.storage || {}; const r = p.reviews || {}; const kg = p.knowledge_graph || {};
    const warning = s.message || s.warning?.message || '—';
    const cards = [
      ['Storage mode', s.mode || '—'], ['Status', s.status || (s.healthy ? 'healthy' : '—')], ['Persistent storage configured', s.data_root_source === 'FXPILOT_DATA_DIR' ? 'Да' : 'Нет'],
      ['Media catalog items', s.media_items ?? p.media?.catalog_items ?? 0], ['Review files', s.review_files ?? r.storage_files ?? 0], ['Valid reviews', r.total ?? 0],
      ['Malformed reviews', kg.malformed_reviews ?? 0], ['Transcript files', p.transcripts?.files ?? '—'], ['Runtime instance', s.instance_id || '—'], ['Process start time', s.process_started_at || '—'], ['Warning', warning],
    ];
    const el = $('storageCards');
    if (el) el.innerHTML = cards.map(([label, value]) => `<article class="stats-page-card ${s.code === 'ephemeral_storage_risk' ? 'warning' : ''}"><span>${label}</span><strong>${value ?? '—'}</strong></article>`).join('');
  }
  function renderStorageDiagnostics(p) {
    const s = p.storage || {}; const reviews = p.llm_reviews || {}; const transcripts = p.transcripts || {};
    const cards = [
      ['Storage mode', s.mode || '—'], ['Status', s.status || '—'], ['Persistent storage configured', s.configured ? 'Да' : 'Нет'],
      ['Media catalog items', p.media_catalog?.items ?? 0], ['TV catalog items', p.tv_catalog?.items ?? 0], ['Review files', reviews.json_files ?? 0],
      ['Valid reviews', reviews.valid_reviews ?? 0], ['Malformed reviews', reviews.malformed_reviews ?? 0], ['Transcript files', transcripts.files ?? 0],
      ['Runtime instance', p.runtime?.instance_id || '—'], ['Process start time', p.runtime?.process_started_at || '—'], ['Warning', s.message || '—'],
    ];
    const el = $('storageCards');
    if (el) el.innerHTML = cards.map(([label, value]) => `<article class="stats-page-card ${s.code === 'ephemeral_storage_risk' ? 'warning' : ''}"><span>${label}</span><strong>${value ?? '—'}</strong></article>`).join('');
  }
  async function loadStorage(button) {
    if (button) setBusy(button, true);
    try { const r = await fetch('/api/ops/storage', { headers: headers(), cache: 'no-store' }); storagePayload = await r.json(); renderStorageDiagnostics(storagePayload); logResult({ name: 'Диагностика хранилища', started: new Date().toLocaleString('ru-RU'), finished: new Date().toLocaleString('ru-RU'), duration: 0, httpStatus: r.status, ok: r.ok, payload: storagePayload }); }
    finally { if (button) setBusy(button, false); }
  }
  function confirmCostly(name, count) {
    const model = statusPayload?.llm?.model || 'модель не определена';
    return window.confirm(`${name}\nВидео: ${count}\nМодель: ${model}\nОперация использует платную LLM-модель и может расходовать баланс OpenRouter.`);
  }
  async function postOp(name, url, params = {}, costly = false, button) {
    if (costly && !confirmCostly(name, params.limit || params.video_id || 1)) return;
    const startedAt = new Date(); const started = startedAt.toLocaleString('ru-RU'); setBusy(button, true);
    const qs = new URLSearchParams(params); const requestUrl = qs.toString() ? `${url}?${qs}` : url;
    let payload = null; let httpStatus = 0; let ok = false;
    try { const r = await fetch(requestUrl, { method: 'POST', headers: headers(), body: '{}' }); httpStatus = r.status; ok = r.ok; payload = await r.json().catch(() => ({})); }
    catch (e) { payload = { success: false, error: e.message }; }
    finally { setBusy(button, false); }
    const finishedAt = new Date(); logResult({ name, started, finished: finishedAt.toLocaleString('ru-RU'), duration: finishedAt - startedAt, httpStatus, ok, payload });
    loadAudit().catch(() => {});
    loadStatus().catch(() => {});
  }
  async function loadAudit() {
    const r = await fetch('/api/ops/audit?limit=50', { headers: headers(), cache: 'no-store' });
    $('opsAudit').textContent = JSON.stringify(await r.json(), null, 2);
  }

  tokenInput.value = getToken() ? '••••••••' : '';
  $('saveToken').addEventListener('click', () => { if (tokenInput.value && tokenInput.value !== '••••••••') window.sessionStorage.setItem(TOKEN_KEY, tokenInput.value); tokenInput.value = '••••••••'; });
  $('clearToken').addEventListener('click', () => { window.sessionStorage.removeItem(TOKEN_KEY); tokenInput.value = ''; });
  $('refreshStatus').addEventListener('click', loadStatus);
  $('refreshAudit').addEventListener('click', () => loadAudit().catch((e) => { $('opsAudit').textContent = e.message; }));
  document.querySelector('[data-op="storage-diagnostics"]').addEventListener('click', (e) => loadStorage(e.currentTarget));
  document.querySelector('[data-op="storage-migrate-dry"]').addEventListener('click', (e) => postOp('Проверить миграцию хранилища', '/api/ops/storage/migrate', { dry_run: true, execute: false }, false, e.currentTarget));
  document.querySelector('[data-op="storage-migrate-exec"]').addEventListener('click', (e) => { if (window.confirm('Выполнить безопасную миграцию известных файлов в настроенное хранилище?')) postOp('Выполнить миграцию хранилища', '/api/ops/storage/migrate', { dry_run: false, execute: true }, false, e.currentTarget); });
  document.querySelector('[data-op="media-import"]').addEventListener('click', (e) => postOp('Импортировать новые материалы', '/api/ops/media/import', {}, false, e.currentTarget));
  document.querySelector('[data-op="review-reprocess"]').addEventListener('click', (e) => postOp('Перегенерировать AI Reviews', '/api/ops/reviews/reprocess', { force: $('reviewForce').checked, limit: Math.min(20, Number($('reviewLimit').value || 1)) }, true, e.currentTarget));
  document.querySelector('[data-op="pipeline-run"]').addEventListener('click', (e) => postOp('Запустить pipeline для видео', '/api/ops/pipeline/run', { video_id: $('pipelineVideoId').value }, true, e.currentTarget));
  document.querySelector('[data-op="pipeline-run-all"]').addEventListener('click', (e) => postOp('Запустить pipeline для необработанных', '/api/ops/pipeline/run-all', { limit: Math.min(20, Number($('pipelineLimit').value || 1)) }, true, e.currentTarget));
  document.querySelector('[data-op="consensus-rebuild"]').addEventListener('click', (e) => postOp('Пересчитать Consensus', '/api/ops/consensus/rebuild', { symbol: $('consensusSymbol').value, timeframe: $('consensusTimeframe').value }, false, e.currentTarget));
  document.querySelector('[data-op="authors-rebuild"]').addEventListener('click', (e) => postOp('Пересчитать авторов', '/api/ops/authors/rebuild', {}, false, e.currentTarget));
  document.querySelector('[data-op="performance-rebuild"]').addEventListener('click', (e) => postOp('Пересчитать Performance', '/api/ops/performance/rebuild', {}, false, e.currentTarget));
  document.querySelector('[data-op="cache-clear"]').addEventListener('click', (e) => { if (window.confirm('Очистить только безопасные application caches?')) postOp('Очистить безопасные кэши', '/api/ops/cache/clear', {}, false, e.currentTarget); });
  loadStatus().catch(() => {});
})();
