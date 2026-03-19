const ideasRoot = document.getElementById("ideas");
const symbolFilter = document.getElementById("symbol-filter");
const timeframeFilter = document.getElementById("timeframe-filter");

const modal = document.getElementById("modal");
const modalTitle = document.getElementById("modal-title");
const modalSub = document.getElementById("modal-sub");
const closeModalBtn = document.getElementById("close-modal");

const analysisSmc = document.getElementById("analysis-smc");
const analysisLiquidity = document.getElementById("analysis-liquidity");
const analysisDivergence = document.getElementById("analysis-divergence");
const analysisPattern = document.getElementById("analysis-pattern");

const chartHost = document.getElementById("chart-host");
const overlayCanvas = document.getElementById("chart-overlay");

let allIdeas = [];
let activeIdea = null;
let chart = null;
let candleSeries = null;
let currentChartPayload = null;

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

function normalizeIdeas(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.ideas)) return data.ideas;
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
  const symbol = symbolFilter.value;
  const timeframe = timeframeFilter.value;

  return allIdeas.filter((idea) => {
    const symbolOk = symbol === "ALL" || idea.symbol === symbol;
    const tfOk = timeframe === "ALL" || idea.timeframe === timeframe;
    return symbolOk && tfOk;
  });
}

function renderIdeas(ideas) {
  if (!ideas.length) {
    ideasRoot.innerHTML = `<div class="empty">По выбранным фильтрам идеи не найдены.</div>`;
    return;
  }

  ideasRoot.innerHTML = ideas.map((idea, idx) => {
    const tags = Array.isArray(idea.tags) ? idea.tags : [];
    const symbol = idea.symbol || "";
    const direction = getDirectionRu(idea.direction || "NEUTRAL");
    const timeframe = idea.timeframe || "";
    const confidence = idea.confidence ?? "-";
    const summary = idea.summary_ru || "";

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

  document.querySelectorAll(".card").forEach((card, idx) => {
    card.addEventListener("click", () => openIdea(ideas[idx]));
  });
}

function applyFilters() {
  renderIdeas(getFilteredIdeas());
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

async function openIdea(idea) {
  activeIdea = idea;
  modalTitle.textContent = `${idea.symbol} — ${getDirectionRu(idea.direction)}`;
  modalSub.textContent = `${idea.timeframe} · Уверенность ${idea.confidence}%`;
  modal.classList.add("open");

  ensureChart();

  const res = await fetch(`/api/chart/${idea.symbol}/${idea.timeframe}`);
  const payload = await res.json();
  currentChartPayload = payload;

  candleSeries.setData(payload.candles);
  chart.timeScale().fitContent();

  analysisSmc.textContent = payload.overlays?.analysis?.smc_ru || "";
  analysisLiquidity.textContent = payload.overlays?.analysis?.liquidity_ru || "";
  analysisDivergence.textContent = payload.overlays?.analysis?.divergence_ru || "";
  analysisPattern.textContent = payload.overlays?.analysis?.pattern_ru || "";

  requestAnimationFrame(() => {
    requestAnimationFrame(() => drawOverlay());
  });
}

function closeModal() {
  modal.classList.remove("open");
  activeIdea = null;
}

async function load() {
  try {
    const res = await fetch("/ideas/market");
    const data = await res.json();
    allIdeas = normalizeIdeas(data);
    populateFilters(allIdeas);
    applyFilters();
  } catch (error) {
    ideasRoot.innerHTML = `<div class="empty">Не удалось загрузить идеи.</div>`;
    console.error(error);
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

load();
