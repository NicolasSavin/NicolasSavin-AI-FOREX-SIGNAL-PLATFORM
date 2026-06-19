const rows = document.getElementById('archiveRows');
const filters = document.getElementById('archiveFilters');
const meta = document.getElementById('archiveMeta');
const modal = document.getElementById('archiveModal');
const modalBody = document.getElementById('archiveModalBody');
const symbols = ['Все', 'EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD'];
let items = [];
let selectedSymbol = 'Все';

const esc = (value) => String(value ?? '—').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const number = (value) => value == null || Number.isNaN(Number(value)) ? '—' : Number(value).toFixed(5).replace(/0+$/, '').replace(/\.$/, '');
const idea = (item) => item.idea && typeof item.idea === 'object' ? item.idea : item;
const value = (item, ...keys) => {
  const source = idea(item);
  for (const key of keys) if (item[key] != null) return item[key]; else if (source[key] != null) return source[key];
  return null;
};
const status = (item) => {
  const result = String(item.result || '').toUpperCase();
  if (result === 'TP' || item.status === 'tp_hit') return { code: 'TP', label: 'TP ✅', className: 'tp' };
  if (result === 'SL' || item.status === 'sl_hit') return { code: 'SL', label: 'SL ❌', className: 'sl' };
  return { code: 'ACTIVE', label: 'ACTIVE ⏳', className: 'active' };
};
const date = (raw) => {
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short', timeZone: 'UTC' }) + ' UTC';
};
const rr = (item) => {
  const entry = Number(value(item, 'entry', 'entry_price'));
  const sl = Number(value(item, 'sl', 'stop_loss', 'final_sl'));
  const tp = Number(value(item, 'tp', 'take_profit', 'final_tp'));
  return [entry, sl, tp].every(Number.isFinite) && entry !== sl ? `1:${(Math.abs(tp - entry) / Math.abs(entry - sl)).toFixed(2)}` : '—';
};
function chart(item) {
  const source = idea(item);
  const payload = source.candles || source.chartData || source.chart_data || [];
  const candles = Array.isArray(payload) ? payload : Array.isArray(payload.candles) ? payload.candles : [];
  if (!candles.length) return '<div class="archive-chart-empty">График недоступен: свечи не сохранены в архивной идее.</div>';
  const sample = candles.slice(-50);
  const values = sample.flatMap((c) => [c.open, c.high, c.low, c.close].map(Number)).filter(Number.isFinite);
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
  const width = 760, height = 260, pad = 18, step = (width - pad * 2) / sample.length;
  const y = (price) => pad + ((max - Number(price)) / range) * (height - pad * 2);
  const bars = sample.map((c, index) => {
    const x = pad + index * step + step / 2, up = Number(c.close) >= Number(c.open), color = up ? '#22c55e' : '#ef4444';
    return `<line x1="${x}" y1="${y(c.high)}" x2="${x}" y2="${y(c.low)}" stroke="${color}"/><rect x="${x - Math.max(2, step * .28)}" y="${Math.min(y(c.open), y(c.close))}" width="${Math.max(4, step * .56)}" height="${Math.max(2, Math.abs(y(c.open) - y(c.close)))}" fill="${color}" rx="1"/>`;
  }).join('');
  return `<div class="archive-chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="График архивной идеи">${bars}</svg></div>`;
}
function render() {
  filters.innerHTML = symbols.map((symbol) => `<button type="button" class="archive-filter ${selectedSymbol === symbol ? 'is-active' : ''}" data-symbol="${symbol}">${symbol}</button>`).join('');
  const visible = selectedSymbol === 'Все' ? items : items.filter((item) => String(value(item, 'symbol', 'pair', 'instrument') || '').replace('/', '').toUpperCase() === selectedSymbol);
  rows.innerHTML = visible.length ? visible.map((item, index) => {
    const result = status(item);
    return `<tr tabindex="0" data-index="${items.indexOf(item)}"><td>${esc(date(value(item, 'created_at_utc', 'created_at', 'signal_time_utc')))}</td><td><strong>${esc(value(item, 'symbol', 'pair', 'instrument'))}</strong></td><td><span class="archive-direction archive-direction--${String(value(item, 'action', 'signal', 'direction') || '').toLowerCase()}">${esc(value(item, 'action', 'signal', 'direction'))}</span></td><td>${number(value(item, 'entry', 'entry_price'))}</td><td>${number(value(item, 'sl', 'stop_loss', 'final_sl'))}</td><td>${number(value(item, 'tp', 'take_profit', 'final_tp'))}</td><td><span class="archive-result archive-result--${result.className}">${result.label}</span></td></tr>`;
  }).join('') : '<tr><td colspan="7" class="archive-empty">Для выбранного инструмента идей пока нет.</td></tr>';
  meta.textContent = `Показано: ${visible.length} · Всего: ${items.length}`;
}

function archiveLessons(item, result) {
  const saved = value(item, 'lessons_learned', 'lessonsLearned');
  if (saved) return saved;
  if (result.code === 'TP') {
    return 'Lessons Learned: сценарий сработал, потому что после сбора целевой ликвидности рынок подтвердил намерение крупного участника: не вернулся против displacement, удержал рабочую зону и доставил цену к TP. Собранная ликвидность и реакция от зоны показали, что движение было не случайным описанием графика, а доставкой цены к цели.';
  }
  if (result.code === 'SL') {
    return 'Lessons Learned: первоначальная гипотеза оказалась неверной. Крупный участник не защитил исходную зону, появилась новая ликвидность против идеи, а принятие цены за invalidation нарушило логику sweep → mitigation → delivery. Идея потеряла актуальность после смены поведения order flow.';
  }
  return 'Lessons Learned появится после закрытия сигнала по TP или SL.';
}

function openItem(item) {
  const source = idea(item), result = status(item);
  const fullText = value(item, 'unified_narrative', 'full_text', 'description_ru', 'narrative', 'summary_ru') || 'Полный текст идеи не был сохранён.';
  const reason = value(item, 'entry_reason_ru', 'reason_ru', 'entry_reason', 'setup_reason', 'why_entry') || 'Причина входа не указана.';
  const thesis = value(item, 'institutional_thesis', 'institutionalThesis') || 'Institutional Thesis не сохранён.';
  const sourceLabel = value(item, 'narrative_source') || 'fallback';
  const lessons = archiveLessons(item, result);
  modalBody.innerHTML = `<p class="section-kicker">${esc(value(item, 'symbol', 'pair', 'instrument'))} · ${esc(result.label)}</p><h2 id="archiveModalTitle">Архивная торговая идея</h2><div class="archive-modal__metrics"><div><span>Score</span><strong>${esc(value(item, 'prop_score', 'score'))}</strong></div><div><span>Grade</span><strong>${esc(value(item, 'prop_grade', 'grade'))}</strong></div><div><span>RR</span><strong>${esc(rr(item))}</strong></div><div><span>Итог</span><strong>${esc(result.label)}</strong></div><div><span>Дата открытия</span><strong>${esc(date(value(item, 'created_at_utc', 'created_at')))}</strong></div><div><span>Дата закрытия</span><strong>${esc(date(value(item, 'closed_at_utc', 'closed_at')))}</strong></div></div>${chart(item)}<section class="archive-modal__text"><h3>Institutional Thesis</h3><p>${esc(thesis)}</p><h3>Smart Money Narrative</h3><p><strong>narrative_source:</strong> ${esc(sourceLabel)}</p><p>${esc(fullText)}</p><h3>Причина входа</h3><p>${esc(reason)}</p><h3>Lessons Learned</h3><p>${esc(lessons)}</p></section>`;
  modal.hidden = false; document.body.style.overflow = 'hidden';
}
filters.addEventListener('click', (event) => { const button = event.target.closest('[data-symbol]'); if (button) { selectedSymbol = button.dataset.symbol; render(); } });
rows.addEventListener('click', (event) => { const row = event.target.closest('tr[data-index]'); if (row) openItem(items[Number(row.dataset.index)]); });
rows.addEventListener('keydown', (event) => { if (event.key === 'Enter') { const row = event.target.closest('tr[data-index]'); if (row) openItem(items[Number(row.dataset.index)]); } });
function closeModal() { modal.hidden = true; document.body.style.overflow = ''; }
document.getElementById('archiveModalClose').addEventListener('click', closeModal); modal.addEventListener('click', (event) => { if (event.target === modal) closeModal(); }); document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeModal(); });
async function loadArchive() { try { const response = await fetch('/api/archive?include_active=true', { cache: 'no-store' }); if (!response.ok) throw new Error(`HTTP ${response.status}`); const data = await response.json(); items = Array.isArray(data.items) ? data.items : Array.isArray(data.archive) ? data.archive : []; render(); } catch (error) { rows.innerHTML = '<tr><td colspan="7" class="archive-empty">Архив временно недоступен.</td></tr>'; meta.textContent = `Ошибка загрузки: ${error.message}`; } }
loadArchive();
