(function () {
  const root = document.getElementById('committeeRoot');
  const select = document.getElementById('committeeVideoSelect');
  const esc = (v) => String(v ?? '—').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
  const list = (items, empty) => `<ul class="committee-list">${(Array.isArray(items) && items.length ? items : [empty]).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
  function card(label, value, cls) { return `<article class="committee-card ${cls || ''}"><span>${esc(label)}</span><strong>${esc(value)}</strong></article>`; }
  function render(report) {
    root.innerHTML = `
      <section class="panel tv-review-section--summary committee-hero-card">
        <p class="section-kicker">${esc(report.video?.title || 'FXPilot TV')}</p>
        <h2>${esc(report.summary)}</h2>
      </section>
      <section class="committee-score-grid">
        ${card('Overall Score', `${report.overall_score}/100`, 'score')}
        ${card('Decision', report.decision, String(report.decision || '').toLowerCase())}
        ${card('Risk', report.risk_level, String(report.risk_level || '').toLowerCase())}
        ${card('Agreement', `${report.agreement_score}%`, 'agreement')}
        ${card('Institutional Bias', report.institutional_bias, String(report.institutional_bias || '').toLowerCase())}
        ${card('Committee Verdict', report.committee_verdict, String(report.committee_verdict || '').toLowerCase())}
      </section>
      <section class="panel"><h2>Pros</h2>${list(report.pros, 'Нет сильных подтверждений')}</section>
      <section class="panel"><h2>Cons</h2>${list(report.cons, 'Существенные минусы не выявлены')}</section>
      <section class="panel"><h2>Conflicts</h2>${list(report.conflicts, 'Конфликты между слоями не обнаружены')}</section>
      <section class="panel"><h2>Provider Architecture</h2><p class="tv-context-note">Provider: ${esc(report.provider)}. Контракт готов для OpenAI, Gemini, Claude, DeepSeek, OpenRouter и Local LLM без изменения frontend.</p></section>`;
  }
  async function loadReport(id) {
    root.innerHTML = '<article class="panel">Комитет анализирует видео...</article>';
    const res = await fetch(`/api/media/committee/${encodeURIComponent(id)}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!res.ok) throw new Error('committee request failed');
    render(await res.json());
  }
  async function init() {
    try {
      const media = await fetch('/api/media', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then((r) => r.json());
      const items = Array.isArray(media) ? media : [];
      select.innerHTML = items.map((v) => `<option value="${esc(v.id)}">${esc(v.title || v.id)}</option>`).join('') || '<option>Видео не найдены</option>';
      if (items[0]?.id) await loadReport(items[0].id);
    } catch (e) {
      root.innerHTML = '<article class="panel">Не удалось загрузить Investment Committee.</article>';
    }
  }
  select?.addEventListener('change', () => select.value && loadReport(select.value));
  init();
})();
