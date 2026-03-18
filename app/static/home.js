const ticker = document.getElementById('ticker');
const summaryCount = document.getElementById('summaryCount');
const summaryUpdatedAt = document.getElementById('summaryUpdatedAt');
const statsGrid = document.getElementById('statsGrid');
const activeSignalsGrid = document.getElementById('activeSignalsGrid');
const archiveSignalsGrid = document.getElementById('archiveSignalsGrid');

const REFRESH_INTERVAL_MS = 60000;

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return `${new Intl.DateTimeFormat('ru-RU', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(date)} UTC`;
}

function formatPrice(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return Number(value).toFixed(5).replace(/0+$/, '').replace(/\.$/, '');
}

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return '0%';
  return `${Number(value).toFixed(value % 1 === 0 ? 0 : 2)}%`;
}

function formatPatternConfidence(value) {
  if (value == null || Number.isNaN(Number(value))) return '0%';
  return `${Math.round(Number(value) * 100)}%`;
}

function formatRiskReward(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return `1:${Number(value).toFixed(2).replace(/0+$/, '').replace(/\.$/, '')}`;
}

function getDirectionLabel(signal) {
  return signal.direction === 'LONG' ? 'LONG' : signal.direction === 'SHORT' ? 'SHORT' : 'FLAT';
}

function getStatusMeta(status) {
  return {
    active: { label: 'Active', className: 'status-badge--active' },
    hit: { label: 'Hit', className: 'status-badge--hit' },
    missed: { label: 'Missed', className: 'status-badge--missed' },
    cancelled: { label: 'Cancelled', className: 'status-badge--cancelled' },
    expired: { label: 'Expired', className: 'status-badge--expired' },
  }[status] || { label: status || 'unknown', className: 'status-badge--expired' };
}

function getPatternBiasLabel(direction) {
  return {
    bullish: 'Бычий',
    bearish: 'Медвежий',
    neutral: 'Нейтральный',
  }[direction] || 'Нейтральный';
}

function getPatternImpactLabel(alignment) {
  return {
    supports: 'Подтверждает сигнал',
    conflicts: 'Конфликтует с сигналом',
    neutral: 'Нейтрально к сигналу',
    not_applicable: 'Не влияет на вход',
  }[alignment] || 'Не влияет на вход';
}

function getAnnotationAppearance(type) {
  return {
    order_block: { className: 'chart-zone chart-zone--order-block', labelClass: 'chart-label chart-label--zone' },
    liquidity: { className: 'chart-zone chart-zone--liquidity', labelClass: 'chart-label chart-label--zone' },
    fvg: { className: 'chart-zone chart-zone--fvg', labelClass: 'chart-label chart-label--zone' },
    imbalance: { className: 'chart-zone chart-zone--imbalance', labelClass: 'chart-label chart-label--zone' },
    entry: { className: 'chart-line chart-line--entry', labelClass: 'chart-label chart-label--entry' },
    stop_loss: { className: 'chart-line chart-line--stop', labelClass: 'chart-label chart-label--stop' },
    take_profit: { className: 'chart-line chart-line--target', labelClass: 'chart-label chart-label--target' },
    support: { className: 'chart-line chart-line--support', labelClass: 'chart-label chart-label--support' },
    resistance: { className: 'chart-line chart-line--resistance', labelClass: 'chart-label chart-label--resistance' },
    pattern_line: { className: 'chart-segment chart-segment--pattern', labelClass: 'chart-label chart-label--pattern' },
    pattern_point: { className: 'chart-point chart-point--pattern', labelClass: 'chart-label chart-label--pattern' },
    pattern_breakout: { className: 'chart-point chart-point--breakout', labelClass: 'chart-label chart-label--breakout' },
    pattern_target: { className: 'chart-line chart-line--pattern-target', labelClass: 'chart-label chart-label--target' },
    pattern_invalidation: { className: 'chart-line chart-line--pattern-invalid', labelClass: 'chart-label chart-label--stop' },
  }[type] || { className: 'chart-line', labelClass: 'chart-label' };
}

async function getSignals() {
  const response = await fetch('/api/signals');
  if (!response.ok) {
    throw new Error('signals_request_failed');
  }
  return response.json();
}

function buildTickerText(payload) {
  const active = payload.activeSignals || [];
  if (!active.length) {
    return 'Нет активных сигналов: лента ждёт новый подтверждённый сетап.';
  }
  return active
    .slice(0, 8)
    .map((signal) => {
      const patternSuffix = signal.patternSummary?.dominantPatternTitleRu ? ` • Паттерн: ${signal.patternSummary.dominantPatternTitleRu}` : '';
      return `${signal.symbol} ${getDirectionLabel(signal)} • ${signal.status_label_ru} • RR ${formatRiskReward(signal.risk_reward)}${patternSuffix}`;
    })
    .join('  ✦  ');
}

function renderStats(stats = {}) {
  if (!statsGrid) return;
  const cards = [
    { label: 'Всего сигналов', value: stats.total ?? 0, hint: 'Общий объём записей в ленте и архиве.' },
    { label: 'Успешные', value: stats.hit ?? 0, hint: 'Сигналы со статусом hit.' },
    { label: 'Неуспешные', value: stats.missed ?? 0, hint: 'Сигналы со статусом missed.' },
    { label: 'Success rate', value: formatPercent(stats.successRate ?? 0), hint: 'hit / (hit + missed) * 100' },
    { label: 'Failure rate', value: formatPercent(stats.failureRate ?? 0), hint: 'missed / (hit + missed) * 100' },
  ];

  statsGrid.innerHTML = cards.map((card) => `
    <article class="stat-surface" aria-label="${escapeHtml(card.label)}">
      <span class="stat-surface__label">${escapeHtml(card.label)}</span>
      <strong class="stat-surface__value">${escapeHtml(card.value)}</strong>
      <p class="stat-surface__hint">${escapeHtml(card.hint)}</p>
    </article>
  `).join('');
}

function buildChart(signal) {
  const historical = Array.isArray(signal.chartData) ? signal.chartData : [];
  const projected = Array.isArray(signal.projectedCandles) ? signal.projectedCandles : [];
  const candles = [...historical, ...projected];

  if (!candles.length) {
    return '<div class="signal-chart__empty">Для этого сигнала нет данных для безопасной визуализации.</div>';
  }

  const values = candles.flatMap((candle) => [candle.open, candle.high, candle.low, candle.close]);
  const annotationValues = (signal.annotations || []).flatMap((item) => [item.value, item.from_price, item.to_price, item.start_price, item.end_price, item.point_price]).filter((value) => value != null);
  const minPrice = Math.min(...values, ...annotationValues);
  const maxPrice = Math.max(...values, ...annotationValues);
  const width = 860;
  const height = 320;
  const padLeft = 34;
  const padRight = 16;
  const padTop = 14;
  const padBottom = 30;
  const plotWidth = width - padLeft - padRight;
  const plotHeight = height - padTop - padBottom;
  const stepX = plotWidth / Math.max(candles.length, 1);
  const candleWidth = Math.max(8, Math.min(20, stepX * 0.55));
  const range = Math.max(maxPrice - minPrice, 0.0001);
  const yOf = (price) => padTop + ((maxPrice - Number(price)) / range) * plotHeight;
  const xOf = (index) => padLeft + Number(index) * stepX + stepX / 2;

  const grid = Array.from({ length: 4 }, (_, index) => {
    const price = minPrice + (range / 3) * index;
    const y = yOf(price);
    return `
      <g>
        <line class="chart-grid__line" x1="${padLeft}" y1="${y.toFixed(2)}" x2="${width - padRight}" y2="${y.toFixed(2)}"></line>
        <text class="chart-grid__label" x="0" y="${(y + 4).toFixed(2)}">${formatPrice(price)}</text>
      </g>
    `;
  }).join('');

  const annotations = (signal.annotations || []).map((annotation) => {
    const appearance = getAnnotationAppearance(annotation.type);
    if (annotation.start_price != null && annotation.end_price != null && annotation.start_index != null && annotation.end_index != null) {
      const x1 = xOf(annotation.start_index);
      const x2 = xOf(annotation.end_index);
      const y1 = yOf(annotation.start_price);
      const y2 = yOf(annotation.end_price);
      return `
        <g>
          <line class="${appearance.className}" x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}">
            <title>${escapeHtml(`${annotation.label}: ${annotation.description_ru}`)}</title>
          </line>
          <text class="${appearance.labelClass}" x="${((x1 + x2) / 2).toFixed(2)}" y="${(Math.min(y1, y2) - 8).toFixed(2)}">${escapeHtml(annotation.label)}</text>
        </g>
      `;
    }

    if (annotation.point_price != null && annotation.point_index != null) {
      const x = xOf(annotation.point_index);
      const y = yOf(annotation.point_price);
      return `
        <g>
          <circle class="${appearance.className}" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="4.5">
            <title>${escapeHtml(`${annotation.label}: ${annotation.description_ru}`)}</title>
          </circle>
          <text class="${appearance.labelClass}" x="${(x + 8).toFixed(2)}" y="${(y - 8).toFixed(2)}">${escapeHtml(annotation.label)}</text>
        </g>
      `;
    }

    if (annotation.from_price != null && annotation.to_price != null) {
      const startIndex = Math.max(0, annotation.start_index ?? 0);
      const endIndex = Math.min(candles.length - 1, annotation.end_index ?? candles.length - 1);
      const x = padLeft + startIndex * stepX;
      const zoneWidth = Math.max(stepX, (endIndex - startIndex + 1) * stepX);
      const y1 = yOf(annotation.from_price);
      const y2 = yOf(annotation.to_price);
      const top = Math.min(y1, y2);
      const zoneHeight = Math.max(10, Math.abs(y2 - y1));
      return `
        <g>
          <rect class="${appearance.className}" x="${x.toFixed(2)}" y="${top.toFixed(2)}" width="${zoneWidth.toFixed(2)}" height="${zoneHeight.toFixed(2)}">
            <title>${escapeHtml(`${annotation.label}: ${annotation.description_ru}`)}</title>
          </rect>
          <text class="${appearance.labelClass}" x="${(x + 8).toFixed(2)}" y="${(top + 14).toFixed(2)}">${escapeHtml(annotation.label)}</text>
        </g>
      `;
    }

    if (annotation.value != null) {
      const y = yOf(annotation.value);
      return `
        <g>
          <line class="${appearance.className}" x1="${padLeft}" y1="${y.toFixed(2)}" x2="${width - padRight}" y2="${y.toFixed(2)}">
            <title>${escapeHtml(`${annotation.label}: ${annotation.description_ru}`)}</title>
          </line>
          <text class="${appearance.labelClass}" x="${(width - padRight - 170).toFixed(2)}" y="${(y - 6).toFixed(2)}">${escapeHtml(annotation.label)} ${formatPrice(annotation.value)}</text>
        </g>
      `;
    }

    return '';
  }).join('');

  const candlesMarkup = candles.map((candle, index) => {
    const centerX = padLeft + index * stepX + stepX / 2;
    const bodyX = centerX - candleWidth / 2;
    const openY = yOf(candle.open);
    const closeY = yOf(candle.close);
    const highY = yOf(candle.high);
    const lowY = yOf(candle.low);
    const top = Math.min(openY, closeY);
    const bodyHeight = Math.max(4, Math.abs(closeY - openY));
    const isBullish = candle.close >= candle.open;
    const isProjected = index >= historical.length;
    return `
      <g class="chart-candle ${isBullish ? 'chart-candle--bullish' : 'chart-candle--bearish'} ${isProjected ? 'chart-candle--projected' : ''}">
        <line class="chart-candle__wick" x1="${centerX.toFixed(2)}" y1="${highY.toFixed(2)}" x2="${centerX.toFixed(2)}" y2="${lowY.toFixed(2)}">
          <title>${escapeHtml(`${candle.time_label}: O ${formatPrice(candle.open)}, H ${formatPrice(candle.high)}, L ${formatPrice(candle.low)}, C ${formatPrice(candle.close)}`)}</title>
        </line>
        <rect class="chart-candle__body" x="${bodyX.toFixed(2)}" y="${top.toFixed(2)}" width="${candleWidth.toFixed(2)}" height="${bodyHeight.toFixed(2)}"></rect>
        <text class="chart-axis__label" x="${(centerX - 10).toFixed(2)}" y="${height - 8}">${escapeHtml(candle.time_label)}</text>
      </g>
    `;
  }).join('');

  return `
    <div class="signal-chart__frame">
      <svg class="signal-chart__svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Свечной график сигнала ${escapeHtml(signal.symbol)}">
        ${grid}
        ${annotations}
        ${candlesMarkup}
      </svg>
    </div>
  `;
}

function buildAnnotationTags(signal) {
  const annotations = signal.annotations || [];
  if (!annotations.length) {
    return '<span class="analytics-chip analytics-chip--muted">Аннотации недоступны</span>';
  }
  return annotations.map((annotation) => `
    <span class="analytics-chip analytics-chip--${escapeHtml(annotation.type)}" title="${escapeHtml(annotation.description_ru)}">
      ${escapeHtml(annotation.label)}
    </span>
  `).join('');
}

function buildPatternBlock(signal) {
  const patterns = signal.chartPatterns || [];
  const summary = signal.patternSummary;
  const impact = signal.patternSignalImpact;

  if (!patterns.length) {
    return `
      <div class="pattern-empty-state">
        <strong>Графические паттерны</strong>
        <p>Явные графические паттерны не обнаружены.</p>
      </div>
    `;
  }

  const patternCards = patterns.map((pattern) => `
    <article class="pattern-card pattern-card--${escapeHtml(pattern.direction)}">
      <div class="pattern-card__head">
        <strong>${escapeHtml(pattern.title_ru)}</strong>
        <span>${escapeHtml(getPatternBiasLabel(pattern.direction))}</span>
      </div>
      <div class="pattern-card__stats">
        <span>Уверенность: ${escapeHtml(formatPatternConfidence(pattern.confidence))}</span>
        <span>Статус: ${escapeHtml(pattern.status === 'confirmed' ? 'Подтверждён' : pattern.status)}</span>
      </div>
      <p>${escapeHtml(pattern.description_ru)}</p>
      <small>${escapeHtml(pattern.explanation_ru)}</small>
    </article>
  `).join('');

  return `
    <section class="pattern-analysis-panel">
      <div class="signal-details__panel-head">
        <strong>Графические паттерны</strong>
        <span>${escapeHtml(summary?.patternSummaryRu || 'Дополнительный подтверждающий модуль')}</span>
      </div>
      <div class="pattern-summary-grid">
        <div><span>Найдено</span><strong>${escapeHtml(String(summary?.patternsDetected || 0))}</strong></div>
        <div><span>Доминирует</span><strong>${escapeHtml(summary?.dominantPatternTitleRu || '—')}</strong></div>
        <div><span>Bias</span><strong>${escapeHtml(getPatternBiasLabel(summary?.patternBias))}</strong></div>
        <div><span>Влияние</span><strong>${escapeHtml(getPatternImpactLabel(impact?.patternAlignmentWithSignal))}</strong></div>
      </div>
      ${impact ? `
        <div class="pattern-impact pattern-impact--${escapeHtml(impact.patternAlignmentWithSignal || 'neutral')}">
          <strong>${escapeHtml(impact.patternAlignmentLabelRu || 'Нейтрально')}</strong>
          <span>${escapeHtml(impact.explanationRu || '')}</span>
        </div>
      ` : ''}
      <div class="pattern-card-list">${patternCards}</div>
    </section>
  `;
}

function buildSignalCard(signal, sectionLabel) {
  const article = document.createElement('article');
  const statusMeta = getStatusMeta(signal.status);
  article.className = `signal-card premium-signal-card premium-signal-card--${signal.status}`;

  const detailsId = `signal-details-${signal.signal_id}`;

  article.innerHTML = `
    <div class="premium-signal-card__surface">
      <div class="premium-signal-card__header">
        <div>
          <p class="premium-signal-card__eyebrow">${escapeHtml(signal.timeframe)} • ${escapeHtml(sectionLabel)}</p>
          <h3 class="premium-signal-card__title">${escapeHtml(signal.symbol)}</h3>
          <p class="premium-signal-card__subtitle">${escapeHtml(getDirectionLabel(signal))} • опубликован ${formatDateTime(signal.signal_time_utc)}</p>
        </div>
        <div class="premium-signal-card__badges">
          <span class="signal-chip signal-chip--${signal.action}">${escapeHtml(getDirectionLabel(signal))}</span>
          <span class="status-badge ${statusMeta.className}">${escapeHtml(signal.status_label_ru || statusMeta.label)}</span>
        </div>
      </div>

      <div class="premium-signal-card__metrics">
        <div class="metric-box">
          <span>Точка входа</span>
          <strong>${formatPrice(signal.entry)}</strong>
        </div>
        <div class="metric-box">
          <span>Stop Loss</span>
          <strong>${formatPrice(signal.stop_loss)}</strong>
        </div>
        <div class="metric-box">
          <span>Take Profit</span>
          <strong>${(signal.takeProfits || []).map(formatPrice).join(' / ') || formatPrice(signal.take_profit)}</strong>
        </div>
        <div class="metric-box">
          <span>Risk / Reward</span>
          <strong>${formatRiskReward(signal.risk_reward)}</strong>
        </div>
      </div>

      <div class="premium-signal-card__description">
        <p>${escapeHtml(signal.description_ru)}</p>
      </div>

      ${signal.patternSummary?.dominantPatternTitleRu ? `
        <div class="signal-pattern-banner signal-pattern-banner--${escapeHtml(signal.patternSignalImpact?.patternAlignmentWithSignal || 'neutral')}">
          <strong>Паттерн: ${escapeHtml(signal.patternSummary.dominantPatternTitleRu)}</strong>
          <span>${escapeHtml(signal.patternSignalImpact?.patternAlignmentLabelRu || 'Дополнительный фактор')}</span>
        </div>
      ` : ''}

      <div class="premium-signal-card__footer">
        <div class="premium-signal-card__meta">
          <span>Вероятность: ${escapeHtml(String(signal.probability_percent || signal.probability || 0))}%</span>
          <span>Источник: ${escapeHtml(signal.data_status === 'real' ? 'Реальные данные + proxy chart overlay' : 'Proxy visualization')}</span>
        </div>
        <button
          class="details-button"
          type="button"
          aria-expanded="false"
          aria-controls="${escapeHtml(detailsId)}"
          aria-label="Показать детали сигнала ${escapeHtml(signal.symbol)}"
        >
          Подробнее
        </button>
      </div>

      <div class="signal-details" id="${escapeHtml(detailsId)}" hidden>
        <div class="signal-details__grid">
          <section class="signal-details__panel">
            <div class="signal-details__panel-head">
              <strong>Структура сигнала</strong>
              <span>${escapeHtml(signal.chart_note_ru)}</span>
            </div>
            ${buildChart(signal)}
          </section>
          <section class="signal-details__panel">
            <div class="signal-details__panel-head">
              <strong>Аналитика</strong>
              <span>Order Blocks, liquidity, SR, FVG, imbalance, patterns</span>
            </div>
            <div class="analytics-chip-list">${buildAnnotationTags(signal)}</div>
            <div class="detail-kv-list">
              <div><span>Статус</span><strong>${escapeHtml(signal.status_label_ru)}</strong></div>
              <div><span>Причина</span><strong>${escapeHtml(signal.reason_ru)}</strong></div>
              <div><span>Инвалидация</span><strong>${escapeHtml(signal.invalidation_ru)}</strong></div>
              <div><span>Прогресс к TP</span><strong>${formatPercent(signal.progressToTP || 0)}</strong></div>
              <div><span>Риск до SL</span><strong>${formatPercent(signal.progressToSL || 0)}</strong></div>
              <div><span>Текущая цена</span><strong>${formatPrice(signal.progress?.current_price)}</strong></div>
            </div>
            ${buildPatternBlock(signal)}
          </section>
        </div>
      </div>
    </div>
  `;

  const button = article.querySelector('.details-button');
  const details = article.querySelector('.signal-details');

  button?.addEventListener('click', () => {
    const expanded = button.getAttribute('aria-expanded') === 'true';
    button.setAttribute('aria-expanded', String(!expanded));
    button.textContent = expanded ? 'Подробнее' : 'Скрыть детали';
    if (details) {
      details.hidden = expanded;
    }
  });

  return article;
}

function renderEmptyState(container, title, text) {
  if (!container) return;
  container.innerHTML = `
    <article class="empty-state">
      <h3>${escapeHtml(title)}</h3>
      <p>${escapeHtml(text)}</p>
    </article>
  `;
}

function renderSignals(container, signals, sectionLabel, emptyText) {
  if (!container) return;
  container.innerHTML = '';
  if (!signals.length) {
    renderEmptyState(container, 'Список пуст', emptyText);
    return;
  }
  signals.forEach((signal) => {
    container.appendChild(buildSignalCard(signal, sectionLabel));
  });
}

function renderDashboard(payload) {
  if (ticker) {
    ticker.textContent = buildTickerText(payload);
  }
  if (summaryCount) {
    summaryCount.textContent = String(payload.stats?.total ?? payload.signals?.length ?? 0);
  }
  if (summaryUpdatedAt) {
    summaryUpdatedAt.textContent = formatDateTime(payload.updated_at_utc);
  }

  renderStats(payload.stats || {});
  renderSignals(activeSignalsGrid, payload.activeSignals || [], 'Актуальные', 'Сейчас нет активных сигналов. Система ждёт подтверждённый сетап.');
  renderSignals(archiveSignalsGrid, payload.archiveSignals || [], 'Архив', 'Архив пока пуст.');
}

async function loadDashboard() {
  renderEmptyState(activeSignalsGrid, 'Загрузка сигналов…', 'Собираем активные сетапы и архивную статистику.');
  renderEmptyState(archiveSignalsGrid, 'Загрузка архива…', 'Подготавливаем историю статусов сигналов.');

  try {
    const payload = await getSignals();
    renderDashboard(payload);
  } catch {
    if (ticker) {
      ticker.textContent = 'Не удалось загрузить тикер сигналов.';
    }
    renderEmptyState(activeSignalsGrid, 'Сигналы временно недоступны', 'API не вернул данные по активным сетапам.');
    renderEmptyState(archiveSignalsGrid, 'Архив временно недоступен', 'Не удалось получить историю статусов сигналов.');
  }
}

window.addEventListener('load', () => {
  loadDashboard();
  window.setInterval(loadDashboard, REFRESH_INTERVAL_MS);
});
