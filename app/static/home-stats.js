(async function loadHomeStats() {
  try {
    const response = await fetch('/api/stats', { cache: 'no-store' });
    if (!response.ok) return;
    const stats = await response.json();
    document.getElementById('homeTodayTp').textContent = String(stats.today_tp ?? 0);
    document.getElementById('homeTodaySl').textContent = String(stats.today_sl ?? 0);
    document.getElementById('homeWinrate').textContent = `${stats.winrate ?? 0}%`;
  } catch (_) {
    // Keep unavailable values explicit instead of inventing market results.
  }
})();
