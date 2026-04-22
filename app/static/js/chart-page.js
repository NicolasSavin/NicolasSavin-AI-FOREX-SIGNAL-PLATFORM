const ideasRoot = document.getElementById("ideas");
const statsGrid = document.getElementById("stats-grid");
const symbolFilter = document.getElementById("symbol-filter");
const timeframeFilter = document.getElementById("timeframe-filter");

const modal = document.getElementById("modal");
const modalTitle = document.getElementById("modal-title");
const modalSub = document.getElementById("modal-sub");
const closeModalBtn = document.getElementById("close-modal");

const analysisText = document.getElementById("analysis-text");
const tradingPlanText = document.getElementById("trading-plan-text");
const analysisSectionsRoot = document.getElementById("analysis-sections");

const chartSnapshotLayer = document.getElementById("chart-snapshot-layer");
const chartSnapshotImage = document.getElementById("chart-snapshot-image");
const chartLiveLayer = document.getElementById("chart-live-layer");
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
const detailMetrics = document.getElementById("detail-metrics");
const scenarioPrimary = document.getElementById("scenario-primary");
const scenarioSwing = document.getElementById("scenario-swing");
const scenarioInvalidation = document.getElementById("scenario-invalidation");

let allIdeas = [];
let activeIdea = null;
let chart = null;
let candleSeries = null;
let currentChartPayload = null;
let detailRequestId = 0;
let chartDisplayMode = "unavailable";
const CHART_REQUEST_TIMEOUT_MS = 5000;
const DEFAULT_PAIR_OPTIONS = ["EURUSD", "GBPUSD", "USDJPY"];
const DEFAULT_TIMEFRAME_OPTIONS = ["M15", "H1", "H4"];
const ENABLE_MOCK_IDEAS_ON_EMPTY = new URLSearchParams(window.location.search).get("ideas_mock") === "1";
const TEMP_MOCK_IDEAS = [
  {
    id: "mock-eurusd-h1-buy",
    symbol: "EURUSD",
    timeframe: "H1",
    direction: "buy",
    entry: 1.082,
    sl: 1.0795,
    tp: 1.0865,
    summary: "Технический откат к поддержке, ожидается восстановление импульса вверх.",
  },
  {
    id: "mock-gbpusd-m15-sell",
    symbol: "GBPUSD",
    timeframe: "M15",
    direction: "sell",
    entry: 1.266,
    sl: 1.2682,
    tp: 1.2624,
    summary: "Локальный пробой структуры вниз после ретеста сопротивления.",
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

function formatDateTime(value) {
  const text = normalizeWhitespace(value);
  if (!text) return "—";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date) + " UTC";
}

function formatSignedPercent(value) {
  if (value == null || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  const sign = num > 0 ? "+" : "";
  return `${sign}${num.toFixed(2)}%`;
}

function statusRu(value) {
  const key = String(value || "").toLowerCase();
  return {
    active: "Активна",
    updated: "Обновлена",
    triggered: "Триггер получен",
    archived: "В архиве",
    tp_hit: "TP достигнут",
    sl_hit: "SL достигнут",
    invalidated: "Инвалидирована",
  }[key] || "Без статуса";
}

function truncateText(value, limit = 92) {
  const text = normalizeWhitespace(value);
  if (!text || text.length <= limit) return text;
  return `${text.slice(0, limit - 1).trimEnd()}…`;
}

function buildShortText(idea) {
  const direct = normalizeWhitespace(idea?.short_text || idea?.shortText);
  if (direct) return direct;

  const scenarioDirect = normalizeWhitespace(idea?.short_scenario_ru || idea?.shortScenarioRu);
  if (scenarioDirect) return scenarioDirect;

  const base = normalizeWhitespace(idea?.summary_ru || idea?.summary || idea?.full_text || idea?.fullText);
  let compact = base.split(/(?<=[.!?])\s+/)[0] || base;
  compact = compact.split(/\s[—-]\s/)[0] || compact;
  compact = compact.replace(/[.!?]+$/, "").trim();

  const direction = getDirectionLabel(idea?.direction || idea?.bias);
  if (compact && !compact.toUpperCase().startsWith(direction)) {
    compact = `${direction} ${compact}`;
  }

  return truncateText(compact || `${direction} ждать подтверждение структуры`, 140);
}

function buildFullText(idea) {
  const detailSummary = normalizeWhitespace(idea?.detail_brief?.summary_narrative);
  if (detailSummary) return detailSummary;
  const direct = normalizeWhitespace(idea?.full_text || idea?.fullText || idea?.narrative);
  if (direct) return direct;
  return normalizeWhitespace(idea?.summary || idea?.summary_ru);
}

function buildDetailBrief(idea) {
  const existing = idea?.detail_brief;
  if (existing && typeof existing === "object") return existing;
  const summaryStructured = idea?.summary_structured || idea?.narrative_structured?.summary_structured || {};
  const tradePlanStructured = idea?.trade_plan_structured || idea?.narrative_structured?.trade_plan_structured || {};
  const marketStructureStructured = idea?.market_structure_structured || idea?.narrative_structured?.market_structure_structured || {};

  const entry = formatLevel(idea?.entry);
  const stop = formatLevel(idea?.stopLoss);
  const takeProfit = formatLevel(idea?.takeProfit);
  const supportedSections = [];
  const sections = [];

  const registerSection = (key, title, content, isProxy = false) => {
    const text = normalizeWhitespace(content);
    if (!text) return;
    supportedSections.push(key);
    sections.push({ key, title, content: text, is_proxy: isProxy });
  };

  registerSection("bias", "Bias", marketStructureStructured?.bias);
  registerSection("structure", "Структура", marketStructureStructured?.structure);
  registerSection("liquidity", "Ликвидность", marketStructureStructured?.liquidity);
  registerSection("zone", "Зона", marketStructureStructured?.zone);
  registerSection("confluence", "Конфлюенс", marketStructureStructured?.confluence);
  if (!sections.length) {
    registerSection("smc_ict", "SMC / ICT", idea?.analysis?.smc_ict_ru || idea?.summary_ru || idea?.summary);
    registerSection("chart_patterns", "Графические паттерны", idea?.analysis?.pattern_ru);
    registerSection("waves", "Волновой анализ", idea?.analysis?.waves_ru);
    registerSection("fundamental", "Фундаментал / макро", idea?.analysis?.fundamental_ru || idea?.ideaContext);
    registerSection("volume_profile", "Объёмы / Volume Profile", idea?.analysis?.volume_ru, /proxy/i.test(String(idea?.analysis?.volume_ru || "")));
    registerSection("cumdelta", "CumDelta / order flow", idea?.analysis?.cumdelta_ru || idea?.analysis?.cumulative_delta_ru, true);
    registerSection("liquidity", "Ликвидность", idea?.analysis?.liquidity_ru || idea?.target);
  }

  const marketUnavailable = idea?.current_price == null || String(idea?.data_status || "").toLowerCase() === "unavailable";
  return {
    header: {
      market_price: marketUnavailable ? "" : formatLevel(idea.current_price),
      daily_change: "",
      market_context: marketUnavailable ? "Нет актуальных рыночных данных" : normalizeWhitespace(idea?.ideaContext || idea?.context),
      bias: getDirectionRu(idea?.direction || idea?.bias),
      confidence: Number(idea?.confidence ?? 0),
      confluence_rating: Number(idea?.confidence ?? 0),
    },
    summary_narrative: normalizeWhitespace(summaryStructured?.situation) || buildFullText(idea),
    scenarios: {
      primary: normalizeWhitespace(summaryStructured?.action || tradePlanStructured?.entry_trigger || idea?.trigger || idea?.summary),
      swing: normalizeWhitespace(summaryStructured?.effect),
      invalidation: normalizeWhitespace(summaryStructured?.risk_note || tradePlanStructured?.invalidation || idea?.invalidation),
    },
    sections,
    trade_plan: {
      entry_zone: normalizeWhitespace(tradePlanStructured?.entry_zone) || entry,
      stop: normalizeWhitespace(tradePlanStructured?.stop_loss) || stop,
      take_profits: normalizeWhitespace(tradePlanStructured?.take_profit) || takeProfit,
      risk_reward: calculateRiskReward(idea),
      primary_scenario: normalizeWhitespace(summaryStructured?.action) || buildFullText(idea),
      alternative_scenario: normalizeWhitespace(summaryStructured?.risk_note || idea?.trade_plan?.alternative_scenario_ru),
    },
    structured_blocks: {
      summary: summaryStructured,
      trade_plan: tradePlanStructured,
      market_structure: marketStructureStructured,
    },
    supported_sections: supportedSections,
  };
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
    detail_brief: buildDetailBrief({
      ...idea,
      summary,
      summary_ru: summary,
      full_text: fullText,
      short_text: shortText,
    }),
    summary_structured: idea?.summary_structured || idea?.narrative_structured?.summary_structured || null,
    trade_plan_structured: idea?.trade_plan_structured || idea?.narrative_structured?.trade_plan_structured || null,
    market_structure_structured: idea?.market_structure_structured || idea?.narrative_structured?.market_structure_structured || null,
    narrative_structured: idea?.narrative_structured || null,
    entry: idea?.entry ?? idea?.entry_zone ?? "—",
    stopLoss: idea?.stopLoss ?? idea?.stop_loss ?? "—",
    takeProfit: idea?.takeProfit ?? idea?.take_profit ?? "—",
    chartData: idea?.chartData ?? idea?.chart_data ?? null,
    ideaContext: idea?.ideaContext ?? idea?.idea_context ?? idea?.idea_context_ru ?? idea?.context ?? idea?.rationale ?? summary,
    trigger: idea?.trigger ?? idea?.trigger_ru ?? (idea?.entry || idea?.entry_zone ? `Ждём подтверждение в зоне ${idea?.entry || idea?.entry_zone}.` : "Ждём подтверждение сценария по структуре."),
    invalidation: idea?.invalidation ?? idea?.invalidation_ru ?? idea?.trade_plan?.invalidation ?? "Идея отменяется при сломе исходной структуры.",
    target: idea?.target ?? idea?.target_ru ?? idea?.trade_plan?.target_1 ?? (idea?.takeProfit || idea?.take_profit ? `Ближайшая цель: ${idea?.takeProfit || idea?.take_profit}.` : "Цель будет уточняться после появления подтверждения."),
    tags: Array.isArray(idea?.tags) ? idea.tags : [symbol, timeframe, getDirectionRu(direction)],
    is_fallback: false,
    status: idea?.status || "active",
    final_status: idea?.final_status || null,
    update_summary: idea?.update_summary || idea?.change_summary || "",
    updated_at: idea?.updated_at || null,
    closed_at: idea?.closed_at || null,
    close_explanation: idea?.close_explanation || "",
    close_reason: idea?.close_reason || "",
    history: Array.isArray(idea?.history) ? idea.history : [],
  };
}

function normalizeIdeas(data) {
  if (Array.isArray(data)) return data.filter(Boolean).map(normalizeIdea);
  if (Array.isArray(data?.ideas) || Array.isArray(data?.archive)) {
    const active = Array.isArray(data?.ideas) ? data.ideas : [];
    const archived = Array.isArray(data?.archive) ? data.archive : [];
    return [...active, ...archived].filter(Boolean).map(normalizeIdea);
  }
  return [];
}

function computeAggregateStats(ideas) {
  const archived = ideas.filter((idea) => idea.status === "archived");
  const pnlValues = archived.map((idea) => Number(idea.pnl_percent)).filter((value) => Number.isFinite(value));
  const rrValues = archived.map((idea) => Number(idea.rr)).filter((value) => Number.isFinite(value));
  const wins = archived.filter((idea) => idea.result === "win").length;
  const total = archived.length;

  return {
    winrate: total ? (wins / total) * 100 : 0,
    trades: total,
    avgRr: rrValues.length ? rrValues.reduce((sum, value) => sum + value, 0) / rrValues.length : 0,
    avgPnl: pnlValues.length ? pnlValues.reduce((sum, value) => sum + value, 0) / pnlValues.length : 0,
  };
}

function renderStats(ideas, payloadStats) {
  if (!statsGrid) return;
  const fallback = computeAggregateStats(ideas);
  const stats = payloadStats || {};
  const cards = [
    ["Winrate", `${Number(stats.winrate ?? fallback.winrate).toFixed(2)}%`],
    ["Trades", String(stats.total_trades ?? fallback.trades)],
    ["Avg RR", Number(stats.avg_rr ?? fallback.avgRr).toFixed(2)],
    ["Avg PnL", formatSignedPercent(stats.avg_pnl ?? fallback.avgPnl)],
  ];
  statsGrid.innerHTML = cards
    .map(
      ([label, value]) => `
      <div class="stat-card">
        <div class="stat-label">${escapeHtml(label)}</div>
        <div class="stat-value">${escapeHtml(value)}</div>
      </div>
    `
    )
    .join("");
}

function populateFilters(ideas) {
  const symbols = [...new Set(ideas.map(x => x.symbol).filter(Boolean))];
  const timeframes = [...new Set(ideas.map(x => x.timeframe).filter(Boolean))];
  const symbolOptions = symbols.length ? symbols : DEFAULT_PAIR_OPTIONS;
  const timeframeOptions = timeframes.length ? timeframes : DEFAULT_TIMEFRAME_OPTIONS;
  const prevSymbol = String(symbolFilter.value || "ALL").toUpperCase();
  const prevTimeframe = String(timeframeFilter.value || "ALL").toUpperCase();

  symbolFilter.innerHTML = `<option value="ALL">Все пары</option>` +
    symbolOptions.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");

  timeframeFilter.innerHTML = `<option value="ALL">Все ТФ</option>` +
    timeframeOptions.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");

  symbolFilter.value = ["ALL", ...symbolOptions].includes(prevSymbol) ? prevSymbol : "ALL";
  timeframeFilter.value = ["ALL", ...timeframeOptions].includes(prevTimeframe) ? prevTimeframe : "ALL";
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
    ideasRoot.innerHTML = `<div class="empty">${escapeHtml(notice || "По выбранным фильтрам идеи не найдены.")}</div>`;
    return;
  }

  const cardsMarkup = ideas.map((idea, idx) => {
    const tags = Array.isArray(idea.tags) ? idea.tags : [];
    const symbol = idea.symbol || "";
    const direction = getDirectionRu(idea.direction || "NEUTRAL");
    const timeframe = idea.timeframe || "";
    const confidence = idea.confidence ?? "-";
    const summary = buildShortText(idea);
    const updateSummary = normalizeWhitespace(idea.update_summary);
    const statusLabel = idea.status === "archived" ? statusRu(idea.final_status || idea.status) : statusRu(idea.status);
    const updatedLabel = formatDateTime(idea.updated_at);
    const archivedStats = idea.status === "archived"
      ? `<div class="symbol">Результат: ${escapeHtml(String(idea.result || "—").toUpperCase())} · PnL: ${escapeHtml(formatSignedPercent(idea.pnl_percent))} · RR: ${escapeHtml(idea.rr != null ? Number(idea.rr).toFixed(2) : "—")} · Длительность: ${escapeHtml(idea.duration || "—")}</div>`
      : "";

    return `
      <div class="card" data-index="${idx}">
        <div class="card-head">
          <div>
            <div class="symbol">${escapeHtml(symbol)}</div>
            <div class="meta">${escapeHtml(direction)} · ${escapeHtml(timeframe)} · ${escapeHtml(String(confidence))}%</div>
            <div class="symbol">Статус: ${escapeHtml(statusLabel)} · Обновлено: ${escapeHtml(updatedLabel)}</div>
            ${archivedStats}
          </div>
        </div>
        <p class="summary">${escapeHtml(summary)}</p>
        ${updateSummary ? `<p class="summary">Обновление: ${escapeHtml(updateSummary)}</p>` : ""}
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
  const filteredIdeas = getFilteredIdeas();
  const emptyMessage = allIdeas.length
    ? "По выбранным фильтрам идеи не найдены."
    : "Идеи пока не сгенерированы.";
  renderIdeas(filteredIdeas, emptyMessage);
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

function renderMetricChips(detailBrief) {
  if (!detailMetrics) return;
  const header = detailBrief?.header || {};
  const marketPrice = header.market_price || "Нет актуальных рыночных данных";
  const metrics = [
    ["Цена", marketPrice],
    ["Изм. за день", header.daily_change || "Нет данных"],
    ["Bias", header.bias || "—"],
    ["Confidence", header.confidence != null && header.confidence !== "" ? `${header.confidence}%` : "—"],
    ["Confluence", header.confluence_rating != null && header.confluence_rating !== "" ? `${header.confluence_rating}%` : "—"],
    ["Контекст", header.market_context || "Контекст не передан"],
  ];
  detailMetrics.innerHTML = metrics.map(([label, value]) => `
    <div class="metric-chip">
      <div class="metric-chip-label">${escapeHtml(label)}</div>
      <div class="metric-chip-value">${escapeHtml(String(value))}</div>
    </div>
  `).join("");
}

function renderAnalysisSections(detailBrief) {
  if (!analysisSectionsRoot) return;
  const sections = Array.isArray(detailBrief?.sections) ? detailBrief.sections : [];
  if (!sections.length) {
    analysisSectionsRoot.innerHTML = "";
    analysisSectionsRoot.style.display = "none";
    return;
  }
  analysisSectionsRoot.style.display = "";
  analysisSectionsRoot.innerHTML = sections.map((section) => `
    <section class="analysis-block">
      <div class="analysis-title">${escapeHtml(section.title || section.key || "Секция")}</div>
      <p class="analysis-text">${escapeHtml(section.content || "")}</p>
    </section>
  `).join("");
}

function renderTradingPlan(detailBrief) {
  const plan = detailBrief?.trade_plan || {};
  const hasPlan = [plan.entry_zone, plan.stop, plan.take_profits, plan.primary_scenario, plan.alternative_scenario]
    .some((value) => normalizeWhitespace(value));
  if (!hasPlan) {
    setTextContent(tradingPlanText, "", "");
    const block = tradingPlanText?.closest(".analysis-block");
    if (block) block.style.display = "none";
    return;
  }
  const block = tradingPlanText?.closest(".analysis-block");
  if (block) block.style.display = "";
  const lines = [
    `Entry zone: ${plan.entry_zone || "—"}`,
    `Stop: ${plan.stop || "—"}`,
    `Take profits: ${plan.take_profits || "—"}`,
    `R:R: ${plan.risk_reward || "—"}`,
    `Основной сценарий: ${plan.primary_scenario || "—"}`,
    `Альтернативный сценарий: ${plan.alternative_scenario || "—"}`,
  ];
  setTextContent(tradingPlanText, lines.join("\n"), "Торговый план недоступен.");
}

function renderDetailText(idea) {
  const detailBrief = buildDetailBrief(idea);
  const fullText = normalizeWhitespace(detailBrief?.summary_narrative) || buildFullText(idea);
  setTextContent(ideaSummary, fullText, "");
  if (fullText) {
    setTextContent(analysisText, fullText, "");
    analysisText?.closest(".analysis-block")?.style?.removeProperty("display");
  } else {
    setTextContent(analysisText, "", "");
    const block = analysisText?.closest(".analysis-block");
    if (block) block.style.display = "none";
  }
  setTextContent(levelEntry, formatLevel(idea.entry));
  setTextContent(levelSl, formatLevel(idea.stopLoss));
  setTextContent(levelTp, formatLevel(idea.takeProfit));
  setTextContent(levelRr, calculateRiskReward(idea));
  renderMetricChips(detailBrief);
  setTextContent(scenarioPrimary, detailBrief?.scenarios?.primary, "");
  setTextContent(scenarioSwing, detailBrief?.scenarios?.swing, "");
  setTextContent(scenarioInvalidation, detailBrief?.scenarios?.invalidation, "");
  document.querySelectorAll(".scenario-card").forEach((card) => {
    const paragraph = card.querySelector("p");
    card.style.display = normalizeWhitespace(paragraph?.textContent) ? "" : "none";
  });
  renderTradingPlan(detailBrief);
  renderAnalysisSections(detailBrief);
  if (idea.current_price == null || String(idea.data_status || "").toLowerCase() === "unavailable") {
    setTextContent(scenarioPrimary, "", "");
    setTextContent(scenarioSwing, "", "");
    setTextContent(scenarioInvalidation, detailBrief?.scenarios?.invalidation, "");
    document.querySelectorAll(".scenario-card").forEach((card) => {
      const paragraph = card.querySelector("p");
      card.style.display = normalizeWhitespace(paragraph?.textContent) ? "" : "none";
    });
  }
  if (idea.status === "archived") {
    const closeText = normalizeWhitespace(idea.close_explanation) || "Сценарий закрыт и зафиксирован в архиве.";
    updateDetailStatus(`Финальный статус: ${statusRu(idea.final_status || idea.status)} · ${closeText} · Закрыто: ${formatDateTime(idea.closed_at)}`);
    return;
  }
  const updateText = normalizeWhitespace(idea.update_summary);
  if (updateText) {
    updateDetailStatus(`Статус: ${statusRu(idea.status)} · Обновлено: ${formatDateTime(idea.updated_at)} · ${updateText}`);
  }
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

function resetChartState() {
  currentChartPayload = null;
  if (chartSnapshotImage) {
    chartSnapshotImage.removeAttribute("src");
  }
  if (chart) {
    candleSeries.setData([]);
    chart.timeScale().fitContent();
  }
  const ctx = overlayCanvas?.getContext("2d");
  if (ctx) {
    ctx.clearRect(0, 0, overlayCanvas.width || overlayCanvas.clientWidth, overlayCanvas.height || overlayCanvas.clientHeight);
  }
}

function showChartPlaceholder(message) {
  chartPlaceholder.classList.add("open");
  chartPlaceholderText.textContent = message;
}

function hideChartPlaceholder() {
  chartPlaceholder.classList.remove("open");
  chartPlaceholderText.textContent = "Chart unavailable (data temporarily missing)";
}

function normalizeChartImageUrl(url) {
  const raw = normalizeWhitespace(url);
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw) || raw.startsWith("/")) return raw;
  if (raw.startsWith("static/")) return `/${raw}`;
  if (raw.startsWith("./")) return `/${raw.slice(2)}`;
  return `/static/${raw.replace(/^\/+/, "")}`;
}

function snapshotStatusRu(status) {
  const key = String(status || "").toLowerCase();
  const reason = {
    rate_limited: "Снапшот не подготовлен из-за лимита источника данных.",
    no_data: "Снапшот не подготовлен: по инструменту нет данных.",
    fetch_error: "Снапшот не подготовлен: ошибка при получении данных.",
    unavailable: "Снапшот временно недоступен.",
  }[key];
  if (reason) return `Chart unavailable (data temporarily missing) — ${reason}`;
  return "Chart unavailable (data temporarily missing)";
}

function setChartMode(mode) {
  chartDisplayMode = mode;
  if (mode === "snapshot") {
    chartSnapshotLayer?.classList.add("open");
    chartLiveLayer?.classList.remove("open");
    hideChartPlaceholder();
    return;
  }
  if (mode === "live") {
    chartSnapshotLayer?.classList.remove("open");
    chartLiveLayer?.classList.add("open");
    hideChartPlaceholder();
    return;
  }
  chartSnapshotLayer?.classList.remove("open");
  chartLiveLayer?.classList.remove("open");
}

function showSnapshotChart(imageUrl) {
  if (!chartSnapshotImage || !imageUrl) return false;
  chartSnapshotImage.src = imageUrl;
  setChartMode("snapshot");
  return true;
}

function showLiveChart(payload) {
  if (!payload?.candles?.length) return false;
  setChartMode("live");
  ensureChart();
  currentChartPayload = payload;
  candleSeries.setData(payload.candles);
  chart.timeScale().fitContent();
  requestAnimationFrame(() => {
    requestAnimationFrame(() => drawOverlay());
  });
  return true;
}

function showUnavailableChart(message) {
  setChartMode("unavailable");
  showChartPlaceholder(message || "Chart unavailable (data temporarily missing)");
}

function updateDetailStatus(message) {
  detailStatus.textContent = message;
}

function resizeChart() {
  if (!chart || chartDisplayMode !== "live") return;
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
  if (idea.chartData?.candles?.length) return idea.chartData;

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort('chart_timeout'), CHART_REQUEST_TIMEOUT_MS);

  try {
    const params = new URLSearchParams({ tf: idea.timeframe || 'H1' });
    const res = await fetch(`/api/chart/${encodeURIComponent(idea.symbol)}?${params.toString()}`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!res.ok) return null;
    const payload = await res.json();
    return payload?.candles?.length ? payload : null;
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
  const supported = Array.isArray(idea?.detail_brief?.supported_sections) ? idea.detail_brief.supported_sections.length : 0;
  modalSub.textContent = `${idea.timeframe} · Уверенность ${idea.confidence}% · аналитических секций: ${supported || "—"}`;
  modal.classList.add("open");

  renderDetailText(idea);
  updateDetailStatus("Загружаем desk-style detail-view идеи и проверяем доступность графика.");
  resetChartState();
  showUnavailableChart("Загружаем график для идеи...");

  const rawSnapshotUrl = idea.chartImageUrl || idea.chart_image || "";
  const snapshotUrl = normalizeChartImageUrl(rawSnapshotUrl);
  const snapshotStatus = idea.chartSnapshotStatus || idea.chart_snapshot_status || "";
  const liveFallbackMessage = snapshotStatusRu(snapshotStatus);

  if (snapshotUrl) {
    const snapshotLoaded = await new Promise((resolve) => {
      const img = chartSnapshotImage;
      if (!img) {
        resolve(false);
        return;
      }
      const done = (ok) => {
        img.removeEventListener("load", onLoad);
        img.removeEventListener("error", onError);
        resolve(ok);
      };
      const onLoad = () => done(true);
      const onError = () => done(false);
      img.addEventListener("load", onLoad, { once: true });
      img.addEventListener("error", onError, { once: true });
      showSnapshotChart(snapshotUrl);
    });

    if (requestId !== detailRequestId || activeIdea?.id !== idea.id) return;
    if (snapshotLoaded) {
      if (idea.status === "archived") {
        const closeText = normalizeWhitespace(idea.close_explanation) || "Сценарий закрыт и зафиксирован в архиве.";
        updateDetailStatus(`Финальный статус: ${statusRu(idea.final_status || idea.status)} · ${closeText} · Закрыто: ${formatDateTime(idea.closed_at)}`);
      } else {
        const updateText = normalizeWhitespace(idea.update_summary);
        updateDetailStatus(
          updateText
            ? `Статус: ${statusRu(idea.status)} · Обновлено: ${formatDateTime(idea.updated_at)} · ${updateText}`
            : "Detail-view заполнен: narrative, сценарии, trading plan и snapshot графика доступны."
        );
      }
      return;
    }
  }

  const payload = await resolveChartData(idea);
  if (requestId !== detailRequestId || activeIdea?.id !== idea.id) return;

  if (showLiveChart(payload)) {
    if (idea.status === "archived") {
      const closeText = normalizeWhitespace(idea.close_explanation) || "Сценарий закрыт и зафиксирован в архиве.";
      updateDetailStatus(`Финальный статус: ${statusRu(idea.final_status || idea.status)} · ${closeText} · Закрыто: ${formatDateTime(idea.closed_at)}`);
    } else {
      const updateText = normalizeWhitespace(idea.update_summary);
      updateDetailStatus(
        updateText
          ? `Статус: ${statusRu(idea.status)} · Обновлено: ${formatDateTime(idea.updated_at)} · ${updateText}`
          : "Detail-view заполнен: narrative, сценарии, trading plan и график доступны."
      );
    }
    return;
  }

  showUnavailableChart(liveFallbackMessage);
  if (idea.status === "archived") {
    const closeText = normalizeWhitespace(idea.close_explanation) || "Сценарий закрыт и зафиксирован в архиве.";
    updateDetailStatus(`Финальный статус: ${statusRu(idea.final_status || idea.status)} · ${closeText} · Закрыто: ${formatDateTime(idea.closed_at)}`);
  } else {
    const updateText = normalizeWhitespace(idea.update_summary);
    updateDetailStatus(
      updateText
        ? `Статус: ${statusRu(idea.status)} · Обновлено: ${formatDateTime(idea.updated_at)} · ${updateText}`
        : "График недоступен, но detail-view завершил загрузку: narrative, сценарии и trading plan показаны без чарта."
    );
  }
}

function closeModal() {
  modal.classList.remove("open");
  activeIdea = null;
  detailRequestId += 1;
  showUnavailableChart("Chart unavailable (data temporarily missing)");
}

async function load() {
  try {
    const res = await fetch("/ideas/market", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const normalizedIdeas = normalizeIdeas(data);
    if (!normalizedIdeas.length && ENABLE_MOCK_IDEAS_ON_EMPTY) {
      console.warn("Используем временный mock идей: активирован ideas_mock=1.");
      allIdeas = normalizeIdeas({ ideas: TEMP_MOCK_IDEAS });
    } else {
      allIdeas = normalizedIdeas;
    }
    populateFilters(allIdeas);
    applyFilters();
    renderStats(allIdeas, data?.statistics);
  } catch (error) {
    console.warn("Не удалось загрузить /ideas/market, synthetic fallback отключён.", error);
    allIdeas = [];
    populateFilters(allIdeas);
    renderIdeas(allIdeas, "Источник идей временно недоступен. Нет актуальных рыночных данных.");
    renderStats(allIdeas, null);
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
