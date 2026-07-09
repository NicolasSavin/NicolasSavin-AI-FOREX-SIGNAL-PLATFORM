const root = document.getElementById('authorsRoot');
const sort = document.getElementById('authorsSort');
const esc = (v) => String(v ?? '—').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function row(a) {
  return `<article class="panel author-card">
    <div><p class="eyebrow">${esc(a.tier)}</p><h2>${esc(a.author)}</h2><p>${esc(a.report?.summary)}</p></div>
    <div class="author-metrics">
      <span><b>${esc(a.rating)}</b>Rating</span><span><b>${esc(a.accuracy)}%</b>Accuracy*</span><span><b>${esc(a.average_committee_score)}</b>Committee</span><span><b>${esc(a.latest_opinion)}</b>Latest</span>
    </div>
    <a class="author-link" href="/api/authors/${encodeURIComponent(a.author)}" target="_blank" rel="noopener">Полный JSON отчёт</a>
  </article>`;
}
async function load() {
  root.innerHTML = '<article class="panel">Загрузка...</article>';
  const res = await fetch(`/api/authors?sort=${encodeURIComponent(sort.value)}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
  const data = await res.json();
  root.innerHTML = `<article class="panel authors-wide"><h2>Leaderboard</h2><p class="muted">*Accuracy — proxy metric, не реальный market win rate.</p></article>${(Array.isArray(data) && data.length ? data.map(row).join('') : '<article class="panel">Авторы пока не найдены.</article>')}`;
}
sort.addEventListener('change', load);
load().catch(() => { root.innerHTML = '<article class="panel">Author Intelligence временно недоступен.</article>'; });
