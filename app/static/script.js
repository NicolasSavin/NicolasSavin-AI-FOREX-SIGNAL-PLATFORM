const ticker = document.getElementById('ticker');
const signalsGrid = document.getElementById('signalsGrid');
const ideasList = document.getElementById('ideasList');
const calendarList = document.getElementById('calendarList');
const heatmapList = document.getElementById('heatmapList');
const newsList = document.getElementById('newsList');
const newsUpdatedAt = document.getElementById('newsUpdatedAt');
const newsStatsGrid = document.getElementById('newsStatsGrid');
const newsCategoryFilters = document.getElementById('newsCategoryFilters');
const newsImportanceFilters = document.getElementById('newsImportanceFilters');
const newsFeedMeta = document.getElementById('newsFeedMeta');
const summaryCount = document.getElementById('summaryCount');
const summaryUpdatedAt = document.getElementById('summaryUpdatedAt');
const pageName = document.body?.dataset.page || 'unknown';
const refreshIntervalMs = pageName === 'news' ? 300000 : 60000;

let knownSignalIds = new Set();
let audioContext = null;
const newsState = {
  rows: [],
  category: 'all',
  importance: 'all',
};

async function getJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} for ${url}`);
  }
  return resp.json();
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

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function renderList(id, rows, mapper, emptyMessage = 'Данные пока недоступны.') {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.add('data-list');
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

function getImpactLabel(impact) {
  return {
    high: 'Высокое влияние',
    medium: 'Среднее влияние',
    low: 'Низкое влияние',
    unknown: 'Статус источника',
  }[impact] || 'Без оценки';
}

function getImportanceClass(importance) {
  return importance || 'low';
}

function getLifecycleLabel(state) {
  return {
    active: 'active',
    open: 'open',
    closed: 'closed',
  }[state] || state || '—';
}

function getSignalValue(value) {
  return value == null ? '—' : value;
}

function buildSignalCard(signal) {
  const card = document.createElement('article');
  card.className = `signal-card signal-card--${signal.action}`;

  const progressPercent = signal.progress?.progress_percent ?? 0;
  card.innerHTML = `
    <div class="signal-card__top">
      <div>
        <h3 class="signal-card__title">${signal.symbol}</h3>
        <div class="signal-card__subtitle">${signal.timeframe} • Signal time: ${formatUpdatedAt(signal.signal_time_utc)}</div>
      </div>
      <div>
        <span class="signal-chip signal-chip--${signal.action}">${signal.action}</span>
        <span class="status-pill status-pill--${signal.lifecycle_state}">${getLifecycleLabel(signal.lifecycle_state)}</span>
      </div>
    </div>

    <div class="signal-card__stats">
      <div class="stat-box"><span>Probability</span><strong>${signal.probability_percent}%</strong></div>
      <div class="stat-box"><span>Status</span><strong>${signal.status}</strong></div>
      <div class="stat-box"><span>Data</span><strong>${signal.data_status}</strong></div>
    </div>

    <div class="signal-card__levels">
      <div class="level-box"><label>Entry</label><strong>${getSignalValue(signal.entry)}</strong></div>
      <div class="level-box"><label>Stop loss</label><strong>${getSignalValue(signal.stop_loss)}</strong></div>
      <div class="level-box"><label>Take profit</label><strong>${getSignalValue(signal.take_profit)}</strong></div>
    </div>

    <div class="progress-card">
      <strong>Progress to TP/SL</strong>
      <div class="progress-bar"><div class="progress-bar__value" style="width:${Math.max(0, Math.min(progressPercent, 100))}%"></div></div>
      <div class="progress-legend">
        <span>TP: ${getSignalValue(signal.progress?.to_take_profit_percent)}%</span>
        <span>SL: ${getSignalValue(signal.progress?.to_stop_loss_percent)}%</span>
        <span>${signal.progress?.label_ru || 'Прогресс недоступен'}</span>
      </div>
    </div>

    <div>
      <p class="signal-card__description">${signal.description_ru}</p>
      <p class="signal-card__meta">Причина: ${signal.reason_ru}</p>
      <p class="signal-card__meta">Инвалидация: ${signal.invalidation_ru}</p>
    </div>

    <div class="signal-card__footer">
      <span class="signal-card__meta">R/R: ${getSignalValue(signal.risk_reward)}</span>
      <span class="signal-card__meta">Current: ${getSignalValue(signal.progress?.current_price)}</span>
      <span class="signal-card__meta">ID: ${signal.signal_id}</span>
    </div>
  `;

  return card;
}

function renderSignals(signals, updatedAt) {
  if (!signalsGrid) return;
  signalsGrid.innerHTML = '';

  if (summaryCount) {
    summaryCount.textContent = String(signals.filter((signal) => signal.action !== 'NO_TRADE').length);
  }
  if (summaryUpdatedAt) {
    summaryUpdatedAt.textContent = formatUpdatedAt(updatedAt);
  }

  if (!signals.length) {
    signalsGrid.innerHTML = `
      <article class="empty-state">
        <h3>Сигналы пока недоступны</h3>
        <p>Попробуйте обновить страницу позже.</p>
      </article>
    `;
    return;
  }

  signals.forEach((signal) => {
    signalsGrid.appendChild(buildSignalCard(signal));
  });
}

function buildNewsTagList(items, emptyText) {
  if (!items?.length) {
    return `<span class="news-chip news-chip--muted">${emptyText}</span>`;
  }
  return items.map((item) => `<span class="news-chip">${escapeHtml(item)}</span>`).join('');
}

function getSignalRelationClass(relation) {
  return relation?.effect_on_signal || 'neutral_to_signal';
}


function renderNewsStats(rows) {
  if (!newsStatsGrid) return;

  const uniqueSources = new Set(rows.map((row) => row.source).filter(Boolean));
  const highCount = rows.filter((row) => row.importance === 'high').length;
  const latestPublished = rows[0]?.published_at ? formatUpdatedAt(rows[0].published_at) : '—';

  newsStatsGrid.innerHTML = `
    <article class="news-stat-card">
      <span>Новостей в ленте</span>
      <strong>${rows.length}</strong>
    </article>
    <article class="news-stat-card">
      <span>Высокая важность</span>
      <strong>${highCount}</strong>
    </article>
    <article class="news-stat-card">
      <span>Источники</span>
      <strong>${uniqueSources.size}</strong>
    </article>
    <article class="news-stat-card">
      <span>Последняя публикация</span>
      <strong>${latestPublished}</strong>
    </article>
  `;
}

function buildFilterButtons(items, activeValue, onClick) {
  return items.map((item) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `news-filter-chip${item.value === activeValue ? ' is-active' : ''}`;
    button.textContent = item.label;
    button.addEventListener('click', () => onClick(item.value));
    return button;
  });
}

function renderNewsFilters(rows) {
  const categories = ['all', ...new Set(rows.map((row) => row.category).filter(Boolean))];
  const categoryLabels = { all: 'Все', Forex: 'Forex', Gold: 'Gold', Crypto: 'Crypto', Macro: 'Macro', 'Central Banks': 'Central Banks', Commodities: 'Commodities', Indices: 'Indices' };
  const importanceItems = [
    { value: 'all', label: 'Все' },
    { value: 'high', label: 'Высокая' },
    { value: 'medium', label: 'Средняя' },
    { value: 'low', label: 'Низкая' },
  ];

  if (newsCategoryFilters) {
    newsCategoryFilters.innerHTML = '';
    buildFilterButtons(
      categories.map((value) => ({ value, label: categoryLabels[value] || value })),
      newsState.category,
      (value) => {
        newsState.category = value;
        renderNews(newsState.rows, null, false);
      },
    ).forEach((button) => newsCategoryFilters.appendChild(button));
  }

  if (newsImportanceFilters) {
    newsImportanceFilters.innerHTML = '';
    buildFilterButtons(importanceItems, newsState.importance, (value) => {
      newsState.importance = value;
      renderNews(newsState.rows, null, false);
    }).forEach((button) => newsImportanceFilters.appendChild(button));
  }
}

function getFilteredNewsRows(rows) {
  return rows.filter((row) => {
    const categoryOkay = newsState.category === 'all' || row.category === newsState.category;
    const importanceOkay = newsState.importance === 'all' || row.importance === newsState.importance;
    return categoryOkay && importanceOkay;
  });
}

function renderNews(rows, updatedAt, syncFilters = true, emptyTitle = 'Новостей пока нет', emptyText = 'Подтверждённые новости появятся здесь после обновления источника.') {
  if (!newsList) return;

  if (updatedAt && newsUpdatedAt) {
    newsUpdatedAt.textContent = `Обновление: ${formatUpdatedAt(updatedAt)}`;
  }

  if (syncFilters) {
    newsState.rows = [...rows];
    renderNewsStats(newsState.rows);
    renderNewsFilters(newsState.rows);
  }

  const filteredRows = getFilteredNewsRows(newsState.rows.length ? newsState.rows : rows);
  if (newsFeedMeta) {
    const totalRows = (newsState.rows.length ? newsState.rows : rows).length;
    newsFeedMeta.textContent = `Показано: ${filteredRows.length} из ${totalRows}`;
  }

  newsList.innerHTML = '';

  if (!filteredRows.length) {
    newsList.innerHTML = `
      <article class="news-card news-card--empty">
        <p class="news-card__title">${emptyTitle}</p>
        <p class="news-card__text">${emptyText}</p>
      </article>
    `;
    return;
  }

  filteredRows.forEach((row) => {
    const card = document.createElement('article');
    card.className = 'news-card';
    const relation = row.signal_relation || {};
    const hasRelation = Boolean(relation.has_related_signal);

    card.innerHTML = `
      <div class="news-card__header news-card__header--stacked">
        <div>
          <p class="news-card__eyebrow">${escapeHtml(row.category || 'News')}</p>
          <h3 class="news-card__title">${escapeHtml(row.title_ru || row.title_original || 'Новость без заголовка')}</h3>
        </div>
        <div class="news-card__badges">
          <span class="impact-badge impact-badge--${getImportanceClass(row.importance)}">${escapeHtml(row.importance_ru || getImpactLabel(row.importance))}</span>
          <span class="news-source-badge">${escapeHtml(row.source || 'RSS')}</span>
        </div>
      </div>

      <p class="news-card__summary">${escapeHtml(row.summary_ru || 'Краткий пересказ пока недоступен.')}</p>

      <div class="news-card__sections">
        <section class="news-detail-box">
          <h4>Что произошло</h4>
          <p>${escapeHtml(row.what_happened_ru || '—')}</p>
        </section>
        <section class="news-detail-box">
          <h4>Почему это важно</h4>
          <p>${escapeHtml(row.why_it_matters_ru || '—')}</p>
        </section>
        <section class="news-detail-box news-detail-box--impact">
          <h4>Влияние на рынок</h4>
          <p>${escapeHtml(row.market_impact_ru || '—')}</p>
        </section>
      </div>

      <div class="news-card__taxonomy">
        <div>
          <span class="news-label">Категория</span>
          <strong>${escapeHtml(row.category || '—')}</strong>
        </div>
        <div>
          <span class="news-label">Важность</span>
          <strong>${escapeHtml(row.importance_ru || '—')}</strong>
        </div>
        <div>
          <span class="news-label">Публикация</span>
          <strong>${formatUpdatedAt(row.published_at)}</strong>
        </div>
      </div>

      <div class="news-card__assets">
        <span class="news-label">Связанные активы</span>
        <div class="news-chip-row">${buildNewsTagList(row.assets, 'Активы не определены')}</div>
      </div>

      ${hasRelation ? `
        <div class="news-signal-box news-signal-box--${getSignalRelationClass(relation)}">
          <span class="news-label">Связь с активным сигналом</span>
          <strong>${escapeHtml(relation.effect_on_signal_ru || 'Новость нейтральна для текущего сигнала')}</strong>
        </div>
      ` : ''}

      <div class="news-card__footer">
        <p class="news-card__meta">Оригинальный заголовок: ${escapeHtml(row.title_original || '—')}</p>
        ${row.source_url ? `<a class="news-link-button" href="${escapeHtml(row.source_url)}" target="_blank" rel="noopener noreferrer">Читать источник</a>` : '<span class="news-link-button news-link-button--disabled">Источник недоступен</span>'}
      </div>
    `;
    newsList.appendChild(card);
  });
}

function renderNewsError() {
  if (!newsList) return;
  if (newsUpdatedAt) {
    newsUpdatedAt.textContent = 'Обновление: ошибка загрузки';
  }
  if (newsFeedMeta) {
    newsFeedMeta.textContent = 'Показано: ошибка загрузки';
  }
  newsList.innerHTML = `
    <article class="news-card news-card--empty">
      <p class="news-card__title">Новости временно недоступны</p>
      <p class="news-card__text">Не удалось получить подтверждённые новости. Попробуйте обновить страницу позже.</p>
    </article>
  `;
}

async function loadSignalsSection() {
  if (!signalsGrid) return;

  if (ticker) ticker.textContent = 'Загрузка рыночного тикера...';
  signalsGrid.innerHTML = `
    <article class="empty-state">
      <h3>Загрузка сигналов...</h3>
      <p>Собираем поток сигналов и рассчитываем прогресс к TP/SL.</p>
    </article>
  `;

  try {
    const payload = await getJson('/signals/live');
    if (ticker) {
      ticker.textContent = payload.ticker?.join(' • ') || 'Тикер: сигналов пока нет';
    }
    notifyAboutNewSignals(payload.signals || []);
    renderSignals(payload.signals || [], payload.updated_at_utc);
  } catch (error) {
    console.error('Не удалось загрузить сигналы', error);
    if (ticker) ticker.textContent = 'Ошибка загрузки тикера';
    signalsGrid.innerHTML = `
      <article class="empty-state">
        <h3>Сигналы временно недоступны</h3>
        <p>API не вернул live-сигналы. Остальные страницы сайта продолжают работать.</p>
      </article>
    `;
  }
}

async function loadIdeasSection() {
  if (!ideasList) return;
  renderList('ideasList', [], () => '', 'Загрузка торговых идей...');

  try {
    const ideas = await getJson('/ideas/market');
    renderList('ideasList', ideas.ideas || [], (idea) => `${idea.title}: ${idea.description_ru}`, 'Торговые идеи пока недоступны.');
  } catch (error) {
    console.error('Не удалось загрузить идеи', error);
    renderList('ideasList', [], () => '', 'Торговые идеи временно недоступны.');
  }
}

async function loadCalendarSection() {
  if (!calendarList) return;
  renderList('calendarList', [], () => '', 'Загрузка календаря...');

  try {
    const calendar = await getJson('/calendar/events');
    renderList('calendarList', calendar.events || [], (event) => `${event.title}: ${event.description_ru}`, 'События календаря пока недоступны.');
  } catch (error) {
    console.error('Не удалось загрузить календарь', error);
    renderList('calendarList', [], () => '', 'Экономический календарь временно недоступен.');
  }
}

async function loadHeatmapSection() {
  if (!heatmapList) return;
  renderList('heatmapList', [], () => '', 'Загрузка тепловой карты...');

  try {
    const heatmap = await getJson('/heatmap');
    renderList('heatmapList', heatmap.rows || [], (row) => `${row.pair}: ${row.change_percent ?? 'нет данных'} [${row.label}]`, 'Тепловая карта пока недоступна.');
  } catch (error) {
    console.error('Не удалось загрузить тепловую карту', error);
    renderList('heatmapList', [], () => '', 'Тепловая карта временно недоступна.');
  }
}

async function loadNewsSection() {
  if (!newsList) return;

  if (newsUpdatedAt) {
    newsUpdatedAt.textContent = 'Обновление: загрузка...';
  }
  newsList.innerHTML = `
    <article class="news-card news-card--empty">
      <p class="news-card__title">Загрузка новостей...</p>
      <p class="news-card__text">Получаем подтверждённые новости рынка из открытых источников.</p>
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

function ensureAudioContext() {
  if (!audioContext) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      audioContext = new AudioContextClass();
    }
  }
  return audioContext;
}

function playSignalNotification() {
  const context = ensureAudioContext();
  if (!context) return;

  if (context.state === 'suspended') {
    context.resume().catch(() => {});
  }

  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.type = 'triangle';
  oscillator.frequency.setValueAtTime(880, context.currentTime);
  oscillator.frequency.exponentialRampToValueAtTime(1320, context.currentTime + 0.18);
  gain.gain.setValueAtTime(0.0001, context.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.08, context.currentTime + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.35);
  oscillator.connect(gain);
  gain.connect(context.destination);
  oscillator.start();
  oscillator.stop(context.currentTime + 0.36);
}

function notifyAboutNewSignals(signals) {
  const currentIds = new Set(signals.map((signal) => signal.signal_id));
  const hasFreshTradableSignal = signals.some(
    (signal) => signal.action !== 'NO_TRADE' && !knownSignalIds.has(signal.signal_id),
  );

  if (knownSignalIds.size && hasFreshTradableSignal) {
    playSignalNotification();
  }

  knownSignalIds = currentIds;
}

function refreshCurrentPage() {
  loadSignalsSection();
  loadIdeasSection();
  loadCalendarSection();
  loadHeatmapSection();
  loadNewsSection();
}

window.addEventListener('load', () => {
  document.body.addEventListener('click', ensureAudioContext, { once: true });
  refreshCurrentPage();
});
setInterval(refreshCurrentPage, refreshIntervalMs);
