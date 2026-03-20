const ideasRoot = document.getElementById("ideas");
const symbolFilter = document.getElementById("symbol-filter");
const timeframeFilter = document.getElementById("timeframe-filter");

const modal = document.getElementById("modal");
const modalTitle = document.getElementById("modal-title");
const modalSub = document.getElementById("modal-sub");
const closeModalBtn = document.getElementById("close-modal");

const analysisText = document.getElementById("analysis-text");

const chartHost = document.getElementById("chart-host");
const overlayCanvas = document.getElementById("chart-overlay");
const chartPlaceholder = document.getElementById("chart-placeholder");
const chartPlaceholderText = document.getElementById("chart-placeholder-text");

const ideaSummary = document.getElementById("idea-summary");
const levelEntry = document.getElementById("level-entry");
const levelSl = document.getElementById("level-sl");
const levelTp = document.getElementById("level-tp");
const levelRr = document.getElementById("level-rr");
const detailStatus = document.getElementById("detail-status");

let allIdeas = [];
let activeIdea = null;
let chart = null;
let candleSeries = null;
let currentChartPayload = null;
let detailRequestId = 0;
const CHART_REQUEST_TIMEOUT_MS = 5000;

function toUnixSeconds(value) {
  if (value == null || value === "") return null;
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 1e12 ? Math.floor(value / 1000) : Math.floor(value);
  }
  const numeric = Number(String(value).trim());
  if (Number.isFinite(numeric)) {
    return numeric > 1e12 ? Math.floor(numeric / 1000) : Math.floor(numeric);
  }
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

function normalizeChartCandles(rawCandles) {
  if (!Array.isArray(rawCandles)) return [];
  return rawCandles
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const time = toUnixSeconds(item.time ?? item.datetime ?? item.timestamp ?? item.date);
      const open = Number(item.open);
      const high = Number(item.high);
      const low = Number(item.low);
      const close = Number(item.close);
      if (![time, open, high, low, close].every(Number.isFinite)) return null;
      return { time, open, high, low, close };
    })
    .filter(Boolean)
    .sort((a, b) => a.time - b.time);
}

function normalizeChartResponse(payload) {
  const rawCandles = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.candles)
      ? payload.candles
      : Array.isArray(payload?.data)
        ? payload.data
        : [];
  const normalizedCandles = normalizeChartCandles(rawCandles);
  console.debug("[ideas detail] raw chart response", payload);
  console.debug("[ideas detail] normalized candles length", normalizedCandles.length);
  console.debug("[ideas detail] first normalized candle", normalizedCandles[0] || null);
  return {
    ...(payload && typeof payload === "object" ? payload : {}),
    candles: normalizedCandles,
  };
}
const fallbackIdeas = [
  {
    id: "eurusd-m15-bullish-demo",
    symbol: "EURUSD",
    pair: "EURUSD",
    timeframe: "M15",
    tf: "M15",
    direction: "bullish",
    bias: "bullish",
    confidence: 72,
    summary: "EURUSD на M15 сохраняет бычий уклон. Приоритет — continuation после отката в demand-зону.",
    summary_ru: "EURUSD на M15 сохраняет бычий уклон. Приоритет — continuation после отката в demand-зону.",
    entry: 1.0849,
    stopLoss: 1.0832,
    takeProfit: 1.0876,
    context: "Восходящая структура.",
    trigger: "Подтверждение реакции от зоны.",
    invalidation: "Пробой локального HL.",
    target: "Предыдущий максимум / buy-side liquidity.",
    tags: ["Fallback", "SMC", "Liquidity", "M15", "EURUSD"],
    is_fallback: true,
  },
  {
    id: "gbpusd-h1-bearish-demo",
    symbol: "GBPUSD",
    pair: "GBPUSD",
    timeframe: "H1",
    tf: "H1",
    direction: "bearish",
    bias: "bearish",
    confidence: 69,
    summary: "GBPUSD на H1 остаётся под давлением после снятия buy-side liquidity. Базовый сценарий — sell on pullback.",
    summary_ru: "GBPUSD на H1 остаётся под давлением после снятия buy-side liquidity. Базовый сценарий — sell on pullback.",
    entry: 1.2715,
    stopLoss: 1.2741,
    takeProfit: 1.2668,
    context: "Слабая реакция от premium-зоны.",
    trigger: "Отбой после ретеста imbalance.",
    invalidation: "Закрепление выше локального swing high.",
    target: "Возврат к sell-side liquidity.",
    tags: ["Fallback", "SMC", "Pullback", "H1", "GBPUSD"],
    is_fallback: true,
  },
  {
    id: "usdjpy-h4-neutral-demo",
    symbol: "USDJPY",
    pair: "USDJPY",
    timeframe: "H4",
    tf: "H4",
    direction: "neutral",
    bias: "neutral",
    confidence: 64,
    summary: "USDJPY консолидируется в диапазоне. Приоритет — ждать подтверждение выхода.",
    summary_ru: "USDJPY консолидируется в диапазоне. Приоритет — ждать подтверждение выхода.",
    entry: 149.82,
    stopLoss: 149.21,
    takeProfit: 150.96,
    context: "Диапазон перед импульсом.",
    trigger: "Подтверждённый breakout и retest.",
    invalidation: "Возврат внутрь диапазона.",
    target: "Ликвидность над максимумами диапазона.",
    tags: ["Fallback", "Liquidity", "Range", "H4", "USDJPY"],
    is_fallback: true,
  },
  {
    id: "usdcad-m15-bearish-demo",
    symbol: "USDCAD",
    pair: "USDCAD",
    timeframe: "M15",
    tf: "M15",
    direction: "bearish",
    bias: "bearish",
    confidence: 71,
    summary: "USDCAD удерживает медвежий intraday-уклон. Базовый сценарий — sell continuation после отката.",
    summary_ru: "USDCAD удерживает медвежий intraday-уклон. Базовый сценарий — sell continuation после отката.",
    entry: 1.3484,
    stopLoss: 1.3502,
    takeProfit: 1.3451,
    context: "Нисходящая структура с давлением из premium-зоны.",
    trigger: "Слабая реакция покупателей на ретесте supply.",
    invalidation: "Возврат выше локального lower high.",
    target: "Ближайшая sell-side liquidity под intraday-минимумом.",
    tags: ["Fallback", "SMC", "Liquidity", "M15", "USDCAD"],
    is_fallback: true,
  },
  {
    id: "eurgbp-h1-bullish-demo",
    symbol: "EURGBP",
    pair: "EURGBP",
    timeframe: "H1",
    tf: "H1",
    direction: "bullish",
    bias: "bullish",
    confidence: 66,
    summary: "EURGBP формирует бычье восстановление от discount-зоны. Приоритет — continuation после подтверждения.",
    summary_ru: "EURGBP формирует бычье восстановление от discount-зоны. Приоритет — continuation после подтверждения.",
    entry: 0.8526,
    stopLoss: 0.8508,
    takeProfit: 0.8563,
    context: "Цена удерживает higher low после снятия sell-side liquidity.",
    trigger: "Подтверждённый импульс выше локального range.",
    invalidation: "Потеря спроса и возврат ниже demand-зоны.",
    target: "Тест ближайшего buy-side liquidity.",
    tags: ["Fallback", "SMC", "Continuation", "H1", "EURGBP"],
    is_fallback: true,
  },
  {
    id: "eurchf-h4-bearish-demo",
    symbol: "EURCHF",
    pair: "EURCHF",
    timeframe: "H4",
    tf: "H4",
    direction: "bearish",
    bias: "bearish",
    confidence: 63,
    summary: "EURCHF торгуется под давлением внутри медвежьего swing-сценария. Приоритет — sell on rally.",
    summary_ru: "EURCHF торгуется под давлением внутри медвежьего swing-сценария. Приоритет — sell on rally.",
    entry: 0.9587,
    stopLoss: 0.9621,
    takeProfit: 0.9528,
    context: "Рынок сохраняет lower highs после отката в premium.",
    trigger: "Подтверждение слабости покупателей после ретеста imbalance.",
    invalidation: "Закрепление выше последнего swing high.",
    target: "Возврат к sell-side liquidity и предыдущему минимуму диапазона.",
    tags: ["Fallback", "SMC", "Swing", "H4", "EURCHF"],
    is_fallback: true,
  },
];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function getDirectionRu(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (["bullish", "buy", "long"].includes(raw)) return "БЫЧИЙ";
  if (["bearish", "sell", "short"].includes(raw)) return "МЕДВЕЖИЙ";
  return "НЕЙТРАЛЬНЫЙ";
}

function getDirectionLabel(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (["bullish", "buy", "long"].includes(raw)) return "BUY";
  if (["bearish", "sell", "short"].includes(raw)) return "SELL";
  return "NEUTRAL";
}

function normalizeWhitespace(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function truncateText(value, limit = 92) {
  const text = normalizeWhitespace(value);
  if (!text || text.length <= limit) return text;
  return `${text.slice(0, limit - 1).trimEnd()}…`;
}

function buildShortText(idea) {
  const direct = normalizeWhitespace(idea?.short_text || idea?.shortText);
  if (direct) return truncateText(direct);

  const base = normalizeWhitespace(idea?.summary_ru || idea?.summary || idea?.full_text || idea?.fullText);
  let compact = base.split(/(?<=[.!?])\s+/)[0] || base;
  compact = compact.split(/\s[—-]\s/)[0] || compact;
  compact = compact.replace(/[.!?]+$/, "").trim();

  const direction = getDirectionLabel(idea?.direction || idea?.bias);
  if (compact && !compact.toUpperCase().startsWith(direction)) {
    compact = `${direction} ${compact}`;
  }

  return truncateText(compact || `${direction} ждать подтверждение структуры`);
}

function buildFullText(idea) {
  const direct = normalizeWhitespace(idea?.full_text || idea?.fullText || idea?.narrative);
  if (direct) return direct;

  const segments = [
    idea?.summary,
    idea?.summary_ru,
    idea?.ideaContext,
    idea?.trigger,
    idea?.invalidation,
    idea?.target,
  ];
  const unique = [];
  const seen = new Set();

  segments.forEach((segment) => {
    const text = normalizeWhitespace(segment);
    if (!text) return;
    const key = text.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    unique.push(text.replace(/[.!?]+$/, ""));
  });

  const joined = unique.join(". ").trim();
  if (!joined) return "Идея подготовлена без расширенного аналитического текста.";
  return /[.!?]$/.test(joined) ? joined : `${joined}.`;
}

function normalizeIdea(idea) {
  const symbol = String(idea?.symbol || idea?.pair || idea?.instrument || "MARKET").toUpperCase();
  const timeframe = String(idea?.timeframe || idea?.tf || "H1").toUpperCase();
  const direction = String(idea?.direction || idea?.bias || "neutral").toLowerCase();
  const summary = idea?.summary_ru || idea?.summary || idea?.description_ru || idea?.rationale || "";
  const fullText = buildFullText({
    ...idea,
    summary,
    summary_ru: summary,
  });
  const shortText = buildShortText({
    ...idea,
    summary,
    summary_ru: summary,
    full_text: fullText,
  });

  return {
    ...idea,
    id: idea?.id || idea?.idea_id || `${symbol}-${timeframe}-${direction}`,
    symbol,
    pair: symbol,
    timeframe,
    tf: timeframe,
    direction,
    bias: direction,
    confidence: Number(idea?.confidence ?? idea?.confidence_percent ?? idea?.probability_percent ?? 0),
    summary: shortText,
    summary_ru: shortText,
    short_text: shortText,
    full_text: fullText,
    entry: idea?.entry ?? idea?.entry_zone ?? "—",
    stopLoss: idea?.stopLoss ?? idea?.stop_loss ?? "—",
    takeProfit: idea?.takeProfit ?? idea?.take_profit ?? "—",
    chartData: idea?.chartData ?? idea?.chart_data ?? null,
    ideaContext: idea?.ideaContext ?? idea?.idea_context ?? idea?.idea_context_ru ?? idea?.context ?? idea?.rationale ?? summary,
    trigger: idea?.trigger ?? idea?.trigger_ru ?? (idea?.entry || idea?.entry_zone ? `Ждём подтверждение в зоне ${idea?.entry || idea?.entry_zone}.` : "Ждём подтверждение сценария по структуре."),
    invalidation: idea?.invalidation ?? idea?.invalidation_ru ?? idea?.trade_plan?.invalidation ?? "Идея отменяется при сломе исходной структуры.",
    target: idea?.target ?? idea?.target_ru ?? idea?.trade_plan?.target_1 ?? (idea?.takeProfit || idea?.take_profit ? `Ближайшая цель: ${idea?.takeProfit || idea?.take_profit}.` : "Цель будет уточняться после появления подтверждения."),
    tags: Array.isArray(idea?.tags) ? idea.tags : [symbol, timeframe, getDirectionRu(direction)],
    is_fallback: Boolean(idea?.is_fallback),
  };
}

function normalizeIdeas(data) {
  if (Array.isArray(data)) return data.filter(Boolean).map(normalizeIdea);
  if (Array.isArray(data?.ideas)) return data.ideas.filter(Boolean).map(normalizeIdea);
  return [];
}

function populateFilters(ideas) {
  const symbols = [...new Set(ideas.map(x => x.symbol).filter(Boolean))];
  const timeframes = [...new Set(ideas.map(x => x.timeframe).filter(Boolean))];

  symbolFilter.innerHTML = `<option value="ALL">Все пары</option>` +
    symbols.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");

  timeframeFilter.innerHTML = `<option value="ALL">Все ТФ</option>` +
    timeframes.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
}

function getFilteredIdeas() {
  const symbol = String(symbolFilter.value || "ALL").trim().toUpperCase();
  const timeframe = String(timeframeFilter.value || "ALL").trim().toUpperCase();

  return allIdeas.filter((idea) => {
    const currentSymbol = String(idea.symbol || idea.pair || "").trim().toUpperCase();
    const currentTf = String(idea.timeframe || idea.tf || "").trim().toUpperCase();
    const symbolOk = symbol === "ALL" || currentSymbol === symbol;
    const tfOk = timeframe === "ALL" || currentTf === timeframe;
    return symbolOk && tfOk;
  });
}

function renderIdeas(ideas, notice = "") {
  if (!ideas.length) {
    ideasRoot.innerHTML = `<div class="empty">По выбранным фильтрам идеи не найдены.</div>`;
    return;
  }

  const cardsMarkup = ideas.map((idea, idx) => {
    const tags = Array.isArray(idea.tags) ? idea.tags : [];
    const symbol = idea.symbol || "";
    const direction = getDirectionRu(idea.direction || "NEUTRAL");
    const timeframe = idea.timeframe || "";
    const confidence = idea.confidence ?? "-";
    const summary = buildShortText(idea);

    return `
      <div class="card" data-index="${idx}">
        <div class="card-head">
          <div>
            <div class="symbol">${escapeHtml(symbol)}</div>
            <div class="meta">${escapeHtml(direction)} · ${escapeHtml(timeframe)} · ${escapeHtml(String(confidence))}%</div>
          </div>
        </div>
        <p class="summary">${escapeHtml(summary)}</p>
        <div class="tags">
          ${tags.map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}
        </div>
      </div>
    `;
  }).join("");

  ideasRoot.innerHTML = `${notice ? `<div class="empty">${escapeHtml(notice)}</div>` : ""}${cardsMarkup}`;

  document.querySelectorAll(".card").forEach((card, idx) => {
    card.addEventListener("click", () => openIdea(ideas[idx]));
  });
}

function applyFilters() {
  renderIdeas(getFilteredIdeas());
}

function normalizeLevel(value) {
  if (value == null) return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const normalized = String(value).replace(",", ".").trim();
  if (!normalized || normalized === "—") return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatLevel(value) {
  if (value == null || value === "" || value === "—") return "—";
  const numeric = normalizeLevel(value);
  if (numeric == null) return String(value);
  return numeric.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
}

function calculateRiskReward(idea) {
  const entry = normalizeLevel(idea.entry);
  const stop = normalizeLevel(idea.stopLoss);
  const target = normalizeLevel(idea.takeProfit);
  if (entry == null || stop == null || target == null) return "—";

  const risk = Math.abs(entry - stop);
  const reward = Math.abs(target - entry);
  if (!risk || !Number.isFinite(risk) || !Number.isFinite(reward)) return "—";
  return `${(reward / risk).toFixed(2)}R`;
}

function setTextContent(node, value, fallback = "—") {
  node.textContent = value && String(value).trim() ? String(value).trim() : fallback;
}

function renderDetailText(idea) {
  const fullText = buildFullText(idea);
  setTextContent(ideaSummary, fullText, "Идея доступна без расширенного summary.");
  setTextContent(analysisText, fullText, "Идея доступна без расширенного summary.");
  setTextContent(levelEntry, formatLevel(idea.entry));
  setTextContent(levelSl, formatLevel(idea.stopLoss));
  setTextContent(levelTp, formatLevel(idea.takeProfit));
  setTextContent(levelRr, calculateRiskReward(idea));
}

function ensureChart() {
  if (chart) return;

  chart = LightweightCharts.createChart(chartHost, {
    layout: {
      background: { color: "#08111f" },
      textColor: "#9fb0c7",
    },
    grid: {
      vertLines: { color: "#13233c" },
      horzLines: { color: "#13233c" },
    },
    rightPriceScale: {
      borderColor: "#233553",
    },
    timeScale: {
      borderColor: "#233553",
      timeVisible: true,
      secondsVisible: false,
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
    },
    width: chartHost.clientWidth,
    height: chartHost.clientHeight,
  });

  candleSeries = chart.addCandlestickSeries({
    upColor: "#22c55e",
    downColor: "#ef4444",
    borderVisible: false,
    wickUpColor: "#22c55e",
    wickDownColor: "#ef4444",
  });
}

function destroyChart() {
  currentChartPayload = null;
  if (!chart) return;
  chart.remove();
  chart = null;
  candleSeries = null;
}

function resetChartState() {
  currentChartPayload = null;
  if (chart) {
    candleSeries.setData([]);
    chart.timeScale().fitContent();
  }
  const ctx = fitOverlayCanvas();
  ctx.clearRect(0, 0, overlayCanvas.clientWidth, overlayCanvas.clientHeight);
}

function showChartPlaceholder(message) {
  chartPlaceholder.classList.add("open");
  chartPlaceholderText.textContent = message;
}

function hideChartPlaceholder() {
  chartPlaceholder.classList.remove("open");
  chartPlaceholderText.textContent = "График для этой идеи сейчас недоступен.";
}

function updateDetailStatus(message) {
  detailStatus.textContent = message;
}

function resizeChart() {
  if (!chart) return;
  chart.applyOptions({
    width: chartHost.clientWidth,
    height: chartHost.clientHeight,
  });
  drawOverlay();
}

function fitOverlayCanvas() {
  const rect = overlayCanvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  overlayCanvas.width = Math.floor(rect.width * dpr);
  overlayCanvas.height = Math.floor(rect.height * dpr);
  const ctx = overlayCanvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function zoneColors(type) {
  const t = String(type || "").toLowerCase();
  if (t === "demand") return { fill: "rgba(34,197,94,0.18)", stroke: "#22c55e" };
  if (t === "supply") return { fill: "rgba(239,68,68,0.18)", stroke: "#ef4444" };
  if (t === "fvg") return { fill: "rgba(139,92,246,0.18)", stroke: "#8b5cf6" };
  return { fill: "rgba(56,189,248,0.15)", stroke: "#38bdf8" };
}

function drawLabel(ctx, x, y, text, color = "#fff", bg = "rgba(8,17,31,0.92)") {
  ctx.font = "12px Arial";
  const w = ctx.measureText(text).width + 12;
  const h = 22;
  ctx.fillStyle = bg;
  roundRect(ctx, x, y - h, w, h, 8);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.fillText(text, x + 6, y - 7);
}

function priceFromMeta(candles, meta, zones, levels) {
  if (meta === "zone_mid_0" && zones[0]) return (zones[0]._fromPrice + zones[0]._toPrice) / 2;
  if (meta === "level_0" && levels[0]) return levels[0]._price;
  if (meta === "mid_range") {
    const highs = candles.slice(25, 88).map(c => c.high);
    const lows = candles.slice(25, 88).map(c => c.low);
    return (Math.max(...highs) + Math.min(...lows)) / 2;
  }
  if (meta === "high_local") return candles[80]?.high ?? candles[candles.length - 1].high;
  if (meta === "low_local") return candles[80]?.low ?? candles[candles.length - 1].low;
  return candles[candles.length - 1].close;
}

function prepareOverlayData(payload) {
  const candles = payload.candles || [];
  const overlays = payload.overlays || {};
  const zones = (overlays.zones || []).map(zone => {
    const slice = candles.slice(zone.from_index, zone.to_index + 1);
    const lows = slice.map(c => c.low);
    const highs = slice.map(c => c.high);

    return {
      ...zone,
      _fromPrice: Math.min(...lows),
      _toPrice: Math.max(...highs),
    };
  });

  const levels = (overlays.levels || []).map(level => {
    const slice = candles.slice(level.lookback_start, level.lookback_end + 1);
    const highs = slice.map(c => c.high);
    const lows = slice.map(c => c.low);

    let price;
    if (level.price_source === "high") price = Math.max(...highs);
    else if (level.price_source === "low") price = Math.min(...lows);
    else price = (Math.max(...highs) + Math.min(...lows)) / 2;

    return {
      ...level,
      _price: price + (level.offset || 0),
    };
  });

  const arrows = (overlays.arrows || []).map(arrow => ({
    ...arrow,
    _fromPrice: priceFromMeta(candles, arrow.from_price_ref, zones, levels),
    _toPrice: priceFromMeta(candles, arrow.to_price_ref, zones, levels),
  }));

  const labels = (overlays.labels || []).map(label => ({
    ...label,
    _price: priceFromMeta(candles, label.price_ref, zones, levels),
  }));

  return { candles, zones, levels, arrows, labels };
}

function drawOverlay() {
  if (!chart || !currentChartPayload) return;

  const ctx = fitOverlayCanvas();
  const width = overlayCanvas.clientWidth;
  const height = overlayCanvas.clientHeight;
  ctx.clearRect(0, 0, width, height);

  const { candles, zones, levels, arrows, labels } = prepareOverlayData(currentChartPayload);
  const timeScale = chart.timeScale();

  zones.forEach(zone => {
    const x1 = timeScale.timeToCoordinate(candles[zone.from_index]?.time);
    const x2 = timeScale.timeToCoordinate(candles[zone.to_index]?.time);
    const y1 = candleSeries.priceToCoordinate(zone._toPrice);
    const y2 = candleSeries.priceToCoordinate(zone._fromPrice);

    if (x1 == null || x2 == null || y1 == null || y2 == null) return;

    const style = zoneColors(zone.type);
    const left = Math.min(x1, x2);
    const top = Math.min(y1, y2);
    const rectW = Math.max(10, Math.abs(x2 - x1));
    const rectH = Math.max(8, Math.abs(y2 - y1));

    ctx.fillStyle = style.fill;
    ctx.strokeStyle = style.stroke;
    ctx.lineWidth = 2;
    roundRect(ctx, left, top, rectW, rectH, 10);
    ctx.fill();
    ctx.stroke();

    drawLabel(ctx, left + 8, top + 24, zone.label, style.stroke);
  });

  levels.forEach(level => {
    const x1 = timeScale.timeToCoordinate(candles[level.from_index]?.time);
    const x2 = timeScale.timeToCoordinate(candles[level.to_index]?.time);
    const y = candleSeries.priceToCoordinate(level._price);

    if (x1 == null || x2 == null || y == null) return;

    ctx.save();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([8, 6]);
    ctx.beginPath();
    ctx.moveTo(x1, y);
    ctx.lineTo(x2, y);
    ctx.stroke();
    ctx.restore();

    drawLabel(ctx, Math.max(8, x2 - 130), y - 6, level.label, "#ffffff");
  });

  arrows.forEach(arrow => {
    const x1 = timeScale.timeToCoordinate(candles[arrow.from_index]?.time);
    const x2 = timeScale.timeToCoordinate(candles[arrow.to_index]?.time);
    const y1 = candleSeries.priceToCoordinate(arrow._fromPrice);
    const y2 = candleSeries.priceToCoordinate(arrow._toPrice);

    if (x1 == null || x2 == null || y1 == null || y2 == null) return;

    ctx.save();
    ctx.strokeStyle = "#facc15";
    ctx.fillStyle = "#facc15";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();

    const angle = Math.atan2(y2 - y1, x2 - x1);
    const headLen = 12;
    ctx.beginPath();
    ctx.moveTo(x2, y2);
    ctx.lineTo(x2 - headLen * Math.cos(angle - Math.PI / 6), y2 - headLen * Math.sin(angle - Math.PI / 6));
    ctx.lineTo(x2 - headLen * Math.cos(angle + Math.PI / 6), y2 - headLen * Math.sin(angle + Math.PI / 6));
    ctx.closePath();
    ctx.fill();
    ctx.restore();

    drawLabel(ctx, x2 + 8, y2 - 8, arrow.label, "#facc15");
  });

  labels.forEach(label => {
    const x = timeScale.timeToCoordinate(candles[label.index]?.time);
    const y = candleSeries.priceToCoordinate(label._price);

    if (x == null || y == null) return;

    const color = label.text.includes("BOS") || label.text.includes("CHoCH") || label.text.includes("Liquidity")
      ? "#38bdf8"
      : "#f59e0b";

    drawLabel(ctx, x + 6, y - 6, label.text, color);
  });
}

async function resolveChartData(idea) {
  if (idea.chartData?.candles?.length || Array.isArray(idea.chartData) || Array.isArray(idea.chartData?.data)) {
    return normalizeChartResponse(idea.chartData);
  }

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort('chart_timeout'), CHART_REQUEST_TIMEOUT_MS);

  try {
    const params = new URLSearchParams({ tf: idea.timeframe || 'H1' });
    const res = await fetch(`/api/chart/${encodeURIComponent(idea.symbol)}?${params.toString()}`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!res.ok) return null;
    const payload = normalizeChartResponse(await res.json());
    return payload.candles.length ? payload : null;
  } catch (error) {
    if (error?.name === 'AbortError') {
      console.warn('Chart request timeout for idea detail-view.', idea?.symbol, idea?.timeframe);
      return null;
    }
    console.warn("Chart data unavailable for idea detail-view.", error);
    return null;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function openIdea(idea) {
  activeIdea = idea;
  const requestId = ++detailRequestId;
  modalTitle.textContent = `${idea.symbol} — ${getDirectionRu(idea.direction)}`;
  modalSub.textContent = `${idea.timeframe} · Уверенность ${idea.confidence}%`;
  modal.classList.add("open");

  renderDetailText(idea);
  updateDetailStatus("Загружаем detail-view идеи и проверяем доступность графика.");
  ensureChart();
  resetChartState();
  showChartPlaceholder("Загружаем график для идеи...");

  const payload = await resolveChartData(idea);
  if (requestId !== detailRequestId || activeIdea?.id !== idea.id) return;

  if (payload?.candles?.length) {
    hideChartPlaceholder();
    currentChartPayload = payload;
    console.debug("[ideas detail] calling setData with candles", payload.candles.length);
    candleSeries.setData(payload.candles);
    chart.timeScale().fitContent();
    updateDetailStatus("Detail-view заполнен: единый текст, уровни и график доступны.");

    requestAnimationFrame(() => {
      requestAnimationFrame(() => drawOverlay());
    });
    return;
  }

  console.debug("[ideas detail] fallback triggered", {
    reason: "empty_normalized_candles",
    symbol: idea.symbol,
    timeframe: idea.timeframe,
  });
  showChartPlaceholder("Chart unavailable");
  updateDetailStatus("График недоступен, поэтому detail-view завершил загрузку и показал fallback вместо вечного loading.");
}

function closeModal() {
  modal.classList.remove("open");
  activeIdea = null;
  detailRequestId += 1;
  showChartPlaceholder("График для этой идеи сейчас недоступен.");
}

async function load() {
  try {
    const res = await fetch("/api/ideas", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    allIdeas = normalizeIdeas(data);
    if (!allIdeas.length) {
      console.warn("Получен пустой массив идей из /api/ideas.");
      throw new Error("ideas_empty_payload");
    }
    populateFilters(allIdeas);
    applyFilters();
  } catch (error) {
    console.warn("Не удалось загрузить /api/ideas, включаем fallback.", error);
    allIdeas = normalizeIdeas(fallbackIdeas);
    populateFilters(allIdeas);
    renderIdeas(allIdeas, "Источник идей временно недоступен — показан резервный demo-набор.");
  }
}

symbolFilter.addEventListener("change", applyFilters);
timeframeFilter.addEventListener("change", applyFilters);

closeModalBtn.addEventListener("click", closeModal);
modal.addEventListener("click", (event) => {
  if (event.target === modal) closeModal();
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeModal();
});

window.addEventListener("resize", () => {
  resizeChart();
});
window.addEventListener("beforeunload", destroyChart);

load();
