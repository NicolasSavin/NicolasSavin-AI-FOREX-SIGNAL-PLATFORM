const ticker = document.getElementById('ticker');
const signalsGrid = document.getElementById('signalsGrid');

async function getJson(url) {
  const resp = await fetch(url);
  return resp.json();
}

function renderList(id, rows, mapper) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = '';
  rows.forEach((row) => {
    const li = document.createElement('li');
    li.textContent = mapper(row);
    el.appendChild(li);
  });
}

function formatUpdatedAt(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('ru-RU', {
    dateStyle: 'short',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(date) + ' UTC';
}

function getImpactLabel(impact) {
  const labels = {
    high: 'Высокое влияние',
    medium: 'Среднее влияние',
    low: 'Низкое влияние',
    unknown: 'Статус источника',
  };
  return labels[impact] || 'Без оценки влияния';
}

function renderNews(rows, updatedAt) {
  const el = document.getElementById('newsList');
  const meta = document.getElementById('newsUpdatedAt');
  if (meta) {
    meta.textContent = `Обновление: ${formatUpdatedAt(updatedAt)}`;
  }
  if (!el) return;

  el.innerHTML = '';
  if (!rows.length) {
    el.innerHTML = `
      <article class="news-card news-card--empty">
        <p class="news-card__title">Новостей пока нет</p>
        <p class="news-card__text">Подтверждённые новости появятся здесь после обновления источника.</p>
      </article>
    `;
    return;
  }

  rows.forEach((row) => {
    const card = document.createElement('article');
    const impact = row.impact || 'unknown';
    card.className = 'news-card animated';
    card.innerHTML = `
      <div class="news-card__header">
        <p class="news-card__title">${row.title || 'Новость без заголовка'}</p>
        <span class="impact-badge impact-badge--${impact}">${getImpactLabel(impact)}</span>
      </div>
      <p class="news-card__text">${row.description_ru || 'Описание отсутствует.'}</p>
    `;
    el.appendChild(card);
  });
}

function renderSignals(signals) {
  if (!signalsGrid) return;
  signalsGrid.innerHTML = '';
  signals.forEach((s) => {
    const card = document.createElement('article');
    card.className = 'card animated';
    card.innerHTML = `
      <h3>${s.symbol} • ${s.timeframe} • ${s.action}</h3>
      <p>Entry: <strong>${s.entry ?? '—'}</strong></p>
      <p>Stop Loss: <strong>${s.stop_loss ?? '—'}</strong></p>
      <p>Take Profit: <strong>${s.take_profit ?? '—'}</strong></p>
      <p>Risk/Reward: <strong>${s.risk_reward ?? '—'}</strong></p>
      <p>Distance to target: <strong>${s.distance_to_target_percent ?? '—'}%</strong></p>
      <p>Уверенность: <strong>${s.confidence_percent}%</strong></p>
      <p>Статус: <strong>${s.status}</strong></p>
      <p>Описание: ${s.description_ru}</p>
      <p>Причина: ${s.reason_ru}</p>
      <p>Invalidation: ${s.invalidation_ru}</p>
      <p>Данные: <strong>${s.data_status}</strong></p>
    `;
    signalsGrid.appendChild(card);
  });
}

async function refreshAll() {
  try {
    const [signals, ideas, news, calendar, heatmap] = await Promise.all([
      getJson('/signals/live'),
      getJson('/ideas/market'),
      getJson('/news/market'),
      getJson('/calendar/events'),
      getJson('/heatmap'),
    ]);

    if (ticker) {
      ticker.textContent = signals.ticker.join(' • ') || 'Тикер: сигналов пока нет';
    }

    renderSignals(signals.signals || []);
    renderList('ideasList', ideas.ideas || [], (x) => `${x.title}: ${x.description_ru}`);
    renderNews(news.news || [], news.updated_at_utc);
    renderList('calendarList', calendar.events || [], (x) => `${x.title}: ${x.description_ru}`);
    renderList('heatmapList', heatmap.rows || [], (x) => `${x.pair}: ${x.change_percent ?? 'нет данных'} [${x.label}]`);
  } catch (e) {
    if (ticker) ticker.textContent = 'Ошибка загрузки данных платформы';
    renderNews([], null);
  }
}

window.addEventListener('load', refreshAll);
setInterval(refreshAll, 60000);
