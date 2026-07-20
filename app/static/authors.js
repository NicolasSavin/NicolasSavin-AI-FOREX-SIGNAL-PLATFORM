const root = document.getElementById('authorsRoot');
const sort = document.getElementById('authorsSort');
const esc = (v) => String(v ?? '—').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let authors = [];
function details(a) {
  return `<section class="panel author-detail" id="author-${esc(a.id)}">
    <h2>${esc(a.name)}</h2>
    <p class="muted">Профиль автора: история обзоров, символы, торговые идеи, accuracy, trust evolution и performance. Accuracy/performance — proxy-метрики до подключения реальных market outcomes.</p>
    <div class="author-metrics">
      <span><b>${esc(a.trust_score)}</b>Trust</span><span><b>${esc(a.accuracy_score)}</b>Accuracy*</span><span><b>${esc(a.average_agreement)}</b>Agreement</span><span><b>${esc(a.consensus_alignment)}</b>Consensus</span>
      <span><b>${esc(a.buy_count)}/${esc(a.sell_count)}/${esc(a.wait_count)}</b>BUY/SELL/WAIT</span><span><b>${esc(a.structured_extraction_percent)}%</b>Structured</span>
    </div>
    <p><b>Символы:</b> ${esc((a.symbols || []).join(', ') || 'нет')}</p>
    <p><b>Aliases:</b> ${esc((a.aliases || []).join(', ') || 'нет')}</p>
  </section>`;
}
function table(rows) {
  return `<article class="panel authors-wide"><div class="authors-toolbar"><input id="authorSearch" placeholder="Поиск автора, символа или статуса" /></div><div class="authors-table-wrap"><table class="authors-table"><thead><tr><th>Name</th><th>Trust</th><th>Accuracy</th><th>Reviews</th><th>Trade Ideas</th><th>Symbols</th><th>Activity</th><th>Quality</th><th>Last Review</th><th>Status</th></tr></thead><tbody>${rows.map(a => `<tr data-author="${esc(a.id)}"><td><a href="#author-${esc(a.id)}">${esc(a.name || a.author)}</a></td><td>${esc(a.trust_score ?? a.rating)}</td><td>${esc(a.accuracy_score ?? a.accuracy)}*</td><td>${esc(a.review_count ?? a.videos)}</td><td>${esc(a.trade_idea_count ?? a.signals)}</td><td>${esc(a.symbol_count)}</td><td>${esc(a.activity_score)}</td><td>${esc(a.quality_score)}</td><td>${esc(a.last_review)}</td><td>${esc(a.status || a.tier)}</td></tr>`).join('')}</tbody></table></div></article>`;
}
function render(rows) {
  root.innerHTML = rows.length ? `${table(rows)}${rows.slice(0, 12).map(details).join('')}` : '<article class="panel">Авторы пока не найдены.</article>';
  const search = document.getElementById('authorSearch');
  search?.addEventListener('input', () => {
    const q = search.value.toLowerCase();
    render(authors.filter(a => JSON.stringify([a.name, a.author, a.symbols, a.status]).toLowerCase().includes(q)));
  }, { once: true });
}
async function load() {
  root.innerHTML = '<article class="panel">Загрузка...</article>';
  const res = await fetch(`/api/authors?sort=${encodeURIComponent(sort.value)}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
  authors = await res.json();
  render(Array.isArray(authors) ? authors : []);
}
sort.addEventListener('change', load);
load().catch(() => { root.innerHTML = '<article class="panel">Author Intelligence временно недоступен.</article>'; });
