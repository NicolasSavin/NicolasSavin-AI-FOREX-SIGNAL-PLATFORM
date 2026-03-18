const ticker = document.getElementById('ticker');
const signalsGrid = document.getElementById('signalsGrid');
const ideasList = document.getElementById('ideasList');
const calendarList = document.getElementById('calendarList');
const heatmapList = document.getElementById('heatmapList');
const newsList = document.getElementById('newsList');
const newsUpdatedAt = document.getElementById('newsUpdatedAt');

async function getJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} for ${url}`);
  }
  return resp.json();
}

function renderList(id, rows, mapper, emptyMessage = 'Данные пока недоступны.') {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = '';

  if (!rows.length) {
    const li = document.createElement('li');
    li.textContent = emptyMessage;
    el.appendChild(li);
    return;
  }

  rows.forEach((row) => {
    const li = document.createElement('li');
    li.textContent = mapper(row);
    el.appendChild(li);
  });
}

function setListLoading(id, message) {
  renderList(id, [], () => '', message);
}

function formatUpdatedAt(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return `${new Intl.DateTimeFormat('ru-RU', {
    dateStyle: 'short',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(date)} UTC`;
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

function setNewsMeta(message) {
  if (newsUpdatedAt) {
    newsUpdatedAt.textContent = message;
  }
}

function renderNews(rows, updatedAt, emptyTitle = 'Новостей пока нет', emptyText = 'Подтверждённые новости появятся здесь после обновления источника.') {
  if (!newsList) return;

  setNewsMeta(`Обновление: ${formatUpdatedAt(updatedAt)}`);
  newsList.innerHTML = '';

  if (!rows.length) {
    newsList.innerHTML = `
      <article class="news-card news-card--empty">
        <p class="news-card__title">${emptyTitle}</p>
        <p class="news-card__text">${emptyText}</p>
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
    newsList.appendChild(card);
  });
}

function renderNewsError() {
  if (!newsList) return;
  setNewsMeta('Обновление: ошибка загрузки');
  newsList.innerHTML = `
    <article class="news-card news-card--empty">
      <p class="news-card__title">Новости временно недоступны</p>
      <p class="news-card__text">Не удалось получить подтверждённые новости. Попробуйте обновить страницу позже.</p>
    </article>
  `;
}

function renderSignals(signals) {
  if (!signalsGrid) return;
  signalsGrid.innerHTML = '';

  if (!signals.length) {
    signalsGrid.innerHTML = `
      <article class="card">
        <p>Сигналы пока недоступны. Попробуйте обновить страницу позже.</p>
      </article>
    `;
    return;
  }

  signals.forEach((s) => {
    const card = document.createElement('article');
    card.className = 'card animated';
    card.innerHTML = `
      <h3>${s.symbol} • ${s.timeframe} • ${s.action}</h3>
      <p>Точка входа: <strong>${s.entry ?? '—'}</strong></p>
      <p>Стоп-лосс: <strong>${s.stop_loss ?? '—'}</strong></p>
      <p>Тейк-профит: <strong>${s.take_profit ?? '—'}</strong></p>
      <p>Риск/прибыль: <strong>${s.risk_reward ?? '—'}</strong></p>
      <p>Дистанция до цели: <strong>${s.distance_to_target_percent ?? '—'}%</strong></p>
      <p>Уверенность: <strong>${s.confidence_percent}%</strong></p>
      <p>Статус: <strong>${s.status}</strong></p>
      <p>Описание: ${s.description_ru}</p>
      <p>Причина: ${s.reason_ru}</p>
      <p>Инвалидация: ${s.invalidation_ru}</p>
      <p>Данные: <strong>${s.data_status}</strong></p>
    `;
    signalsGrid.appendChild(card);
  });
}

async function loadNewsSection() {
  if (!newsList) return;

  setNewsMeta('Обновление: загрузка...');
  newsList.innerHTML = `
    <article class="news-card news-card--empty">
      <p class="news-card__title">Загрузка новостей...</p>
      <p class="news-card__text">Получаем подтверждённые новости рынка.</p>
    </article>
  `;

  try {
    const news = await getJson('/news/market');
    renderNews(news.news || [], news.updated_at_utc);
  } catch (error) {
    console.error('Не удалось загрузить новости', error);
    renderNewsError();
  }
}

async function loadSignalsSection() {
  if (!signalsGrid) return;

  if (ticker) ticker.textContent = 'Загрузка тикера...';
  signalsGrid.innerHTML = `
    <article class="card">
      <p>Загрузка сигналов...</p>
    </article>
  `;

  try {
    const signals = await getJson('/signals/live');
    if (ticker) {
      ticker.textContent = signals.ticker?.join(' • ') || 'Тикер: сигналов пока нет';
    }
    renderSignals(signals.signals || []);
  } catch (error) {
    console.error('Не удалось загрузить сигналы', error);
    if (ticker) ticker.textContent = 'Ошибка загрузки тикера';
    signalsGrid.innerHTML = `
      <article class="card">
        <p>Сигналы временно недоступны. Это не влияет на блок новостей.</p>
      </article>
    `;
  }
}

async function loadIdeasSection() {
  if (!ideasList) return;

  setListLoading('ideasList', 'Загрузка торговых идей...');
  try {
    const ideas = await getJson('/ideas/market');
    renderList('ideasList', ideas.ideas || [], (x) => `${x.title}: ${x.description_ru}`, 'Торговые идеи пока недоступны.');
  } catch (error) {
    console.error('Не удалось загрузить идеи', error);
    renderList('ideasList', [], () => '', 'Торговые идеи временно недоступны.');
  }
}

async function loadCalendarSection() {
  if (!calendarList) return;

  setListLoading('calendarList', 'Загрузка календаря...');
  try {
    const calendar = await getJson('/calendar/events');
    renderList('calendarList', calendar.events || [], (x) => `${x.title}: ${x.description_ru}`, 'События календаря пока недоступны.');
  } catch (error) {
    console.error('Не удалось загрузить календарь', error);
    renderList('calendarList', [], () => '', 'Экономический календарь ременно недоступен.');
  }
}

async function loadHeatmapSection() {
  if (!heatmapList) return;

  setListLoading('heatmapList', 'Загрузка тепловой карты...');
  try {
    const heatmap = await getJson('/heatmap');
    renderList('heatmapList', heatmap.rows || [], (x) => `${x.pair}: ${x.change_percent ?? 'нет данных'} [${x.label}]`, 'Тепловая карта пока недоступна.');
  } catch (error) {
    console.error('Не удалось загрузить тепловую карту', error);
    renderList('heatmapList', [], () => '', 'Тепловая карта временно недоступна.');
  }
}

function refreshCurrentPage() {
  loadNewsSection();

  if (signalsGrid || ideasList || calendarList || heatmapList || ticker) {
    loadSignalsSection();
    loadIdeasSection();
    loadCalendarSection();
    loadHeatmapSection();
  }
}

window.addEventListener('load', refreshCurrentPage);
setInterval(refreshCurrentPage, 60000);
