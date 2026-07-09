const root = document.getElementById('performanceRoot');
const board = document.getElementById('leaderboard');
const esc = (v) => String(v ?? '—').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = (v) => Number.isFinite(Number(v)) ? `${Number(v).toFixed(1)}%` : '—';
function metric(title, rows) {
  return `<article class="panel author-card"><p class="eyebrow">${esc(title)}</p>${(rows||[]).slice(0,5).map(r=>`<p><b>${esc(r.author)}</b> · win ${pct(r.win_rate)} · RR ${esc(r.average_rr)} · hold ${esc(r.average_holding_time)}h</p>`).join('') || '<p>Нет завершённых результатов.</p>'}</article>`;
}
function card(x) {
  return `<article class="panel signal-card author-card">
    <p class="eyebrow">${esc(x.symbol)} · ${esc(x.author)} · ${esc(x.status)}</p><h2>${esc(x.video_id)}</h2>
    <div class="author-metrics"><span><b>${esc(x.result)}</b>Result</span><span><b>${esc(x.rr)}</b>RR</span><span><b>${esc(x.max_profit)}</b>Profit/MFE</span><span><b>${esc(x.max_drawdown)}</b>Loss/MAE</span></div>
    <p><b>Prediction:</b> ${esc(x.direction)} entry ${esc(x.entry_price)} / SL ${esc(x.stop_loss)} / TP ${esc(x.take_profit)}</p>
    <p><b>Reality:</b> high ${esc(x.market_high)} / low ${esc(x.market_low)} / provider ${esc(x.provider)} (${esc(x.data_status)})</p>
    <p><b>Difference:</b> ${esc(x.difference ? JSON.stringify(x.difference) : x.warning_ru)}</p>
  </article>`;
}
async function load() {
  const res = await fetch('/api/performance', {headers:{Accept:'application/json'}, cache:'no-store'});
  const data = await res.json();
  const lb = data.leaderboard || {};
  board.innerHTML = metric('Best Authors', lb.best_authors) + metric('Worst Authors', lb.worst_authors) + metric('Most Accurate', lb.most_accurate) + metric('Most Profitable', lb.most_profitable);
  root.innerHTML = (data.items || []).map(card).join('') || '<article class="panel">Видео пока не найдены.</article>';
}
load().catch(() => { root.innerHTML = '<article class="panel">Performance Engine временно недоступен.</article>'; });
