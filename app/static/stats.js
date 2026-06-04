const statsGrid = document.getElementById('statsPageGrid');
const updatedAt = document.getElementById('statsUpdatedAt');
const cards = [
  ['Всего идей', 'total_ideas'], ['Активные идеи', 'active'], ['Закрыто по TP', 'tp'], ['Закрыто по SL', 'sl'],
  ['WinRate', 'winrate', '%'], ['Средний RR', 'average_rr', '', 'rr'], ['Сегодня TP', 'today_tp'], ['Сегодня SL', 'today_sl'],
];
const safe = (value) => String(value ?? '—').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
async function loadStats() {
  try {
    const response = await fetch('/api/stats', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    statsGrid.innerHTML = cards.map(([label, key, suffix = '', type = '']) => `<article class="stats-page-card stats-page-card--${key}"><span>${label}</span><strong>${type === 'rr' ? `1:${safe(data[key] ?? 0)}` : `${safe(data[key] ?? 0)}${suffix}`}</strong></article>`).join('');
    updatedAt.textContent = `Обновлено: ${new Date(data.updated_at_utc).toLocaleString('ru-RU', { timeZone: 'UTC' })} UTC`;
  } catch (error) {
    statsGrid.innerHTML = '<article class="empty-state"><h3>Статистика временно недоступна</h3><p>Не удалось получить данные из /api/stats.</p></article>';
    updatedAt.textContent = `Ошибка загрузки: ${error.message}`;
  }
}
loadStats();
