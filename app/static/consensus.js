const root = document.getElementById('consensusRoot');
const form = document.getElementById('consensusForm');
const symbolInput = document.getElementById('symbolInput');
const timeframeInput = document.getElementById('timeframeInput');
const symbolSelect = symbolInput;
const fmt = (v) => (v === null || v === undefined || v === '' ? '—' : v);
function card(title, value, extra = '') { return `<article class="panel consensus-card"><p class="eyebrow">${title}</p><h2>${value}</h2>${extra}</article>`; }
function render(data) {
  const authors = (data.top_authors || []).map((a) => `<tr><td>${a.author}</td><td>${a.historical_accuracy_label || 'placeholder'}</td><td>${a.current_confidence}%</td><td>${a.committee_score}</td><td>${a.latest_opinion}</td></tr>`).join('');
  const conflicts = (data.disagreements || []).map((x) => `<li>${x}</li>`).join('') || '<li>Явных конфликтов нет</li>';
  root.innerHTML = `
    ${data.empty_message ? `<article class="panel consensus-wide"><strong>${data.empty_message}</strong></article>` : ''}
    ${card('Overall Consensus', data.overall_direction, `<p>Сила: ${data.consensus_strength}</p>`)}
    ${card('Agreement %', `${data.agreement_percent}%`)}
    ${card('Bullish %', `${data.bullish_percent}%`, `<p>${data.bullish_count} мнений</p>`)}
    ${card('Bearish %', `${data.bearish_percent}%`, `<p>${data.bearish_count} мнений</p>`)}
    ${card('Neutral %', `${data.neutral_percent}%`, `<p>${data.neutral_count} мнений</p>`)}
    ${card('Committee Average', fmt(data.average_committee_score), `<p>Средняя уверенность: ${data.average_confidence}%</p>`)}
    <article class="panel consensus-wide"><h2>Top Analysts</h2><table><thead><tr><th>Автор</th><th>Historical accuracy</th><th>Confidence</th><th>Committee</th><th>Latest</th></tr></thead><tbody>${authors || '<tr><td colspan="5">Нет данных</td></tr>'}</tbody></table></article>
    <article class="panel consensus-wide"><h2>Conflicts</h2><ul>${conflicts}</ul><p>${data.market_summary}</p></article>`;
}
async function initSymbols() {
  const res = await fetch('/api/media', { headers: { Accept: 'application/json' }, cache: 'no-store' });
  const payload = await res.json();
  const videos = Array.isArray(payload) ? payload : (Array.isArray(payload.items) ? payload.items : []);
  const symbols = [...new Set(videos.map((v) => (v.symbol || 'MARKET').toString().toUpperCase()).filter(Boolean))].sort();
  symbolSelect.innerHTML = '<option value="MARKET">All / MARKET</option>' + symbols.map((s) => `<option value="${s}">${s}</option>`).join('');
  symbolSelect.value = symbols.includes('MARKET') ? 'MARKET' : (symbols[0] || 'MARKET');
}
async function load() {
  root.innerHTML = '<article class="panel">Загрузка...</article>';
  const symbol = encodeURIComponent(symbolInput.value.trim() || 'MARKET');
  const tf = timeframeInput.value.trim();
  const url = tf ? `/api/consensus/${symbol}/${encodeURIComponent(tf)}` : `/api/consensus/${symbol}`;
  const res = await fetch(url);
  render(await res.json());
}
form.addEventListener('submit', (e) => { e.preventDefault(); load(); });
initSymbols().then(load).catch((error) => { console.error('Consensus init failed:', error); load().catch(() => { root.innerHTML = '<article class="panel">Consensus временно недоступен.</article>'; }); });
