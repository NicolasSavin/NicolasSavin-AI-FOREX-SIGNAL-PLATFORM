const ideasRoot = document.getElementById("ideas");
const statsGrid = document.getElementById("stats-grid");
const symbolFilter = document.getElementById("symbol-filter");
const timeframeFilter = document.getElementById("timeframe-filter");

const modal = document.getElementById("modal");
const modalCard = document.getElementById("modal-card");
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
const registeredChartPlugins = [];
let initialIdeasSyncCompleted = false;
let ideasPollInFlight = false;
let lastNotificationAt = 0;
const previousIdeasById = new Map();
const renderedIdeaSignatureById = new Map();
const lastValidChartByIdeaId = new Map();
const IDEAS_POLL_INTERVAL_MS = 15000;
const NOTIFICATION_COOLDOWN_MS = 1500;
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
  if (["bullish", "buy", "long"].includes(raw)) return "ПОКУПКА";
  if (["bearish", "sell", "short"].includes(raw)) return "ПРОДАЖА";
  return "НЕЙТРАЛЬНО";
}

function getDirectionTone(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (["bullish", "buy", "long"].includes(raw)) return "bullish";
  if (["bearish", "sell", "short"].includes(raw)) return "bearish";
  return "neutral";
}

function getSignalTone(idea) {
  const signal = normalizeSignalValue(idea?.final_signal || idea?.signal || "");
  if (signal === "BUY") return "bullish";
  if (signal === "SELL") return "bearish";
  if (signal === "WAIT") return "neutral";
  return getDirectionTone(idea?.direction || idea?.bias || "neutral");
}

function getSignalLabel(idea) {
  const signal = normalizeSignalValue(idea?.final_signal || idea?.signal || "");
  if (signal === "BUY") return "ПОКУПКА";
  if (signal === "SELL") return "ПРОДАЖА";
  if (signal === "WAIT") return "ОЖИДАНИЕ";
  return getDirectionLabel(idea?.direction || idea?.bias || "neutral");
}

function normalizeSignalValue(value) {
  const signal = normalizeWhitespace(value).toUpperCase();
  if (["ПОКУПКА", "BUY", "LONG", "BULLISH"].includes(signal)) return "BUY";
  if (["ПРОДАЖА", "SELL", "SHORT", "BEARISH"].includes(signal)) return "SELL";
  if (["ОЖИДАНИЕ", "WAIT", "WAITING", "HOLD", "NEUTRAL"].includes(signal)) return "WAIT";
  return signal;
}

function normalizeWhitespace(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function isCompactTechnicalSummary(value) {
  const text = normalizeWhitespace(value).toLowerCase();
  if (!text) return false;
  return /h4\s*=\s*(bullish|bearish|neutral|нет данных).+h1\s*=\s*(bullish|bearish|neutral|нет данных).+m15\s*=\s*(bullish|bearish|neutral|нет данных).+итог:\s*(buy|sell|wait)/i.test(text);
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

function getStructuredNarrativeBlocks(idea) {
  const summaryStructured = idea?.summary_structured || idea?.narrative_structured?.summary_structured || {};
  const tradePlanStructured = idea?.trade_plan_structured || idea?.narrative_structured?.trade_plan_structured || {};
  const marketStructureStructured = idea?.market_structure_structured || idea?.narrative_structured?.market_structure_structured || {};
  return {
    summaryStructured,
    tradePlanStructured,
    marketStructureStructured,
  };
}

function buildShortText(idea) {
  if (idea?.combined) {
    const htf = normalizeWhitespace(idea?.htf_bias_summary);
    const mtf = normalizeWhitespace(idea?.mtf_structure_summary);
    const ltf = normalizeWhitespace(idea?.ltf_trigger_summary);
    const finalSignal = String(idea?.final_signal || idea?.signal || "wait").toUpperCase();
    const lines = [htf, mtf, ltf].filter(Boolean);
    const combinedText = lines.length ? `${lines.join(" · ")} · Итог: ${finalSignal}` : "";
    if (combinedText) return truncateText(combinedText, 140);
  }
  const { summaryStructured } = getStructuredNarrativeBlocks(idea);
  const structuredShort = normalizeWhitespace(summaryStructured?.signal || summaryStructured?.action);
  if (structuredShort) return truncateText(structuredShort, 140);

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
  const thesis = normalizeWhitespace(idea?.idea_thesis || idea?.ideaThesis);
  if (isRenderableNarrative(thesis) && !isCompactTechnicalSummary(thesis)) return thesis;
  const unified = normalizeWhitespace(idea?.unified_narrative);
  if (isRenderableNarrative(unified) && !isCompactTechnicalSummary(unified)) return unified;
  const legacyNarrative = normalizeWhitespace(idea?.legacy_narrative || idea?.legacyNarrative);
  if (isRenderableNarrative(legacyNarrative) && !isCompactTechnicalSummary(legacyNarrative)) return legacyNarrative;
  const legacyNarrativeCandidates = [
    idea?.full_text,
    idea?.fullText,
    idea?.narrative,
    idea?.description_ru,
    idea?.reason_ru,
    idea?.rationale,
    idea?.idea_context_ru,
    idea?.ideaContext,
  ];
  for (const candidate of legacyNarrativeCandidates) {
    const text = normalizeWhitespace(candidate);
    if (isRenderableNarrative(text) && !isCompactTechnicalSummary(text)) return text;
  }
  const { summaryStructured, tradePlanStructured } = getStructuredNarrativeBlocks(idea);
  const structuredText = normalizeWhitespace(summaryStructured?.situation) || normalizeWhitespace(tradePlanStructured?.entry_trigger);
  if (isRenderableNarrative(structuredText) && !isCompactTechnicalSummary(structuredText)) return structuredText;
  const detailSummary = normalizeWhitespace(idea?.detail_brief?.summary_narrative);
  if (isRenderableNarrative(detailSummary) && !isCompactTechnicalSummary(detailSummary)) return detailSummary;
  const legacySummary = normalizeWhitespace(idea?.summary || idea?.summary_ru);
  if (isRenderableNarrative(legacySummary)) return legacySummary;
  return "Подробное описание пока не получено. Дождитесь обновления идеи перед входом в сделку.";
}

function isRenderableNarrative(value) {
  const text = normalizeWhitespace(value).toLowerCase();
  if (!text) return false;
  const blocked = ["none", "fallback", "idea_created", "status created", "debug", "schema", "payload", "статус created"];
  if (blocked.some((token) => text.includes(token))) return false;
  if (/^[a-z]{3,8}\s*[/-]?\s*[a-z]{3,8}\s+[mhdw]\d{1,2}\s*:\s*(bullish|bearish|neutral).*(статус|status)\s+\w+/.test(text)) return false;
  if (text.includes("ситуация:") && text.includes("причина:") && text.includes("следствие:") && text.includes("действие:")) return false;
  return true;
}

function isMeaningfulUpdate(idea) {
  if (!idea || idea.status === "archived") return false;
  if (Boolean(idea.has_meaningful_update)) return true;
  const reason = normalizeWhitespace(idea.meaningful_update_reason || "").toLowerCase();
  if (!reason || ["idea_created", "fallback", "status created", "debug"].includes(reason)) return false;
  return true;
}

function buildDetailBrief(idea) {
  const existing = idea?.detail_brief;
  if (existing && typeof existing === "object") return existing;
  const { summaryStructured, tradePlanStructured, marketStructureStructured } = getStructuredNarrativeBlocks(idea);

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

  registerSection("bias", "Уклон", marketStructureStructured?.bias);
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
  const timeframeSections = [];
  if (idea?.combined && idea?.timeframe_ideas && typeof idea.timeframe_ideas === "object") {
    const tfOrder = ["H4", "H1", "M15"];
    tfOrder.forEach((tf) => {
      const tfIdea = idea.timeframe_ideas?.[tf];
      if (!tfIdea || typeof tfIdea !== "object") return;
      const tfSummary = normalizeWhitespace(tfIdea?.short_text || tfIdea?.summary_ru || tfIdea?.summary);
      if (!tfSummary) return;
      timeframeSections.push({
        key: `tf_${tf.toLowerCase()}`,
        title: `${tf} блок`,
        content: tfSummary,
        is_proxy: false,
      });
    });
  }
  return {
    header: {
      market_price: marketUnavailable ? "" : formatLevel(idea.current_price),
      daily_change: "",
      market_context: marketUnavailable ? "Нет актуальных рыночных данных" : normalizeWhitespace(idea?.ideaContext || idea?.context),
      bias: getDirectionRu(idea?.direction || idea?.bias),
      confidence: Number(idea?.confidence ?? 0),
      confluence_rating: Number(idea?.confidence ?? 0),
      htf: normalizeWhitespace(idea?.htf_bias_summary),
      mtf: normalizeWhitespace(idea?.mtf_structure_summary),
      ltf: normalizeWhitespace(idea?.ltf_trigger_summary),
    },
    summary_narrative: buildFullText(idea) || normalizeWhitespace(summaryStructured?.situation),
    scenarios: {
      primary: normalizeWhitespace(summaryStructured?.action || tradePlanStructured?.entry_trigger || idea?.trigger || idea?.summary),
      swing: normalizeWhitespace(summaryStructured?.effect),
      invalidation: normalizeWhitespace(summaryStructured?.risk_note || tradePlanStructured?.invalidation || idea?.invalidation),
    },
    sections: timeframeSections.length ? [...timeframeSections, ...sections] : sections,
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

  const normalizedChartImageUrl = normalizeChartImageUrl(idea?.chartImageUrl || idea?.chart_image || "");
  const normalizedChartData = idea?.chartData ?? idea?.chart_data ?? null;
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
    idea_thesis: normalizeWhitespace(idea?.idea_thesis || idea?.ideaThesis) || fullText,
    unified_narrative: normalizeWhitespace(idea?.unified_narrative) || fullText,
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
    chartData: normalizedChartData,
    chartImageUrl: normalizedChartImageUrl,
    chart_image: normalizedChartImageUrl,
    chartSnapshotStatus: idea?.chartSnapshotStatus || idea?.chart_snapshot_status || "",
    chart_snapshot_status: idea?.chart_snapshot_status || idea?.chartSnapshotStatus || "",
    chart_overlays: idea?.chart_overlays
      ?? idea?.chartOverlays
      ?? idea?.chart_data?.chart_overlays
      ?? idea?.chartData?.chart_overlays
      ?? null,
    ideaContext: idea?.ideaContext ?? idea?.idea_context ?? idea?.idea_context_ru ?? idea?.context ?? idea?.rationale ?? summary,
    trigger: idea?.trigger ?? idea?.trigger_ru ?? (idea?.entry || idea?.entry_zone ? `Ждём подтверждение в зоне ${idea?.entry || idea?.entry_zone}.` : "Ждём подтверждение сценария по структуре."),
    invalidation: idea?.invalidation ?? idea?.invalidation_ru ?? idea?.trade_plan?.invalidation ?? "Идея отменяется при сломе исходной структуры.",
    target: idea?.target ?? idea?.target_ru ?? idea?.trade_plan?.target_1 ?? (idea?.takeProfit || idea?.take_profit ? `Ближайшая цель: ${idea?.takeProfit || idea?.take_profit}.` : "Цель будет уточняться после появления подтверждения."),
    tags: Array.isArray(idea?.tags) ? idea.tags : [symbol, timeframe, getDirectionRu(direction)],
    is_fallback: false,
    status: idea?.status || "active",
    final_status: idea?.final_status || null,
    update_summary: idea?.update_summary || idea?.change_summary || "",
    update_reason: idea?.update_reason || "",
    updated_at: idea?.updated_at || null,
    meaningful_updated_at: idea?.meaningful_updated_at || idea?.updated_at || null,
    meaningful_update_reason: idea?.meaningful_update_reason || "",
    has_meaningful_update: Boolean(idea?.has_meaningful_update),
    internal_refresh_at: idea?.internal_refresh_at || null,
    closed_at: idea?.closed_at || null,
    close_explanation: idea?.close_explanation || "",
    close_reason: idea?.close_reason || "",
    history: Array.isArray(idea?.history) ? idea.history : [],
  };
}

function hasVisibleIdeaText(idea) {
  const candidates = [
    idea?.summary,
    idea?.summary_ru,
    idea?.short_text,
    idea?.shortText,
    idea?.full_text,
    idea?.fullText,
    idea?.idea_thesis,
    idea?.unified_narrative,
  ];
  return candidates.some((value) => isRenderableNarrative(value));
}

function isTechnicalPlaceholderIdea(idea) {
  const symbol = normalizeWhitespace(idea?.symbol || idea?.pair || "").toUpperCase();
  const signal = normalizeSignalValue(idea?.final_signal || idea?.signal || "");
  const hasSummary = hasVisibleIdeaText(idea);
  if (!symbol && !signal && !hasSummary) return true;

  const markerText = normalizeWhitespace([
    idea?.summary,
    idea?.summary_ru,
    idea?.full_text,
    idea?.fullText,
    idea?.short_text,
    idea?.shortText,
  ].filter(Boolean).join(" ")).toLowerCase();
  if (!markerText) return false;

  return [
    "debug",
    "schema",
    "payload",
    "no data",
    "нет данных",
    "placeholder",
    "technical placeholder",
  ].some((token) => markerText.includes(token));
}

function isVisibleIdea(idea) {
  const symbol = normalizeWhitespace(idea?.symbol || idea?.pair || "").toUpperCase();
  const signal = normalizeSignalValue(idea?.final_signal || idea?.signal || "");
  const visibleSignals = new Set(["ПОКУПКА", "ПРОДАЖА", "ОЖИДАНИЕ", "BUY", "SELL", "WAIT"]);
  const hasValidSignal = visibleSignals.has(signal);
  const hasText = hasVisibleIdeaText(idea);

  if (isTechnicalPlaceholderIdea(idea)) return false;
  return Boolean(symbol) && hasText && hasValidSignal;
}

function registerChartPlugin(plugin) {
  if (!plugin?.id || typeof plugin?.afterDraw !== "function") return;
  const exists = registeredChartPlugins.some(item => item.id === plugin.id);
  if (!exists) registeredChartPlugins.push(plugin);
}

function normalizeIdeas(data) {
  const normalizeList = (items) => items.filter(Boolean).map(normalizeIdea).map((idea) => ({
    ...idea,
    direction: getSignalTone(idea),
    bias: getSignalTone(idea),
  }));
  if (Array.isArray(data)) return normalizeList(data);
  if (Array.isArray(data?.ideas) || Array.isArray(data?.archive)) {
    const active = Array.isArray(data?.ideas) ? data.ideas : [];
    const archived = Array.isArray(data?.archive) ? data.archive : [];
    return normalizeList([...active, ...archived]);
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
    ["Винрейт", `${Number(stats.winrate ?? fallback.winrate).toFixed(2)}%`],
    ["Сделки", String(stats.total_trades ?? fallback.trades)],
    ["Средний R/R", Number(stats.avg_rr ?? fallback.avgRr).toFixed(2)],
    ["Средний PnL", formatSignedPercent(stats.avg_pnl ?? fallback.avgPnl)],
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

function buildIdeaCardMarkup(idea) {
  const tags = Array.isArray(idea.tags) ? idea.tags : [];
  const symbol = idea.symbol || "";
  const signalLabel = getSignalLabel(idea);
  const signalTone = getSignalTone(idea);
  const timeframesAvailable = Array.isArray(idea?.timeframes_available) ? idea.timeframes_available : [];
  const confidence = Math.round(Number(idea?.final_confidence ?? idea?.confidence ?? 0)) || "-";
  const mainNarrative = buildFullText(idea);
  const h4 = normalizeWhitespace(idea?.htf_bias_summary) || "H4: нет данных";
  const h1 = normalizeWhitespace(idea?.mtf_structure_summary) || "H1: нет данных";
  const m15 = normalizeWhitespace(idea?.ltf_trigger_summary) || "M15: нет данных";

  return `
    <div class="card-head">
      <div class="card-head-main">
        <div class="symbol">${escapeHtml(symbol)}</div>
        <div class="meta">Итоговый сигнал: ${escapeHtml(signalLabel)} · Уверенность: ${escapeHtml(String(confidence))}%</div>
      </div>
      <div class="signal-badge signal-badge-${escapeHtml(signalTone)}">${escapeHtml(signalLabel)}</div>
    </div>
    <div class="timeframe-tags">
      ${(timeframesAvailable.length ? timeframesAvailable : ["H4", "H1", "M15"]).map((tf) => `<span class="tag tf-tag">${escapeHtml(tf)}</span>`).join("")}
    </div>
    <p class="summary summary-main">${escapeHtml(mainNarrative)}</p>
    <div class="mtf-strip">
      <div class="strip-chip strip-chip-h4">${escapeHtml(h4)}</div>
      <div class="strip-chip strip-chip-h1">${escapeHtml(h1)}</div>
      <div class="strip-chip strip-chip-m15">${escapeHtml(m15)}</div>
    </div>
    <div class="levels-inline">
      <div class="level-pill level-pill-entry">Entry: ${escapeHtml(formatLevel(idea?.entry))}</div>
      <div class="level-pill level-pill-sl">SL: ${escapeHtml(formatLevel(idea?.stopLoss))}</div>
      <div class="level-pill level-pill-tp">TP: ${escapeHtml(formatLevel(idea?.takeProfit))}</div>
      <div class="level-pill level-pill-rr">RR: ${escapeHtml(calculateRiskReward(idea))}</div>
    </div>
    <div class="tags">
      ${tags.filter((tag) => !["created", "status", "debug"].includes(String(tag).toLowerCase())).map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}
    </div>
  `;
}

function getIdeaDiffSignature(idea) {
  return JSON.stringify({
    chartImageUrl: idea?.chartImageUrl || idea?.chart_image || null,
    chart_overlays: idea?.chart_overlays || null,
    unified_narrative: normalizeWhitespace(idea?.unified_narrative),
    idea_thesis: normalizeWhitespace(idea?.idea_thesis || idea?.ideaThesis),
    confidence: Number(idea?.confidence ?? 0),
    entry: formatLevel(idea?.entry),
    stopLoss: formatLevel(idea?.stopLoss),
    takeProfit: formatLevel(idea?.takeProfit),
    signal: normalizeWhitespace(idea?.signal || idea?.direction || idea?.bias),
    risk_note: normalizeWhitespace(idea?.risk_note || idea?.invalidation || idea?.trade_plan?.invalidation),
    update_reason: normalizeWhitespace(idea?.update_reason || idea?.update_summary || idea?.change_summary),
  });
}

function getIdeaVisualSignature(idea) {
  return JSON.stringify({
    chartImageUrl: idea?.chartImageUrl || idea?.chart_image || null,
    chart_overlays: idea?.chart_overlays || null,
    unified_narrative: normalizeWhitespace(idea?.unified_narrative),
    idea_thesis: normalizeWhitespace(idea?.idea_thesis || idea?.ideaThesis),
    confidence: Number(idea?.confidence ?? 0),
    entry: formatLevel(idea?.entry),
    stopLoss: formatLevel(idea?.stopLoss),
    takeProfit: formatLevel(idea?.takeProfit),
    signal: normalizeWhitespace(idea?.signal || idea?.direction || idea?.bias),
  });
}

function hasMeaningfulIdeaChange(prevIdea, nextIdea) {
  if (!prevIdea) return true;
  return getIdeaDiffSignature(prevIdea) !== getIdeaDiffSignature(nextIdea);
}

function hasMeaningfulVisualChange(prevIdea, nextIdea) {
  if (!prevIdea) return true;
  return getIdeaVisualSignature(prevIdea) !== getIdeaVisualSignature(nextIdea);
}

function playIdeaNotification() {
  const now = Date.now();
  if (now - lastNotificationAt < NOTIFICATION_COOLDOWN_MS) return;
  lastNotificationAt = now;

  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();

    oscillator.type = "triangle";
    oscillator.frequency.value = 920;
    gain.gain.value = 0.0001;

    oscillator.connect(gain);
    gain.connect(ctx.destination);

    const start = ctx.currentTime;
    const duration = 0.16;
    gain.gain.exponentialRampToValueAtTime(0.07, start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);

    oscillator.start(start);
    oscillator.stop(start + duration);
    oscillator.onended = () => ctx.close().catch(() => {});
  } catch (error) {
    console.debug("Не удалось воспроизвести уведомление по идее.", error);
  }
}

function flashIdeaCard(card) {
  if (!card) return;
  card.classList.remove("idea-card-flash");
  void card.offsetWidth;
  card.classList.add("idea-card-flash");
}

function renderIdeas(ideas, notice = "") {
  const existingCards = new Map(
    Array.from(ideasRoot.querySelectorAll(".card[data-idea-id]"))
      .map((card) => [card.dataset.ideaId, card])
  );

  const existingNotice = ideasRoot.querySelector('[data-role="ideas-notice"]');
  if (notice) {
    if (existingNotice) {
      existingNotice.textContent = notice;
    } else {
      const noticeNode = document.createElement("div");
      noticeNode.className = "empty";
      noticeNode.dataset.role = "ideas-notice";
      noticeNode.textContent = notice;
      ideasRoot.prepend(noticeNode);
    }
  } else if (existingNotice) {
    existingNotice.remove();
  }

  if (!ideas.length) {
    ideasRoot.innerHTML = `<div class="empty">${escapeHtml(notice || "По выбранным фильтрам идеи не найдены.")}</div>`;
    renderedIdeaSignatureById.clear();
    return;
  }

  let insertionPoint = ideasRoot.querySelector('[data-role="ideas-notice"]');
  for (const idea of ideas) {
    const ideaId = String(idea?.id || "");
    if (!ideaId) continue;

    let card = existingCards.get(ideaId);
    if (!card) {
      card = document.createElement("div");
      card.className = "card";
      card.dataset.ideaId = ideaId;
      card.addEventListener("click", () => {
        const currentIdea = allIdeas.find((item) => String(item?.id) === ideaId);
        if (currentIdea) openIdea(currentIdea);
      });
      const insertAfter = insertionPoint ? insertionPoint.nextSibling : ideasRoot.firstChild;
      ideasRoot.insertBefore(card, insertAfter);
    }

    const signature = getIdeaDiffSignature(idea);
    if (renderedIdeaSignatureById.get(ideaId) !== signature) {
      card.innerHTML = buildIdeaCardMarkup(idea);
      renderedIdeaSignatureById.set(ideaId, signature);
    }
    card.dataset.direction = getSignalTone(idea);

    const targetPosition = insertionPoint ? insertionPoint.nextSibling : ideasRoot.firstChild;
    if (card !== targetPosition) {
      ideasRoot.insertBefore(card, targetPosition);
    }

    insertionPoint = card;
    existingCards.delete(ideaId);
  }

  for (const [ideaId, node] of existingCards.entries()) {
    node.remove();
    renderedIdeaSignatureById.delete(ideaId);
  }
}

function applyFilters() {
  const filteredIdeas = getFilteredIdeas();
  const emptyMessage = allIdeas.length
    ? "По выбранным фильтрам идеи не найдены."
    : "Идеи пока не сгенерированы.";
  renderIdeas(filteredIdeas, filteredIdeas.length ? "" : emptyMessage);
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
  const metrics = [];
  if (header.market_price) metrics.push(["Цена", header.market_price]);
  if (header.bias) metrics.push(["Уклон", header.bias]);
  if (header.confidence != null && header.confidence !== "") metrics.push(["Уверенность", `${header.confidence}%`]);
  if (header.confluence_rating != null && header.confluence_rating !== "") metrics.push(["Согласованность", `${header.confluence_rating}%`]);
  if (header.market_context) metrics.push(["Контекст", header.market_context]);
  if (header.htf) metrics.push(["HTF", header.htf]);
  if (header.mtf) metrics.push(["MTF", header.mtf]);
  if (header.ltf) metrics.push(["LTF", header.ltf]);
  if (!metrics.length) {
    detailMetrics.innerHTML = "";
    return;
  }
  detailMetrics.innerHTML = metrics.map(([label, value]) => `
    <div class="metric-chip">
      <div class="metric-chip-label">${escapeHtml(label)}</div>
      <div class="metric-chip-value">${escapeHtml(String(value))}</div>
    </div>
  `).join("");
}

function renderAnalysisSections(detailBrief) {
  if (!analysisSectionsRoot) return;
  analysisSectionsRoot.innerHTML = "";
  analysisSectionsRoot.style.display = "none";
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
  if (block) block.style.display = "none";
  setTextContent(tradingPlanText, "", "");
}

function renderDetailText(idea) {
  const detailBrief = buildDetailBrief(idea);
  const fullText = buildFullText(idea) || normalizeWhitespace(detailBrief?.summary_narrative);
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
  const primaryScenario = idea?.combined
    ? normalizeWhitespace(idea?.htf_bias_summary) || detailBrief?.scenarios?.primary
    : detailBrief?.scenarios?.primary;
  const swingScenario = idea?.combined
    ? normalizeWhitespace(idea?.mtf_structure_summary) || detailBrief?.scenarios?.swing
    : detailBrief?.scenarios?.swing;
  const invalidationScenario = idea?.combined
    ? normalizeWhitespace(idea?.ltf_trigger_summary) || detailBrief?.scenarios?.invalidation
    : detailBrief?.scenarios?.invalidation;
  setTextContent(scenarioPrimary, primaryScenario, "");
  setTextContent(scenarioSwing, swingScenario, "");
  setTextContent(scenarioInvalidation, invalidationScenario, "");
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
  const updateText = isMeaningfulUpdate(idea) ? normalizeWhitespace(idea.update_reason || idea.update_summary) : "";
  if (updateText) {
    updateDetailStatus(`Статус: ${statusRu(idea.status)} · Обновлено: ${formatDateTime(idea.meaningful_updated_at)} · ${updateText}`);
  } else {
    updateDetailStatus(`Статус: ${statusRu(idea.status)}`);
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

function resetChartState({ keepSnapshot = true } = {}) {
  currentChartPayload = null;
  if (chartSnapshotImage && !keepSnapshot) {
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
  chartPlaceholderText.textContent = "График недоступен";
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
  if (reason) return `График недоступен — ${reason}`;
  return "График недоступен";
}

function hasCandles(payload) {
  const normalized = normalizeChartPayload(payload);
  return Boolean(normalized?.candles?.length);
}

function normalizeChartPayload(payload) {
  if (!payload || typeof payload !== "object") return { candles: [] };
  const candles = normalizeAndValidateCandles(payload.candles);
  return { ...payload, candles };
}

function normalizeAndValidateCandles(rawCandles) {
  if (!Array.isArray(rawCandles)) return [];
  const normalized = [];
  for (const candle of rawCandles) {
    if (!candle || typeof candle !== "object") continue;
    const timeRaw = Number(candle.time ?? candle.timestamp);
    const openRaw = Number(candle.open);
    const highRaw = Number(candle.high);
    const lowRaw = Number(candle.low);
    const closeRaw = Number(candle.close);
    if (![timeRaw, openRaw, highRaw, lowRaw, closeRaw].every(Number.isFinite)) continue;
    const lowerBound = Math.min(openRaw, closeRaw);
    const upperBound = Math.max(openRaw, closeRaw);
    const low = Math.min(lowRaw, lowerBound);
    const high = Math.max(highRaw, upperBound);
    if (low > high) continue;
    normalized.push({ time: Math.trunc(timeRaw), open: openRaw, high, low, close: closeRaw });
  }
  normalized.sort((a, b) => a.time - b.time);
  return normalized;
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

function hasRenderableSnapshot(idea) {
  return Boolean(normalizeChartImageUrl(idea?.chartImageUrl || idea?.chart_image || ""));
}

function hasRenderableCandles(idea) {
  return hasCandles(idea?.chartData) || hasCandles(idea?.chart_data);
}

function hasMeaningfulChartOverlays(overlays) {
  if (!overlays || typeof overlays !== "object") return false;
  const keys = ["order_blocks", "liquidity", "fvg", "structure_levels", "patterns", "zones", "levels"];
  return keys.some((key) => Array.isArray(overlays[key]) && overlays[key].length > 0);
}

function mergeWithPreviousIdeaState(nextIdea, prevIdea) {
  if (!prevIdea || typeof prevIdea !== "object") return nextIdea;
  const nextSnapshot = normalizeChartImageUrl(nextIdea?.chartImageUrl || nextIdea?.chart_image || "");
  const prevSnapshot = normalizeChartImageUrl(prevIdea?.chartImageUrl || prevIdea?.chart_image || "");
  const mergedSnapshot = nextSnapshot || prevSnapshot;
  const nextChartData = hasRenderableCandles(nextIdea) ? (nextIdea.chartData || nextIdea.chart_data) : null;
  const prevChartData = hasRenderableCandles(prevIdea) ? (prevIdea.chartData || prevIdea.chart_data) : null;
  const nextOverlays = nextIdea?.chart_overlays;
  const prevOverlays = prevIdea?.chart_overlays;
  const mergedOverlays = hasMeaningfulChartOverlays(nextOverlays) ? nextOverlays : prevOverlays || nextOverlays || null;

  return {
    ...nextIdea,
    chartImageUrl: mergedSnapshot,
    chart_image: mergedSnapshot,
    chartData: nextChartData || prevChartData || nextIdea.chartData || null,
    chart_overlays: mergedOverlays,
  };
}

function showLiveChart(payload) {
  const normalizedPayload = normalizeChartPayload(payload);
  if (!hasCandles(normalizedPayload)) return false;
  setChartMode("live");
  ensureChart();
  currentChartPayload = normalizedPayload;
  candleSeries.setData(normalizedPayload.candles);
  chart.timeScale().fitContent();
  requestAnimationFrame(() => {
    requestAnimationFrame(() => drawOverlay());
  });
  return true;
}

function showUnavailableChart(message) {
  setChartMode("unavailable");
  showChartPlaceholder(message || "График недоступен");
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

  return {
    candles,
    zones,
    levels,
    arrows,
    labels,
    smcOverlays: normalizeSmcOverlays(payload),
  };
}

function toFiniteNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function normalizeSmcOverlays(payload) {
  const base = payload?.chart_overlays
    ?? payload?.chartOverlays
    ?? payload?.overlays?.chart_overlays
    ?? {};

  const asZone = (item, fallbackLabel = "Zone") => {
    const from = toFiniteNumber(item?.from ?? item?.low);
    const to = toFiniteNumber(item?.to ?? item?.high);
    if (from == null || to == null) return null;
    return {
      from: Math.min(from, to),
      to: Math.max(from, to),
      type: String(item?.type || "").toLowerCase(),
      label: normalizeWhitespace(item?.label || fallbackLabel),
      start_index: Number.isFinite(Number(item?.start_index)) ? Number(item.start_index) : null,
      end_index: Number.isFinite(Number(item?.end_index)) ? Number(item.end_index) : null,
    };
  };

  const orderBlocksRaw = Array.isArray(base?.order_blocks) ? base.order_blocks : [];
  const fvgRaw = Array.isArray(base?.fvg) ? base.fvg : [];
  const liquidityRaw = Array.isArray(base?.liquidity) ? base.liquidity : [];
  const structureRaw = Array.isArray(base?.structure_levels) ? base.structure_levels : Array.isArray(base?.structure) ? base.structure : [];
  const patternsRaw = Array.isArray(base?.patterns) ? base.patterns : [];
  const genericZones = Array.isArray(base?.zones) ? base.zones : [];
  const genericLevels = Array.isArray(base?.levels) ? base.levels : [];

  const orderBlocks = orderBlocksRaw.map((item) => asZone(item, "Order Block")).filter(Boolean);
  const fvg = fvgRaw.map((item) => asZone(item, "FVG")).filter(Boolean);

  genericZones.forEach((item) => {
    const normalized = asZone(item, "Zone");
    if (!normalized) return;
    const t = String(item?.type || item?.label || "").toLowerCase();
    if (t.includes("fvg") || t.includes("imbalance")) {
      fvg.push(normalized);
    } else if (t.includes("liquidity")) {
      liquidityRaw.push(item);
    } else {
      orderBlocks.push(normalized);
    }
  });

  const liquidity = liquidityRaw
    .map((item) => {
      const level = toFiniteNumber(item?.level ?? item?.price);
      if (level == null) return null;
      return { level, label: normalizeWhitespace(item?.label || "Liquidity") };
    })
    .filter(Boolean);

  const structure = [...structureRaw, ...genericLevels]
    .map((item) => {
      const level = toFiniteNumber(item?.level ?? item?.price);
      if (level == null) return null;
      return {
        level,
        type: normalizeWhitespace(item?.type || "structure"),
        label: normalizeWhitespace(item?.label || item?.type || "Level"),
      };
    })
    .filter(Boolean);

  const patterns = patternsRaw
    .map((item) => {
      const label = normalizeWhitespace(item?.label || item?.name || item?.type || item?.pattern || "");
      if (!label) return null;
      const level = toFiniteNumber(item?.level ?? item?.price ?? item?.y ?? item?.high ?? item?.low);
      return { label, level };
    })
    .filter(Boolean);

  return {
    order_blocks: orderBlocks,
    fvg,
    liquidity,
    structure,
    patterns,
  };
}

const overlayPlugin = {
  id: "smcOverlay",
  afterDraw(chartContext) {
    const { ctx, width, candles, smcOverlays, timeScale, candleSeries } = chartContext || {};
    if (!ctx || !width || !Array.isArray(candles) || !candles.length || !smcOverlays) return;
    const hasAnyOverlay = smcOverlays.order_blocks.length
      || smcOverlays.fvg.length
      || smcOverlays.liquidity.length
      || smcOverlays.structure.length
      || smcOverlays.patterns.length;
    if (!hasAnyOverlay) return;

    const startX = timeScale.timeToCoordinate(candles[0]?.time);
    const endX = timeScale.timeToCoordinate(candles[candles.length - 1]?.time);
    if (startX == null || endX == null) return;
    const leftX = Math.min(startX, endX);
    const rightX = Math.max(startX, endX);
    const lineWidth = Math.max(1, rightX - leftX);

    smcOverlays.order_blocks.forEach((zone) => {
      const yTop = candleSeries.priceToCoordinate(zone.to);
      const yBottom = candleSeries.priceToCoordinate(zone.from);
      if (yTop == null || yBottom == null) return;
      const top = Math.min(yTop, yBottom);
      const height = Math.max(4, Math.abs(yBottom - yTop));
      const isBearish = ["bearish", "supply", "sell", "short"].includes(zone.type);
      ctx.save();
      ctx.fillStyle = isBearish ? "rgba(239, 68, 68, 0.16)" : "rgba(34, 197, 94, 0.16)";
      ctx.strokeStyle = isBearish ? "rgba(239, 68, 68, 0.5)" : "rgba(34, 197, 94, 0.5)";
      ctx.lineWidth = 1;
      ctx.fillRect(leftX, top, lineWidth, height);
      ctx.strokeRect(leftX, top, lineWidth, height);
      ctx.restore();
    });

    smcOverlays.fvg.forEach((gap) => {
      const yTop = candleSeries.priceToCoordinate(gap.to);
      const yBottom = candleSeries.priceToCoordinate(gap.from);
      if (yTop == null || yBottom == null) return;
      const top = Math.min(yTop, yBottom);
      const height = Math.max(3, Math.abs(yBottom - yTop));
      ctx.save();
      ctx.fillStyle = "rgba(59, 130, 246, 0.16)";
      ctx.strokeStyle = "rgba(96, 165, 250, 0.45)";
      ctx.lineWidth = 1;
      ctx.fillRect(leftX, top, lineWidth, height);
      ctx.strokeRect(leftX, top, lineWidth, height);
      ctx.restore();
    });

    smcOverlays.liquidity.forEach((item) => {
      const y = candleSeries.priceToCoordinate(item.level);
      if (y == null) return;
      ctx.save();
      ctx.strokeStyle = "rgba(56, 189, 248, 0.95)";
      ctx.lineWidth = 1.2;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(leftX, y);
      ctx.lineTo(rightX, y);
      ctx.stroke();
      ctx.restore();
      drawLabel(ctx, Math.max(8, rightX - 180), y - 4, item.label || "Liquidity", "#38bdf8");
    });

    smcOverlays.structure.forEach((item) => {
      const y = candleSeries.priceToCoordinate(item.level);
      if (y == null) return;
      ctx.save();
      ctx.strokeStyle = "rgba(251, 191, 36, 0.95)";
      ctx.lineWidth = 1.3;
      ctx.beginPath();
      ctx.moveTo(leftX, y);
      ctx.lineTo(rightX, y);
      ctx.stroke();
      ctx.restore();
      drawLabel(ctx, Math.max(8, rightX - 180), y - 4, item.label || `Structure: ${item.type}`, "#fbbf24");
    });

    smcOverlays.patterns.forEach((item, index) => {
      const y = candleSeries.priceToCoordinate(item.level);
      if (y == null) return;
      const x = Math.max(leftX + 12, rightX - 220 + (index % 2) * 110);
      drawLabel(ctx, x, y - 4, item.label || "Pattern", "#f9a8d4", "rgba(38, 12, 30, 0.86)");
    });
  },
};

registerChartPlugin(overlayPlugin);

function drawOverlay() {
  if (!chart || !currentChartPayload) return;

  const ctx = fitOverlayCanvas();
  const width = overlayCanvas.clientWidth;
  const height = overlayCanvas.clientHeight;
  ctx.clearRect(0, 0, width, height);

  const { candles, zones, levels, arrows, labels, smcOverlays } = prepareOverlayData(currentChartPayload);
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

  const chartContext = { chart, ctx, width, height, candles, smcOverlays, timeScale, candleSeries };
  registeredChartPlugins.forEach((plugin) => plugin.afterDraw(chartContext));
}

async function resolveChartData(idea) {
  if (idea?.combined && idea?.timeframe_ideas && typeof idea.timeframe_ideas === "object") {
    const preferred = idea.timeframe_ideas?.M15 || idea.timeframe_ideas?.H1 || idea.timeframe_ideas?.H4;
    if (preferred) {
      return resolveChartData(preferred);
    }
  }
  const localChartData = idea.chartData;
  if (localChartData?.candles?.length) {
    if (idea?.chart_overlays && !localChartData.chart_overlays) {
      return { ...localChartData, chart_overlays: idea.chart_overlays };
    }
    return localChartData;
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
    const payload = await res.json();
    if (!payload?.candles?.length) return null;
    if (idea?.chart_overlays && !payload.chart_overlays) {
      return { ...payload, chart_overlays: idea.chart_overlays };
    }
    return payload;
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

function cacheIdeaChart(ideaId, chartRef) {
  if (!ideaId || !chartRef) return;
  lastValidChartByIdeaId.set(String(ideaId), chartRef);
}

function readCachedIdeaChart(ideaId) {
  if (!ideaId) return null;
  return lastValidChartByIdeaId.get(String(ideaId)) || null;
}

function showCachedChartIfAny(idea) {
  const cached = readCachedIdeaChart(idea?.id);
  if (!cached) return false;
  if (cached.type === "snapshot") return showSnapshotChart(cached.value);
  if (cached.type === "live") return showLiveChart(cached.value);
  return false;
}

function renderCleanDetailStatus(idea) {
  if (idea.status === "archived") {
    const closeText = normalizeWhitespace(idea.close_explanation) || "Сценарий закрыт и зафиксирован в архиве.";
    updateDetailStatus(`Финальный статус: ${statusRu(idea.final_status || idea.status)} · ${closeText}`);
    return;
  }
  updateDetailStatus(`Сигнал: ${getSignalLabel(idea)} · Последнее обновление: ${formatDateTime(idea.meaningful_updated_at)}`);
}

async function openIdea(idea) {
  activeIdea = idea;
  const requestId = ++detailRequestId;
  modalTitle.textContent = `${idea.symbol} — ${getSignalLabel(idea)}`;
  const compactMeta = [];
  if (idea?.combined && Array.isArray(idea?.timeframes_available)) {
    compactMeta.push(`ТФ: ${idea.timeframes_available.join("/")}`);
  }
  const confidence = Number(idea?.final_confidence ?? idea?.confidence);
  if (Number.isFinite(confidence) && confidence > 0) {
    compactMeta.push(`Уверенность ${Math.round(confidence)}%`);
  }
  modalSub.textContent = compactMeta.join(" · ");
  if (modalCard) {
    modalCard.dataset.direction = getSignalTone(idea);
  }
  modal.classList.add("open");

  renderDetailText(idea);
  renderCleanDetailStatus(idea);
  const rawSnapshotUrl = idea.chartImageUrl || idea.chart_image || "";
  const snapshotUrl = normalizeChartImageUrl(rawSnapshotUrl);
  const snapshotStatus = idea.chartSnapshotStatus || idea.chart_snapshot_status || "";
  const liveFallbackMessage = snapshotStatusRu(snapshotStatus);

  resetChartState({ keepSnapshot: true });
  showUnavailableChart("Загружаем график…");

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
      cacheIdeaChart(idea.id, { type: "snapshot", value: snapshotUrl });
      renderCleanDetailStatus(idea);
      return;
    }
  }

  let payload = null;
  if (hasRenderableCandles(idea)) {
    payload = idea.chartData || idea.chart_data;
  } else {
    payload = await resolveChartData(idea);
  }
  if (requestId !== detailRequestId || activeIdea?.id !== idea.id) return;

  if (showLiveChart(payload) || showLiveChart(idea.chartData)) {
    const livePayload = hasCandles(payload) ? payload : idea.chartData;
    if (hasCandles(livePayload)) {
      cacheIdeaChart(idea.id, { type: "live", value: livePayload });
    }
    renderCleanDetailStatus(idea);
    return;
  }

  if (showCachedChartIfAny(idea)) {
    renderCleanDetailStatus(idea);
    return;
  }

  showUnavailableChart(liveFallbackMessage);
  renderCleanDetailStatus(idea);
}

function closeModal() {
  modal.classList.remove("open");
  if (modalCard) {
    modalCard.dataset.direction = "neutral";
  }
  activeIdea = null;
  detailRequestId += 1;
  resetChartState({ keepSnapshot: false });
  showUnavailableChart("График недоступен");
}

function dedupeIdeasById(ideas) {
  const unique = new Map();
  for (const idea of ideas) {
    const ideaId = String(idea?.id || "");
    if (!ideaId) continue;
    unique.set(ideaId, idea);
  }
  return Array.from(unique.values());
}

function refreshOpenModalIfNeeded() {
  if (!activeIdea) return;
  const fresh = allIdeas.find((idea) => String(idea?.id) === String(activeIdea?.id));
  if (!fresh) return;
  if (!isMeaningfulUpdate(fresh) && !isMeaningfulUpdate(activeIdea)) return;
  if (!hasMeaningfulIdeaChange(activeIdea, fresh)) return;
  openIdea(fresh);
}

async function loadIdeasSnapshot() {
  if (ideasPollInFlight) return;
  ideasPollInFlight = true;

  try {
    const res = await fetch("/ideas/market", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const previousById = new Map(previousIdeasById);
    let normalizedIdeas = normalizeIdeas(data);
    if (!normalizedIdeas.length && ENABLE_MOCK_IDEAS_ON_EMPTY) {
      console.warn("Используем временный mock идей: активирован ideas_mock=1.");
      normalizedIdeas = normalizeIdeas({ ideas: TEMP_MOCK_IDEAS });
    }

    normalizedIdeas = dedupeIdeasById(normalizedIdeas);
    normalizedIdeas = normalizedIdeas.filter((idea) => isVisibleIdea(idea));

    normalizedIdeas = normalizedIdeas.map((idea) => mergeWithPreviousIdeaState(idea, previousById.get(String(idea?.id))));
    const incomingById = new Map(normalizedIdeas.map((idea) => [String(idea.id), idea]));

    let hasRealtimeChanges = false;
    for (const [ideaId, idea] of incomingById.entries()) {
      const prev = previousById.get(ideaId);
      if (!prev) {
        hasRealtimeChanges = initialIdeasSyncCompleted || hasRealtimeChanges;
        continue;
      }
      if (hasMeaningfulIdeaChange(prev, idea) && hasMeaningfulVisualChange(prev, idea) && isMeaningfulUpdate(idea)) {
        hasRealtimeChanges = initialIdeasSyncCompleted || hasRealtimeChanges;
      }
    }

    allIdeas = normalizedIdeas;
    previousIdeasById.clear();
    for (const [ideaId, idea] of incomingById.entries()) {
      previousIdeasById.set(ideaId, idea);
    }

    populateFilters(allIdeas);
    applyFilters();
    renderStats(allIdeas, data?.statistics);
    refreshOpenModalIfNeeded();

    if (hasRealtimeChanges) {
      playIdeaNotification();
      const visibleIds = new Set(getFilteredIdeas().map((idea) => String(idea.id)));
      for (const ideaId of visibleIds) {
        const prev = previousById.get(ideaId);
        const next = incomingById.get(ideaId);
        if (
          !next
          || !isMeaningfulUpdate(next)
          || (prev && (!hasMeaningfulIdeaChange(prev, next) || !hasMeaningfulVisualChange(prev, next)))
        ) continue;
        const card = Array.from(ideasRoot.querySelectorAll(".card[data-idea-id]"))
          .find((node) => node.dataset.ideaId === ideaId);
        flashIdeaCard(card);
      }
    }

    initialIdeasSyncCompleted = true;
  } catch (error) {
    console.warn("Не удалось загрузить /ideas/market, synthetic fallback отключён.", error);
    if (!initialIdeasSyncCompleted) {
      allIdeas = [];
      previousIdeasById.clear();
      populateFilters(allIdeas);
      renderIdeas(allIdeas, "Источник идей временно недоступен. Нет актуальных рыночных данных.");
      renderStats(allIdeas, null);
    }
  } finally {
    ideasPollInFlight = false;
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

loadIdeasSnapshot();
setInterval(loadIdeasSnapshot, IDEAS_POLL_INTERVAL_MS);
