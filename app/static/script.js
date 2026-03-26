const ticker = document.getElementById('ticker');
const signalsGrid = document.getElementById('signalsGrid');
const ideasList = document.getElementById('ideasList');
const calendarList = document.getElementById('calendarList');
const heatmapList = document.getElementById('heatmapList');
const newsList = document.getElementById('newsList');
const newsUpdatedAt = document.getElementById('newsUpdatedAt');
const summaryCount = document.getElementById('summaryCount');
const summaryUpdatedAt = document.getElementById('summaryUpdatedAt');
const newsBannerPanel = document.getElementById('newsBannerPanel');
const newsBannerList = document.getElementById('newsBannerList');
const newsBannerCount = document.getElementById('newsBannerCount');
const newsBannerUpdatedAt = document.getElementById('newsBannerUpdatedAt');
const pageName = document.body?.dataset.page || 'unknown';
const refreshIntervalMs = pageName === 'news' ? 300000 : 60000;
const livePriceRefreshMs = 15000;
const NEWS_PAGE_LIMIT = 12;

let knownSignalIds = new Set();
let knownNewsIds = new Set();
let audioContext = null;
let audioUnlocked = false;

function unlockAudio() {
  audioUnlocked = true;
  const context = ensureAudioContext();
  if (context?.state === 'suspended') {
    context.resume().catch(() => {});
  }
}

document.addEventListener('pointerdown', unlockAudio, { once: true });
document.addEventListener('keydown', unlockAudio, { once: true });

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

function getSignalValue(value, digits = 5) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return Number(value).toFixed(digits).replace(/0+$/, '').replace(/\.$/, '');
}

function getPercentValue(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return `${Math.round(Number(value))}%`;
}

function getDataStatusLabel(signal) {
  return signal.data_status === 'real' ? 'Реальные данные' : 'Fallback / mock';
}

function buildProgressBar(value, modifier = '') {
  const safeValue = Math.max(0, Math.min(Number(value || 0), 100));
  return `
    <div class="progress-bar ${modifier}">
      <div class="progress-bar__value" style="width:${safeValue}%"></div>
    </div>
  `;
}

function buildProbabilityBlock(signal) {
  const probability = signal.probability ?? signal.probability_percent ?? 0;
  return `
    <section class="metric-card metric-card--probability">
      <div class="metric-card__header">
        <strong>Вероятность сигнала ${probability}%</strong>
        <span>${signal.market_context?.is_mock ? 'proxy' : 'model'}</span>
      </div>
      ${buildProgressBar(probability, 'progress-bar--probability')}
      <p class="progress-caption">Анимированная шкала показывает текущую оценку confidence модели.</p>
    </section>
  `;
}

function buildProgressBlock(signal) {
  const progressTp = signal.progressToTP ?? signal.progress?.progress_percent ?? 0;
  const progressSl = signal.progressToSL ?? signal.progress?.to_stop_loss_percent ?? 0;
  const hasMarketPrice = ['real', 'delayed'].includes(String(signal.data_status || '')) && signal.progress?.current_price != null;
  const currentPrice = hasMarketPrice ? signal.progress?.current_price : null;
  const fallbackLabel = signal.progress?.is_fallback ? ' • используется fallback цены' : '';
  const priceLabel = currentPrice == null ? 'Нет актуальных рыночных данных' : getSignalValue(currentPrice);
  return `
    <section class="metric-card">
      <div class="metric-card__header">
        <strong>Прогресс цены</strong>
        <span>${escapeHtml(signal.progress?.label_ru || 'Прогресс недоступен')}${fallbackLabel}</span>
      </div>
      <div class="dual-progress">
        <div>
          <div class="metric-card__row"><span>До Take Profit</span><strong>${getPercentValue(progressTp)}</strong></div>
          ${buildProgressBar(progressTp, 'progress-bar--tp')}
        </div>
        <div>
          <div class="metric-card__row"><span>Риск до Stop Loss</span><strong>${getPercentValue(progressSl)}</strong></div>
          ${buildProgressBar(progressSl, 'progress-bar--sl')}
        </div>
      </div>
      <div class="progress-legend">
        <span>Текущая цена: <strong class="live-price-value" data-symbol="${escapeHtml(signal.symbol)}">${priceLabel}</strong></span>
        <span>${escapeHtml(signal.progress?.zone || 'waiting')}</span>
      </div>
    </section>
  `;
}

function ensureMarketWarningHost() {
  if (!signalsGrid) return null;
  let panel = document.getElementById('marketDataWarning');
  if (panel) return panel;
  panel = document.createElement('article');
  panel.id = 'marketDataWarning';
  panel.className = 'empty-state';
  panel.hidden = true;
  signalsGrid.before(panel);
  return panel;
}

async function refreshLivePrices() {
  const nodes = Array.from(document.querySelectorAll('.live-price-value[data-symbol]'));
  if (!nodes.length) return;
  const symbols = [...new Set(nodes.map((node) => String(node.dataset.symbol || '').toUpperCase()).filter(Boolean))];
  if (!symbols.length) return;

  try {
    const payload = await getJson(`/api/market?symbols=${encodeURIComponent(symbols.join(','))}`);
    const rows = Array.isArray(payload?.market) ? payload.market : [];
    const bySymbol = new Map(rows.map((row) => [String(row.symbol || '').toUpperCase(), row]));
    nodes.forEach((node) => {
      const symbol = String(node.dataset.symbol || '').toUpperCase();
      const row = bySymbol.get(symbol);
      if (!row || row.price == null || !['real', 'delayed'].includes(String(row.data_status || ''))) {
        node.textContent = 'Нет актуальных рыночных данных';
        return;
      }
      node.textContent = getSignalValue(row.price);
    });

    const unavailable = rows.filter((row) => row.data_status !== 'real');
    const warningPanel = ensureMarketWarningHost();
    if (!warningPanel) return;
    if (!unavailable.length) {
      warningPanel.hidden = true;
      warningPanel.innerHTML = '';
      return;
    }
    warningPanel.hidden = false;
    warningPanel.innerHTML = `
      <h3>⚠️ Предупреждение по market data</h3>
      <p>Live-данные частично недоступны. Synthetic fallback отключён.</p>
      <p>${escapeHtml(unavailable.map((row) => `${row.symbol}: ${row.data_status}`).join(' • '))}</p>
    `;
  } catch {
    const warningPanel = ensureMarketWarningHost();
    if (!warningPanel) return;
    warningPanel.hidden = false;
    warningPanel.innerHTML = `
      <h3>⚠️ Предупреждение по market data</h3>
      <p>Не удалось обновить live-цены через /api/market.</p>
    `;
  }
}

function buildChartSvg(signal) {
  const chartData = Array.isArray(signal.chartData) && signal.chartData.length ? signal.chartData : [];
  if (!chartData.length) {
    return '<div class="signal-chart__empty">График недоступен, используется безопасный fallback без рыночной симуляции.</div>';
  }

  const projected = Array.isArray(signal.projectedCandles) ? signal.projectedCandles : [];
  const prices = [
    ...chartData.map((item) => Number(item.price)),
    ...projected.flatMap((item) => [Number(item.open), Number(item.high), Number(item.low), Number(item.close)]),
    ...(signal.levels || []).map((item) => Number(item.value)),
    ...(signal.zones || []).flatMap((item) => [Number(item.from_price), Number(item.to_price)]),
    ...(signal.liquidityAreas || []).flatMap((item) => [Number(item.from_price), Number(item.to_price)]),
  ].filter((value) => Number.isFinite(value));

  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const priceRange = Math.max(maxPrice - minPrice, 0.0001);
  const width = 860;
  const height = 260;
  const padX = 32;
  const padY = 20;
  const stepX = (width - padX * 2) / Math.max(chartData.length - 1, 1);
  const yOf = (price) => height - padY - ((price - minPrice) / priceRange) * (height - padY * 2);
  const points = chartData.map((item, index) => `${padX + index * stepX},${yOf(Number(item.price)).toFixed(2)}`).join(' ');

  const zones = [...(signal.zones || []), ...(signal.liquidityAreas || [])]
    .map((zone, index) => {
      const y1 = yOf(Number(zone.to_price));
      const y2 = yOf(Number(zone.from_price));
      const zoneHeight = Math.max(Math.abs(y2 - y1), 8);
      const top = Math.min(y1, y2);
      const cls = zone.zone_type === 'liquidity' ? 'signal-chart__zone signal-chart__zone--liquidity' : 'signal-chart__zone';
      return `
        <g>
          <rect class="${cls}" x="${padX}" y="${top.toFixed(2)}" width="${width - padX * 2}" height="${zoneHeight.toFixed(2)}"></rect>
          <text class="signal-chart__zone-label" x="${padX + 10}" y="${(top + 14).toFixed(2)}">${escapeHtml(zone.label)}</text>
        </g>
      `;
    })
    .join('');

  const levels = (signal.levels || []).map((level) => {
    const y = yOf(Number(level.value));
    return `
      <g>
        <line class="signal-chart__level" x1="${padX}" y1="${y.toFixed(2)}" x2="${width - padX}" y2="${y.toFixed(2)}"></line>
        <text class="signal-chart__level-label" x="${width - padX - 140}" y="${(y - 6).toFixed(2)}">${escapeHtml(level.label)} ${getSignalValue(level.value)}</text>
      </g>
    `;
  }).join('');

  const candleWidth = 18;
  const projectedMarkup = projected.map((candle, index) => {
    const x = width - padX - (projected.length - index) * (candleWidth + 10);
    const openY = yOf(Number(candle.open));
    const closeY = yOf(Number(candle.close));
    const highY = yOf(Number(candle.high));
    const lowY = yOf(Number(candle.low));
    const top = Math.min(openY, closeY);
    const rectHeight = Math.max(Math.abs(openY - closeY), 6);
    const bullish = Number(candle.close) >= Number(candle.open);
    return `
      <g class="signal-chart__projected-candle ${bullish ? 'is-bullish' : 'is-bearish'}">
        <line x1="${x + candleWidth / 2}" y1="${highY.toFixed(2)}" x2="${x + candleWidth / 2}" y2="${lowY.toFixed(2)}"></line>
        <rect x="${x}" y="${top.toFixed(2)}" width="${candleWidth}" height="${rectHeight.toFixed(2)}"></rect>
        <text class="signal-chart__axis-label" x="${x - 2}" y="${height - 6}">${escapeHtml(candle.time_label)}</text>
      </g>
    `;
  }).join('');

  const axisLabels = chartData.map((item, index) => `
    <text class="signal-chart__axis-label" x="${padX + index * stepX}" y="${height - 6}">${escapeHtml(item.time_label)}</text>
  `).join('');

  return `
    <svg class="signal-chart__svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="График сигнала ${escapeHtml(signal.symbol)}">
      ${zones}
      ${levels}
      <polyline class="signal-chart__path" points="${points}"></polyline>
      ${projectedMarkup}
      ${axisLabels}
    </svg>
  `;
}

function buildNewsAlert(signal) {
  const relevant = (signal.relatedNews || []).filter((item) => ['medium', 'high'].includes(item.impact));
  if (!relevant.length) {
    return `
      <section class="signal-news-alert signal-news-alert--empty">
        <strong>Новостных предупреждений нет</strong>
        <p>Для ${escapeHtml(signal.symbol)} пока нет критичных новостей средней или высокой важности.</p>
      </section>
    `;
  }

  const item = relevant[0];
  return `
    <section class="signal-news-alert signal-news-alert--${item.impact}">
      <div class="signal-news-alert__header">
        <strong>Внимание: важная новость</strong>
        <span class="impact-badge impact-badge--${item.impact}">${escapeHtml(item.impact_ru)}</span>
      </div>
      <h4>${escapeHtml(item.title)}</h4>
      <p>${escapeHtml(item.description)}</p>
      <div class="signal-news-alert__meta">
        <span>Инструмент: ${escapeHtml(item.instrument)}</span>
        <span>Время: ${formatUpdatedAt(item.event_time)}</span>
        <span>Статус: ${escapeHtml(item.status)}</span>
      </div>
    </section>
  `;
}

function buildSignalCard(signal) {
  const card = document.createElement('article');
  card.className = `signal-card signal-card--${signal.action}`;
  const directionLabel = signal.action === 'BUY' ? 'Покупка' : signal.action === 'SELL' ? 'Продажа' : 'Наблюдение';

  card.innerHTML = `
    <div class="signal-card__top">
      <div>
        <h3 class="signal-card__title">${escapeHtml(signal.symbol)}</h3>
        <div class="signal-card__subtitle">${escapeHtml(signal.timeframe)} • Дата/время выхода: ${formatUpdatedAt(signal.signal_datetime || signal.signal_time_utc)}</div>
      </div>
      <div class="signal-card__badges">
        <span class="signal-chip signal-chip--${signal.action}">${escapeHtml(signal.action)}</span>
        <span class="status-pill status-pill--${signal.state}">${escapeHtml(getLifecycleLabel(signal.state))}</span>
      </div>
    </div>

    <div class="signal-card__stats">
      <div class="stat-box"><span>Направление</span><strong>${directionLabel}</strong></div>
      <div class="stat-box"><span>Статус</span><strong>${escapeHtml(signal.status)}</strong></div>
      <div class="stat-box"><span>Состояние</span><strong>${escapeHtml(signal.state)}</strong></div>
      <div class="stat-box"><span>Источник</span><strong>${escapeHtml(getDataStatusLabel(signal))}</strong></div>
    </div>

    <div class="signal-card__levels">
      <div class="level-box"><label>Точка входа</label><strong>${getSignalValue(signal.entry)}</strong></div>
      <div class="level-box"><label>Stop Loss</label><strong>${getSignalValue(signal.stop_loss)}</strong></div>
      <div class="level-box"><label>Take Profit</label><strong>${getSignalValue(signal.take_profit)}</strong></div>
      <div class="level-box"><label>Время сигнала</label><strong>${escapeHtml(signal.signal_time_label || '—')}</strong></div>
    </div>

    ${buildProbabilityBlock(signal)}
    ${buildProgressBlock(signal)}

    <section class="signal-chart">
      <div class="metric-card__header">
        <strong>График сигнала</strong>
        <span>Order block • уровни • liquidity areas • projected candles</span>
      </div>
      ${buildChartSvg(signal)}
    </section>

    ${buildNewsAlert(signal)}

    <div class="signal-card__text-block">
      <p class="signal-card__description">${escapeHtml(signal.description_ru)}</p>
      <p class="signal-card__meta"><strong>Причина:</strong> ${escapeHtml(signal.reason_ru)}</p>
      <p class="signal-card__meta"><strong>Инвалидация:</strong> ${escapeHtml(signal.invalidation_ru)}</p>
    </div>

    <div class="signal-card__footer">
      <span class="signal-card__meta">R/R: ${signal.risk_reward ?? '—'}</span>
      <span class="signal-card__meta">ID: ${escapeHtml(signal.signal_id)}</span>
      <span class="signal-card__meta">Обновлено: ${formatUpdatedAt(signal.updated_at_utc)}</span>
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

function renderNews(rows, updatedAt, emptyTitle = 'Новостей пока нет', emptyText = 'Подтверждённые новости появятся здесь после обновления источника.') {
  const limitedRows = rows.slice(0, NEWS_PAGE_LIMIT);
  if (!newsList) return;

  if (newsUpdatedAt) {
    newsUpdatedAt.textContent = `Обновление: ${formatUpdatedAt(updatedAt)}`;
  }
  newsList.innerHTML = '';

  if (!limitedRows.length) {
    newsList.innerHTML = `
      <article class="news-card news-card--empty">
        <p class="news-card__title">${emptyTitle}</p>
        <p class="news-card__text">${emptyText}</p>
      </article>
    `;
    return;
  }

  limitedRows.forEach((row) => {
    const card = document.createElement('article');
    card.className = 'news-card';
    const relation = row.signal_relation || {};
    const hasRelation = Boolean(relation.has_related_signal);

    card.innerHTML = `
      <div class="news-card__header">
        <div class="news-card__header-main">
          <p class="news-card__eyebrow">${escapeHtml(row.category || 'Новости')}</p>
          <h3 class="news-card__title">${escapeHtml(row.title_ru || row.title_original || 'Новость без заголовка')}</h3>
        </div>
        <div class="news-card__badges">
          <span class="impact-badge impact-badge--${getImportanceClass(row.importance)}">${escapeHtml(row.importance_ru || '—')}</span>
          <span class="news-source-badge">${escapeHtml(row.source || 'RSS')}</span>
        </div>
      </div>

      <p class="news-card__summary">${escapeHtml(row.summary_ru || 'Краткий пересказ пока недоступен.')}</p>

      <div class="news-card__compact-meta">
        <span class="news-chip">${escapeHtml(row.instrument || 'MARKET')}</span>
        <span class="news-chip">${formatUpdatedAt(row.eventTime || row.published_at)}</span>
        <span class="news-chip">${escapeHtml(row.status || '—')}</span>
      </div>

      <div class="news-card__compact-grid">
        <section class="news-detail-box news-detail-box--compact">
          <h4>Что произошло</h4>
          <p>${escapeHtml(row.what_happened_ru || row.summary_ru || '—')}</p>
        </section>
        <section class="news-detail-box news-detail-box--compact news-detail-box--impact">
          <h4>Влияние</h4>
          <p>${escapeHtml(row.market_impact_ru || row.why_it_matters_ru || '—')}</p>
        </section>
      </div>

      <div class="news-card__assets">
        <span class="news-label">Связанные активы</span>
        <div class="news-chip-row">${buildNewsTagList(row.relatedInstruments || row.assets, 'Активы не определены')}</div>
      </div>

      ${hasRelation ? `
        <div class="news-signal-box news-signal-box--${getSignalRelationClass(relation)}">
          <span class="news-label">Связь с активным сигналом</span>
          <strong>${escapeHtml(relation.effect_on_signal_ru || 'Новость нейтральна для текущего сигнала')}</strong>
        </div>
      ` : ''}

      <div class="news-card__footer news-card__footer--compact">
        <p class="news-card__meta">Оригинал: ${escapeHtml(row.title_original || '—')}</p>
        ${row.source_url ? `<a class="news-link-button" href="${escapeHtml(row.source_url)}" target="_blank" rel="noopener noreferrer">Источник</a>` : '<span class="news-link-button news-link-button--disabled">Источник недоступен</span>'}
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
  newsList.innerHTML = `
    <article class="news-card news-card--empty">
      <p class="news-card__title">Новости временно недоступны</p>
      <p class="news-card__text">Не удалось получить подтверждённые новости. Попробуйте обновить страницу позже.</p>
    </article>
  `;
}

function renderNewsBanner(rows, updatedAt) {
  if (!newsBannerPanel || !newsBannerList) return;
  const relevant = (rows || []).filter((item) => ['medium', 'high'].includes(item.impact || item.importance));
  newsBannerList.innerHTML = '';
  newsBannerPanel.hidden = !relevant.length;
  if (newsBannerCount) newsBannerCount.textContent = String(relevant.length);
  if (newsBannerUpdatedAt) newsBannerUpdatedAt.textContent = formatUpdatedAt(updatedAt);
  if (!relevant.length) return;

  relevant.forEach((item) => {
    const article = document.createElement('article');
    article.className = `news-banner news-banner--${item.impact || item.importance}`;
    article.innerHTML = `
      <div>
        <p class="news-banner__title">${escapeHtml(item.instrument || 'MARKET')} • ${escapeHtml(item.title_ru || item.title_original)}</p>
        <p class="news-banner__text">${escapeHtml(item.summary_ru || item.description || 'Описание недоступно.')}</p>
      </div>
      <div class="news-banner__meta">
        <span>${formatUpdatedAt(item.eventTime || item.published_at)}</span>
        <span class="impact-badge impact-badge--${item.impact || item.importance}">${escapeHtml(item.importance_ru || '—')}</span>
      </div>
    `;
    newsBannerList.appendChild(article);
  });
}

async function loadSignalsSection() {
  if (!signalsGrid) return;

  if (ticker) ticker.textContent = 'Загрузка рыночного тикера...';
  signalsGrid.innerHTML = `
    <article class="empty-state">
      <h3>Загрузка сигналов...</h3>
      <p>Собираем поток активных сигналов и рассчитываем прогресс к TP/SL.</p>
    </article>
  `;

  try {
    const signalsPayload = await getJson('/signals/live');
    if (ticker) {
      ticker.textContent = signalsPayload.ticker?.join(' • ') || 'Тикер: сигналов пока нет';
    }
    notifyAboutNewSignals(signalsPayload.signals || []);
    renderSignals(signalsPayload.signals || [], signalsPayload.updated_at_utc);
    await refreshLivePrices();
  } catch {
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
  } catch {
    renderList('ideasList', [], () => '', 'Торговые идеи временно недоступны.');
  }
}

async function loadCalendarSection() {
  if (!calendarList) return;
  renderList('calendarList', [], () => '', 'Загрузка календаря...');

  try {
    const calendar = await getJson('/calendar/events');
    renderList('calendarList', calendar.events || [], (event) => `${event.title}: ${event.description_ru}`, 'События календаря пока недоступны.');
  } catch {
    renderList('calendarList', [], () => '', 'Экономический календарь временно недоступен.');
  }
}

async function loadHeatmapSection() {
  if (!heatmapList) return;
  renderList('heatmapList', [], () => '', 'Загрузка тепловой карты...');

  try {
    const heatmap = await getJson('/heatmap');
    renderList('heatmapList', heatmap.rows || [], (row) => `${row.pair}: ${row.change_percent ?? 'нет данных'} [${row.label}]`, 'Тепловая карта пока недоступна.');
  } catch {
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
    const news = await getJson('/api/news');
    notifyAboutNews(news.news || []);
    renderNews(news.news || [], news.updated_at_utc);
  } catch {
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

function playTone({ startFrequency, endFrequency, duration, type }) {
  if (!audioUnlocked) return;
  const context = ensureAudioContext();
  if (!context) return;

  if (context.state === 'suspended') {
    context.resume().catch(() => {});
  }

  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.type = type;
  oscillator.frequency.setValueAtTime(startFrequency, context.currentTime);
  oscillator.frequency.exponentialRampToValueAtTime(endFrequency, context.currentTime + duration * 0.55);
  gain.gain.setValueAtTime(0.0001, context.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.06, context.currentTime + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + duration);
  oscillator.connect(gain);
  gain.connect(context.destination);
  oscillator.start();
  oscillator.stop(context.currentTime + duration + 0.02);
}

function playSignalNotification() {
  playTone({ startFrequency: 880, endFrequency: 1320, duration: 0.34, type: 'triangle' });
}

function playNewsNotification() {
  playTone({ startFrequency: 620, endFrequency: 780, duration: 0.5, type: 'sine' });
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

function notifyAboutNews(newsItems) {
  const currentIds = new Set(newsItems.map((item) => item.id));
  const hasFreshRelevantNews = newsItems.some(
    (item) => ['medium', 'high'].includes(item.impact || item.importance) && !knownNewsIds.has(item.id),
  );

  if (knownNewsIds.size && hasFreshRelevantNews) {
    playNewsNotification();
  }

  knownNewsIds = currentIds;
}

function refreshCurrentPage() {
  loadSignalsSection();
  loadIdeasSection();
  loadCalendarSection();
  loadHeatmapSection();
  loadNewsSection();
  refreshLivePrices();
}

window.addEventListener('load', () => {
  refreshCurrentPage();
  window.setInterval(refreshCurrentPage, refreshIntervalMs);
  window.setInterval(refreshLivePrices, livePriceRefreshMs);
});
