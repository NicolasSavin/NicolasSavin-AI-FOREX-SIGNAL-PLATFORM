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
let chartPriceLines = [];
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
  const description = normalizeWhitespace(
    idea?.idea_thesis
    || idea?.ideaThesis
    || idea?.unified_narrative
    || idea?.full_text
    || idea?.fullText
    || idea?.summary
  );
  if (isRenderableNarrative(description) && !isCompactTechnicalSummary(description)) return description;

  const fallbackNarrative = normalizeWhitespace(idea?.fallback_narrative);
  if (isRenderableNarrative(fallbackNarrative) && !isCompactTechnicalSummary(fallbackNarrative)) return fallbackNarrative;

  return "Нет описания";
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
  const thesis = normalizeWhitespace(idea?.idea_thesis || idea?.ideaThesis);
  const unifiedNarrative = normalizeWhitespace(idea?.unified_narrative);
  const fallbackNarrative = normalizeWhitespace(idea?.fallback_narrative);
  const shortText = buildShortText({
    ...idea,
    summary,
    summary_ru: summary,
    full_text: fullText,
  });

  const normalizedChartImageUrl = normalizeChartImageUrl(idea?.chartImageUrl || idea?.chart_image || "");
  const normalizedChartData = idea?.chartData ?? idea?.chart_data ?? null;
  const normalizedSnapshotStatus = normalizeSnapshotStatus(
    idea?.chartSnapshotStatus || idea?.chart_snapshot_status || "",
    {
      hasImage: Boolean(normalizedChartImageUrl),
      hasCandles: hasCandles(normalizedChartData),
    },
  );
  console.log("chart_image:", normalizedChartImageUrl || null);
  console.log("snapshot_status:", normalizedSnapshotStatus);
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
    idea_thesis: thesis,
    unified_narrative: unifiedNarrative,
    fallback_narrative: fallbackNarrative,
    narrative_source: normalizeWhitespace(idea?.narrative_source || idea?.narrativeSource || "model").toLowerCase(),
    llm_provider: normalizeWhitespace(idea?.llm_provider || "openrouter").toLowerCase(),
    llm_model: normalizeWhitespace(idea?.llm_model || ""),
    candles_count_sent: Number(idea?.candles_count_sent ?? 0),
    chart_overlays_present: Boolean(
      idea?.chart_overlays_present
      ?? hasMeaningfulChartOverlays(
        idea?.chart_overlays
        ?? idea?.chartOverlays
        ?? idea?.chart_data?.chart_overlays
        ?? idea?.chartData?.chart_overlays
        ?? null,
      )
    ),
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
    chartSnapshotStatus: normalizedSnapshotStatus,
    chart_snapshot_status: normalizedSnapshotStatus,
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

function isNoDataText(value) {
  const text = normalizeWhitespace(value).toLowerCase();
  if (!text) return true;
  return [
    "нет данных",
    "no data",
    "n/a",
    "—",
    "-",
    "none",
    "null",
  ].includes(text);
}

function hasMeaningfulNarrative(idea) {
  const narrativeCandidates = [
    idea?.summary,
    idea?.summary_ru,
    idea?.short_text,
    idea?.idea_thesis,
    idea?.unified_narrative,
    idea?.htf_bias_summary,
    idea?.mtf_structure_summary,
    idea?.ltf_trigger_summary,
  ];
  return narrativeCandidates.some((value) => {
    const text = normalizeWhitespace(value);
    return text && !isNoDataText(text) && isRenderableNarrative(text);
  });
}

function hasValidTradeLevels(idea) {
  return normalizeLevel(idea?.entry) != null
    && normalizeLevel(idea?.stopLoss) != null
    && normalizeLevel(idea?.takeProfit) != null;
}

function normalizeTimeframe(value) {
  const tf = normalizeWhitespace(value).toUpperCase();
  if (["H4", "H1", "M15"].includes(tf)) return tf;
  return tf || "H1";
}

function buildAggregatedIdea(groupedIdeas, symbol) {
  const byTf = new Map();
  groupedIdeas.forEach((idea) => {
    const tf = normalizeTimeframe(idea?.timeframe || idea?.tf);
    if (!byTf.has(tf)) {
      byTf.set(tf, idea);
      return;
    }
    const prev = byTf.get(tf);
    if (Number(idea?.confidence ?? 0) >= Number(prev?.confidence ?? 0)) {
      byTf.set(tf, idea);
    }
  });

  const h4 = byTf.get("H4");
  const h1 = byTf.get("H1");
  const m15 = byTf.get("M15");
  const ordered = [h4, h1, m15].filter(Boolean);
  if (!ordered.length) return null;

  const bestSignalIdea = ordered.find((idea) => {
    const signal = normalizeSignalValue(idea?.final_signal || idea?.signal || "");
    return signal === "BUY" || signal === "SELL";
  }) || ordered[0];

  const fallbackSignal = normalizeSignalValue(bestSignalIdea?.final_signal || bestSignalIdea?.signal || "");
  const finalSignal = ["BUY", "SELL"].includes(fallbackSignal) ? fallbackSignal : "WAIT";
  const narrativeSource = bestSignalIdea || h1 || h4 || m15;

  const aggregated = {
    ...narrativeSource,
    id: `combined-${symbol}`,
    symbol,
    pair: symbol,
    timeframe: "MTF",
    tf: "MTF",
    combined: true,
    timeframes_available: Array.from(byTf.keys()).filter(Boolean),
    timeframe_ideas: Object.fromEntries(Array.from(byTf.entries())),
    htf_bias_summary: normalizeWhitespace(h4?.summary_ru || h4?.summary || h4?.short_text || h4?.idea_thesis || ""),
    mtf_structure_summary: normalizeWhitespace(h1?.summary_ru || h1?.summary || h1?.short_text || h1?.idea_thesis || ""),
    ltf_trigger_summary: normalizeWhitespace(m15?.summary_ru || m15?.summary || m15?.short_text || m15?.idea_thesis || ""),
    final_signal: finalSignal,
    signal: finalSignal,
    direction: finalSignal === "BUY" ? "bullish" : finalSignal === "SELL" ? "bearish" : "neutral",
    bias: finalSignal === "BUY" ? "bullish" : finalSignal === "SELL" ? "bearish" : "neutral",
    confidence: Math.max(...ordered.map((idea) => Number(idea?.confidence ?? 0))),
    entry: narrativeSource?.entry ?? "—",
    stopLoss: narrativeSource?.stopLoss ?? "—",
    takeProfit: narrativeSource?.takeProfit ?? "—",
    chartData: narrativeSource?.chartData ?? narrativeSource?.chart_data ?? null,
    chart_data: narrativeSource?.chart_data ?? narrativeSource?.chartData ?? null,
    chartImageUrl: narrativeSource?.chartImageUrl || narrativeSource?.chart_image || "",
    chart_image: narrativeSource?.chart_image || narrativeSource?.chartImageUrl || "",
  };

  return normalizeIdea(aggregated);
}

function shouldDisplayAggregatedIdea(idea) {
  if (!idea || !isVisibleIdea(idea)) return false;
  const signal = normalizeSignalValue(idea?.final_signal || idea?.signal || "");
  const confidence = Number(idea?.final_confidence ?? idea?.confidence ?? 0);
  const hasNarrative = [
    idea?.full_text,
    idea?.fullText,
    idea?.idea_thesis,
    idea?.unified_narrative,
    idea?.summary,
    idea?.summary_ru,
    idea?.htf_bias_summary,
    idea?.mtf_structure_summary,
    idea?.ltf_trigger_summary,
    idea?.short_text,
    idea?.shortScenarioRu,
    idea?.short_scenario_ru,
  ].some((value) => {
    const text = normalizeWhitespace(value);
    return text && !isNoDataText(text);
  });

  const isStrongIdea = (signal === "BUY" || signal === "SELL")
    && confidence >= 40
    && hasValidTradeLevels(idea);
  if (isStrongIdea) return true;

  // Для WAIT-идей достаточно содержательного нарратива без порога по confidence.
  const isMeaningfulWait = signal === "WAIT" && hasNarrative;
  if (isMeaningfulWait) return true;

  if (!idea?.combined) return false;
  return false;
}

function aggregateIdeasBySymbol(ideas) {
  const grouped = new Map();
  ideas.forEach((idea) => {
    const symbol = normalizeWhitespace(idea?.symbol || idea?.pair).toUpperCase();
    if (!symbol) return;
    if (!grouped.has(symbol)) grouped.set(symbol, []);
    grouped.get(symbol).push(idea);
  });

  const candidates = Array.from(grouped.entries())
    .map(([symbol, symbolIdeas]) => {
      const alreadyCombined = symbolIdeas.find((idea) => idea?.combined);
      if (alreadyCombined) {
        const normalizedCombined = normalizeIdea({
          ...alreadyCombined,
          id: alreadyCombined?.id || `combined-${symbol}`,
          symbol,
          pair: symbol,
          timeframe: "MTF",
          tf: "MTF",
          combined: true,
          timeframes_available: Array.isArray(alreadyCombined?.timeframes_available)
            ? alreadyCombined.timeframes_available
            : ["H4", "H1", "M15"],
        });
        return normalizedCombined;
      }
      const built = buildAggregatedIdea(symbolIdeas, symbol);
      if (built) return built;
      const fallbackIdea = symbolIdeas.find((idea) => isVisibleIdea(idea));
      return fallbackIdea ? normalizeIdea(fallbackIdea) : null;
    })
    .filter(Boolean);

  const result = [];
  const addedSymbols = new Set();
  for (const idea of candidates) {
    const symbol = normalizeWhitespace(idea?.symbol || idea?.pair).toUpperCase();
    if (!symbol || addedSymbols.has(symbol)) continue;
    if (shouldDisplayAggregatedIdea(idea)) {
      result.push(idea);
      addedSymbols.add(symbol);
    }
  }

  for (const idea of candidates) {
    const symbol = normalizeWhitespace(idea?.symbol || idea?.pair).toUpperCase();
    if (!symbol || addedSymbols.has(symbol)) continue;
    if (!isVisibleIdea(idea)) continue;
    result.push(idea);
    addedSymbols.add(symbol);
  }

  return result;
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
  const timeframes = [...new Set(
    ideas.flatMap((idea) => (
      Array.isArray(idea?.timeframes_available) && idea.timeframes_available.length
        ? idea.timeframes_available
        : ["H4", "H1", "M15"]
    ))
  )];
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
    const availableTfs = Array.isArray(idea?.timeframes_available) && idea.timeframes_available.length
      ? idea.timeframes_available.map((tf) => String(tf).trim().toUpperCase())
      : [String(idea.timeframe || idea.tf || "").trim().toUpperCase()];
    const symbolOk = symbol === "ALL" || currentSymbol === symbol;
    const tfOk = timeframe === "ALL" || availableTfs.includes(timeframe);
    return symbolOk && tfOk;
  });
}

function buildIdeaCardMarkup(idea) {
  console.log("IDEA DEBUG:", idea);
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

  console.debug("[ideas-chart] chart init started", {
    hostWidth: chartHost?.clientWidth ?? 0,
    hostHeight: chartHost?.clientHeight ?? 0,
  });

  chart = LightweightCharts.createChart(chartHost, {
    layout: {
      background: { color: "#070f1d" },
      textColor: "#b8c7db",
    },
    grid: {
      vertLines: { color: "rgba(30, 46, 71, 0.7)" },
      horzLines: { color: "rgba(30, 46, 71, 0.8)" },
    },
    rightPriceScale: {
      borderColor: "#233553",
      scaleMargins: {
        top: 0.08,
        bottom: 0.08,
      },
    },
    timeScale: {
      borderColor: "#233553",
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 4,
      barSpacing: 10,
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

  console.debug("[ideas-chart] candlestick series created");
}

function resetChartState({ keepSnapshot = true } = {}) {
  currentChartPayload = null;
  clearChartPriceLines();
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

function clearChartPriceLines() {
  if (!chart || !candleSeries || !Array.isArray(chartPriceLines) || !chartPriceLines.length) return;
  chartPriceLines.forEach((line) => {
    try {
      candleSeries.removePriceLine(line);
    } catch (error) {
      console.debug("Failed to remove chart price line", error);
    }
  });
  chartPriceLines = [];
}

function toFinitePrice(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function hasBasicLevels(idea) {
  return toFinitePrice(idea?.entry) != null
    || toFinitePrice(idea?.stopLoss ?? idea?.sl) != null
    || toFinitePrice(idea?.takeProfit ?? idea?.tp) != null;
}

function createSyntheticLevelCandles(entry, stopLoss, takeProfit, anchorPrice = null) {
  const prices = [entry, stopLoss, takeProfit, anchorPrice].filter(Number.isFinite);
  if (!prices.length) return [];
  const basePrice = Number.isFinite(anchorPrice) ? anchorPrice : (Number.isFinite(entry) ? entry : prices[0]);
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const spread = Math.max(Math.abs(maxPrice - minPrice), Math.abs(basePrice) * 0.0012, 0.0001);
  const amplitude = spread * 0.18;
  const baseTime = Math.floor(Date.now() / 1000);
  const totalBars = 24;
  const candles = [];

  for (let index = 0; index < totalBars; index += 1) {
    const progress = index / Math.max(1, totalBars - 1);
    const wave = Math.sin(progress * Math.PI * 2.6);
    const prevWave = Math.sin(Math.max(0, progress - 1 / totalBars) * Math.PI * 2.6);
    const open = basePrice + prevWave * amplitude * 0.9;
    const close = basePrice + wave * amplitude;
    const high = Math.max(open, close) + amplitude * 0.45;
    const low = Math.min(open, close) - amplitude * 0.45;
    candles.push({
      time: baseTime - (totalBars - index) * 3600,
      open,
      high,
      low,
      close,
    });
  }

  return candles;
}

function buildBasicLevelsChartPayload(idea) {
  if (!hasBasicLevels(idea)) return null;
  const entry = toFinitePrice(idea?.entry);
  const stopLoss = toFinitePrice(idea?.stopLoss ?? idea?.sl);
  const takeProfit = toFinitePrice(idea?.takeProfit ?? idea?.tp);
  const anchorPrice = toFinitePrice(idea?.current_price ?? idea?.latest_close ?? idea?.market_reference_price);
  const syntheticCandles = createSyntheticLevelCandles(entry, stopLoss, takeProfit, anchorPrice);
  if (!syntheticCandles.length) return null;
  const levelLines = [];
  if (entry != null) levelLines.push({ price: entry, title: "Entry", color: "#facc15", lineStyle: LightweightCharts.LineStyle.Solid });
  if (stopLoss != null) levelLines.push({ price: stopLoss, title: "SL", color: "#ef4444", lineStyle: LightweightCharts.LineStyle.Dashed });
  if (takeProfit != null) levelLines.push({ price: takeProfit, title: "TP", color: "#22c55e", lineStyle: LightweightCharts.LineStyle.Dashed });
  return {
    candles: syntheticCandles,
    level_lines: levelLines,
  };
}

function normalizeChartImageUrl(url) {
  const raw = normalizeWhitespace(url);
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw) || raw.startsWith("/")) return raw;
  if (raw.startsWith("static/")) return `/${raw}`;
  if (raw.startsWith("./")) return `/${raw.slice(2)}`;
  return `/static/${raw.replace(/^\/+/, "")}`;
}

function getValidChartUrl(idea) {
  return normalizeChartImageUrl(idea?.chartImageUrl || idea?.chart_image || "");
}

function hasValidSnapshotImage(idea) {
  return Boolean(getValidChartUrl(idea));
}

function normalizeSnapshotStatus(rawStatus, { hasImage = false, hasCandles = false } = {}) {
  const normalized = normalizeWhitespace(rawStatus).toLowerCase();
  if (hasImage && normalized === "ok") return "ok";
  if (normalized === "ok" && !hasImage) {
    return hasCandles ? "snapshot_failed" : "no_data";
  }
  if (normalized) return normalized;
  if (hasImage) return "ok";
  return hasCandles ? "snapshot_failed" : "no_data";
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
  let malformedCount = 0;
  const seenTimes = new Set();
  for (const candle of rawCandles) {
    if (!candle || typeof candle !== "object") continue;
    const timeRaw = normalizeCandleTime(candle.time ?? candle.timestamp);
    const openRaw = Number(candle.open);
    const highRaw = Number(candle.high);
    const lowRaw = Number(candle.low);
    const closeRaw = Number(candle.close);
    if (![timeRaw, openRaw, highRaw, lowRaw, closeRaw].every(Number.isFinite)) {
      malformedCount += 1;
      continue;
    }
    const lowerBound = Math.min(openRaw, closeRaw);
    const upperBound = Math.max(openRaw, closeRaw);
    if (lowRaw > lowerBound || highRaw < upperBound || lowRaw > highRaw) {
      malformedCount += 1;
      continue;
    }
    const ts = Math.trunc(timeRaw);
    if (seenTimes.has(ts)) continue;
    seenTimes.add(ts);
    normalized.push({ time: ts, open: openRaw, high: highRaw, low: lowRaw, close: closeRaw });
  }
  normalized.sort((a, b) => a.time - b.time);
  console.debug("[ideas-chart] candles normalized", {
    received: rawCandles.length,
    normalized: normalized.length,
    malformed: malformedCount,
  });
  return normalized;
}

function normalizeCandleTime(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    if (value > 1e12) return Math.trunc(value / 1000);
    if (value > 1e10) return Math.trunc(value / 1000);
    return Math.trunc(value);
  }

  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return Number.NaN;
    const numeric = Number(trimmed);
    if (Number.isFinite(numeric)) return normalizeCandleTime(numeric);
    const parsed = Date.parse(trimmed);
    if (Number.isFinite(parsed)) return Math.trunc(parsed / 1000);
  }

  return Number.NaN;
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
  clearChartPriceLines();
  chartSnapshotImage.src = imageUrl;
  setChartMode("snapshot");
  return true;
}

function hasRenderableSnapshot(idea) {
  return hasValidSnapshotImage(idea);
}

function hasRenderableCandles(idea) {
  return hasCandles(idea?.chartData) || hasCandles(idea?.chart_data);
}

function shouldUseFallbackCandles(idea) {
  return !hasValidSnapshotImage(idea) && hasRenderableCandles(idea);
}

function hasMeaningfulChartOverlays(overlays) {
  if (!overlays || typeof overlays !== "object") return false;
  const keys = ["order_blocks", "liquidity", "fvg", "structure_levels", "patterns", "zones", "levels"];
  return keys.some((key) => Array.isArray(overlays[key]) && overlays[key].length > 0);
}

function buildIdeaLevelLines(idea, overlays) {
  const lines = [];
  const pushLine = (price, title, color, lineStyle = LightweightCharts.LineStyle.Dashed) => {
    const parsed = toFinitePrice(price);
    if (parsed == null) return;
    lines.push({ price: parsed, title, color, lineStyle });
  };
  pushLine(idea?.entry, "Entry", "#facc15", LightweightCharts.LineStyle.Solid);
  pushLine(idea?.stopLoss ?? idea?.sl, "SL", "#ef4444");
  pushLine(idea?.takeProfit ?? idea?.tp, "TP", "#22c55e");

  const normalizedOverlays = normalizeSmcOverlays({ candles: [], chart_overlays: overlays || {} });
  (normalizedOverlays.structure || []).forEach((item) => pushLine(item?.level, item?.label || "Structure", "#f59e0b"));
  (normalizedOverlays.liquidity || []).forEach((item) => pushLine(item?.level, item?.label || "Liquidity", "#38bdf8", LightweightCharts.LineStyle.Dotted));
  return lines;
}

function mergeWithPreviousIdeaState(nextIdea, prevIdea) {
  if (!prevIdea || typeof prevIdea !== "object") return nextIdea;
  const nextSnapshot = normalizeChartImageUrl(nextIdea?.chartImageUrl || nextIdea?.chart_image || "");
  const prevSnapshot = normalizeChartImageUrl(prevIdea?.chartImageUrl || prevIdea?.chart_image || "");
  const nextChartData = hasRenderableCandles(nextIdea) ? (nextIdea.chartData || nextIdea.chart_data) : null;
  const prevChartData = hasRenderableCandles(prevIdea) ? (prevIdea.chartData || prevIdea.chart_data) : null;
  const nextOverlays = nextIdea?.chart_overlays;
  const prevOverlays = prevIdea?.chart_overlays;
  const mergedOverlays = hasMeaningfulChartOverlays(nextOverlays) ? nextOverlays : prevOverlays || nextOverlays || null;
  const mergedSnapshot = nextSnapshot || prevSnapshot;

  return {
    ...nextIdea,
    chartImageUrl: mergedSnapshot,
    chart_image: mergedSnapshot,
    chartData: nextChartData || prevChartData || nextIdea.chartData || null,
    chart_overlays: mergedOverlays,
  };
}


function mergeChartPayloadWithIdeaOverlays(payload, idea) {
  if (!payload || typeof payload !== "object") return payload;
  const ideaOverlays = idea?.chart_overlays;
  if (!ideaOverlays || payload.chart_overlays) return payload;
  return { ...payload, chart_overlays: ideaOverlays };
}

function applyLevelLines(levelLines) {
  clearChartPriceLines();
  if (!Array.isArray(levelLines) || !levelLines.length || !candleSeries) return;
  levelLines.forEach((line) => {
    const price = toFinitePrice(line?.price);
    if (price == null) return;
    const priceLine = candleSeries.createPriceLine({
      price,
      color: line?.color || "#cbd5e1",
      lineWidth: 2,
      lineStyle: line?.lineStyle ?? LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: line?.title || "",
    });
    chartPriceLines.push(priceLine);
  });
}

function showLiveChart(payload, { levelLines = null } = {}) {
  const normalizedPayload = normalizeChartPayload(payload);
  const candleCount = normalizedPayload?.candles?.length ?? 0;
  console.debug("[ideas-chart] live payload received", { candleCount });
  if (!hasCandles(normalizedPayload)) {
    console.debug("[ideas-chart] fallback placeholder used", { reason: "empty_or_invalid_candles" });
    return false;
  }

  try {
    ensureChart();
    currentChartPayload = normalizedPayload;
    candleSeries.setData(normalizedPayload.candles);
    console.debug("[ideas-chart] setData called", { candleCount });
    applyLevelLines(levelLines ?? normalizedPayload.level_lines ?? null);
    chart.timeScale().fitContent();
  } catch (error) {
    console.warn("[ideas-chart] fallback placeholder used", {
      reason: "setData_failed",
      message: error?.message || String(error),
    });
    return false;
  }

  setChartMode("live");
  requestAnimationFrame(() => {
    requestAnimationFrame(() => drawOverlay());
  });
  return true;
}

function showUnavailableChart(message) {
  clearChartPriceLines();
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


function toFiniteIndex(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(0, Math.round(numeric)) : null;
}

function clampIndex(index, maxIndex) {
  if (!Number.isFinite(index) || maxIndex < 0) return 0;
  return Math.max(0, Math.min(Math.round(index), maxIndex));
}

function buildPatternAnchor(candles, pattern) {
  if (!Array.isArray(candles) || !candles.length || !pattern) return null;
  const maxIndex = candles.length - 1;
  const fromIndex = clampIndex(pattern.from_index ?? pattern.start_index ?? pattern.index ?? maxIndex, maxIndex);
  const toIndex = clampIndex(pattern.to_index ?? pattern.end_index ?? pattern.index ?? fromIndex, maxIndex);
  const anchorIndex = clampIndex(Math.round((fromIndex + toIndex) / 2), maxIndex);
  const explicitPrice = toFiniteNumber(pattern.level ?? pattern.price ?? pattern.y ?? pattern.high ?? pattern.low);
  const candle = candles[toIndex] || candles[anchorIndex] || candles[maxIndex];
  const fallbackPrice = candle ? Math.max(candle.high, candle.close) : null;
  return {
    from_index: fromIndex,
    to_index: Math.max(fromIndex, toIndex),
    anchor_index: anchorIndex,
    anchor_price: explicitPrice ?? fallbackPrice,
  };
}

function normalizeSmcOverlays(payload) {
  const nestedOverlays = payload?.overlays?.chart_overlays ?? payload?.overlays?.chartOverlays ?? payload?.overlays ?? {};
  const base = payload?.chart_overlays
    ?? payload?.chartOverlays
    ?? nestedOverlays
    ?? {};
  const takeList = (...keys) => {
    for (const key of keys) {
      if (Array.isArray(base?.[key])) return base[key];
    }
    return [];
  };

  const asZone = (item, fallbackLabel = "Zone") => {
    const top = toFiniteNumber(item?.top ?? item?.high ?? item?.to ?? item?.price_to ?? item?.priceTo);
    const bottom = toFiniteNumber(item?.bottom ?? item?.low ?? item?.from ?? item?.price_from ?? item?.priceFrom);
    if (top == null || bottom == null) return null;
    const startIndex = toFiniteIndex(item?.from_index ?? item?.start_index ?? item?.startIndex ?? item?.start);
    const endIndex = toFiniteIndex(item?.to_index ?? item?.end_index ?? item?.endIndex ?? item?.end);
    return {
      from: Math.min(top, bottom),
      to: Math.max(top, bottom),
      type: String(item?.type || "").toLowerCase(),
      label: normalizeWhitespace(item?.label || fallbackLabel),
      from_index: startIndex,
      to_index: endIndex,
    };
  };

  const orderBlocksRaw = takeList("order_blocks", "orderBlocks", "orderblock", "order_blocks_zones");
  const fvgRaw = takeList("fvg", "imbalances", "imbalance", "fair_value_gap");
  const liquidityRaw = takeList("liquidity", "liquidity_levels", "liquidityLevels");
  const structureRaw = takeList("structure_levels", "structure", "structureLevels");
  const patternsRaw = takeList("patterns", "chart_patterns", "pattern_overlays");
  const zonesRaw = takeList("zones", "working_zones", "areas");
  const levelsRaw = takeList("levels", "trade_levels", "reference_levels");

  const combinedZoneItems = [...orderBlocksRaw, ...fvgRaw, ...zonesRaw];
  const orderBlocks = combinedZoneItems
    .map((item) => asZone(item, "Order Block"))
    .filter(Boolean)
    .filter((zone) => {
      const marker = String(zone.type || zone.label || "").toLowerCase();
      return !(marker.includes("fvg") || marker.includes("imbalance") || marker.includes("imb"));
    });
  const fvg = combinedZoneItems
    .map((item) => asZone(item, "FVG"))
    .filter(Boolean)
    .filter((zone) => {
      const marker = String(zone.type || zone.label || "").toLowerCase();
      return marker.includes("fvg") || marker.includes("imbalance") || marker.includes("imb");
    });

  const liquidity = [...liquidityRaw, ...levelsRaw]
    .map((item) => {
      const level = toFiniteNumber(item?.price ?? item?.level ?? item?.value);
      if (level == null) return null;
      const type = String(item?.type || "").toLowerCase();
      const fallbackLabel = type.includes("buy") || type.includes("bsl") ? "BSL" : type.includes("sell") || type.includes("ssl") ? "SSL" : "Liquidity";
      return {
        level,
        label: normalizeWhitespace(item?.label || fallbackLabel),
      };
    })
    .filter(Boolean);

  const structure = [...structureRaw, ...levelsRaw]
    .map((item) => {
      const level = toFiniteNumber(item?.price ?? item?.level ?? item?.value);
      if (level == null) return null;
      const type = normalizeWhitespace(item?.type || item?.kind || "structure").toLowerCase();
      const fallbackLabel = type.includes("bos")
        ? "BOS"
        : type.includes("choch")
          ? "CHoCH"
          : type.includes("support")
            ? "support"
            : type.includes("resistance")
              ? "resistance"
              : "Structure";
      return {
        level,
        type,
        label: normalizeWhitespace(item?.label || fallbackLabel),
      };
    })
    .filter(Boolean);

  const patterns = patternsRaw
    .map((item) => {
      const label = normalizeWhitespace(item?.label || item?.name || item?.type || item?.pattern || "");
      if (!label) return null;
      const anchor = buildPatternAnchor(payload?.candles || [], item);
      if (!anchor) return null;
      return {
        label,
        ...anchor,
      };
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

    const maxIndex = candles.length - 1;

    smcOverlays.order_blocks.forEach((zone) => {
      const yTop = candleSeries.priceToCoordinate(zone.to);
      const yBottom = candleSeries.priceToCoordinate(zone.from);
      if (yTop == null || yBottom == null) return;
      const fromIdx = clampIndex(zone.from_index ?? 0, maxIndex);
      const toIdx = clampIndex(zone.to_index ?? maxIndex, maxIndex);
      const x1 = timeScale.timeToCoordinate(candles[fromIdx]?.time) ?? leftX;
      const x2 = timeScale.timeToCoordinate(candles[toIdx]?.time) ?? rightX;
      const zoneLeft = Math.min(x1, x2);
      const zoneWidth = Math.max(8, Math.abs(x2 - x1));
      const top = Math.min(yTop, yBottom);
      const height = Math.max(4, Math.abs(yBottom - yTop));
      const isBearish = ["bearish", "supply", "sell", "short"].includes(zone.type);
      const label = normalizeWhitespace(zone.label || (isBearish ? "Bearish OB" : "Bullish OB"));
      ctx.save();
      ctx.fillStyle = isBearish ? "rgba(239, 68, 68, 0.18)" : "rgba(34, 197, 94, 0.18)";
      ctx.strokeStyle = isBearish ? "rgba(239, 68, 68, 0.55)" : "rgba(34, 197, 94, 0.55)";
      ctx.lineWidth = 1;
      ctx.fillRect(zoneLeft, top, zoneWidth, height);
      ctx.strokeRect(zoneLeft, top, zoneWidth, height);
      ctx.restore();
      drawLabel(ctx, zoneLeft + 6, top + 18, label, isBearish ? "#fca5a5" : "#86efac");
    });

    smcOverlays.fvg.forEach((gap) => {
      const yTop = candleSeries.priceToCoordinate(gap.to);
      const yBottom = candleSeries.priceToCoordinate(gap.from);
      if (yTop == null || yBottom == null) return;
      const fromIdx = clampIndex(gap.from_index ?? 0, maxIndex);
      const toIdx = clampIndex(gap.to_index ?? maxIndex, maxIndex);
      const x1 = timeScale.timeToCoordinate(candles[fromIdx]?.time) ?? leftX;
      const x2 = timeScale.timeToCoordinate(candles[toIdx]?.time) ?? rightX;
      const zoneLeft = Math.min(x1, x2);
      const zoneWidth = Math.max(8, Math.abs(x2 - x1));
      const top = Math.min(yTop, yBottom);
      const height = Math.max(3, Math.abs(yBottom - yTop));
      ctx.save();
      ctx.fillStyle = "rgba(147, 51, 234, 0.13)";
      ctx.strokeStyle = "rgba(167, 139, 250, 0.7)";
      ctx.setLineDash([4, 3]);
      ctx.lineWidth = 1;
      ctx.fillRect(zoneLeft, top, zoneWidth, height);
      ctx.strokeRect(zoneLeft, top, zoneWidth, height);
      ctx.restore();
      drawLabel(ctx, zoneLeft + 6, top + 16, normalizeWhitespace(gap.label || "FVG"), "#c4b5fd", "rgba(30, 12, 54, 0.88)");
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
      drawLabel(ctx, Math.max(8, rightX - 180), y - 4, item.label || "SSL/BSL", "#38bdf8");
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
      drawLabel(ctx, Math.max(8, rightX - 180), y - 4, item.label || "BOS/CHoCH", "#fbbf24");
    });

    smcOverlays.patterns.forEach((item) => {
      const y = candleSeries.priceToCoordinate(item.anchor_price);
      if (y == null) return;
      const anchorIdx = clampIndex(item.anchor_index ?? item.to_index ?? item.from_index ?? maxIndex, maxIndex);
      const x = timeScale.timeToCoordinate(candles[anchorIdx]?.time);
      if (x == null) return;
      drawLabel(ctx, x + 8, y - 8, item.label || "Pattern", "#f9a8d4", "rgba(38, 12, 30, 0.86)");
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
  if (!chartRef) return;
  const cacheKeys = new Set();
  if (ideaId) cacheKeys.add(String(ideaId));
  if (activeIdea) cacheKeys.add(getIdeaChartCacheKey(activeIdea));
  for (const cacheKey of cacheKeys) {
    if (!cacheKey) continue;
    lastValidChartByIdeaId.set(cacheKey, chartRef);
  }
}

function getIdeaChartCacheKey(idea) {
  if (!idea || typeof idea !== "object") return "";
  const symbol = normalizeWhitespace(idea.symbol || idea.pair || idea.instrument).toUpperCase();
  const timeframe = normalizeWhitespace(idea.timeframe || idea.tf || "H1").toUpperCase();
  if (!symbol) return "";
  return `${symbol}:${timeframe}`;
}

function readCachedIdeaChart(idea) {
  const idKey = normalizeWhitespace(idea?.id);
  const symbolKey = getIdeaChartCacheKey(idea);
  return (
    (idKey ? lastValidChartByIdeaId.get(idKey) : null)
    || (symbolKey ? lastValidChartByIdeaId.get(symbolKey) : null)
    || null
  );
}

function getCachedChartUrl(idea) {
  const cached = readCachedIdeaChart(idea);
  if (!cached || cached.type !== "snapshot") return "";
  return normalizeChartImageUrl(cached.value);
}

function showCachedChartIfAny(idea) {
  const cached = readCachedIdeaChart(idea);
  if (!cached) return false;
  if (cached.type === "snapshot") return showSnapshotChart(cached.value);
  if (cached.type === "live") return showLiveChart(cached.value);
  return false;
}

function preloadSnapshotImage(imageUrl) {
  return new Promise((resolve) => {
    if (!imageUrl) {
      resolve(false);
      return;
    }
    const img = new Image();
    img.onload = () => resolve(true);
    img.onerror = () => resolve(false);
    img.src = imageUrl;
  });
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
  const previousActiveIdea = activeIdea;
  const isSameIdeaRefresh = Boolean(
    previousActiveIdea
    && String(previousActiveIdea?.id) === String(idea?.id)
    && modal.classList.contains("open"),
  );
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
  const snapshotUrl = getValidChartUrl(idea);
  const cachedSnapshotUrl = getCachedChartUrl(idea);
  const resolvedSnapshotUrl = snapshotUrl || cachedSnapshotUrl;
  const hasLevelFallback = hasBasicLevels(idea);
  const hasFallbackCandles = shouldUseFallbackCandles(idea);
  normalizeSnapshotStatus(
    idea.chartSnapshotStatus || idea.chart_snapshot_status || "",
    {
      hasImage: Boolean(resolvedSnapshotUrl),
      hasCandles: hasFallbackCandles,
    },
  );

  if (!isSameIdeaRefresh || chartDisplayMode === "unavailable") {
    resetChartState({ keepSnapshot: true });
    showUnavailableChart("Загружаем график…");
  }

  if (resolvedSnapshotUrl) {
    const snapshotLoaded = await preloadSnapshotImage(resolvedSnapshotUrl);

    if (requestId !== detailRequestId || activeIdea?.id !== idea.id) return;
    if (snapshotLoaded) {
      showSnapshotChart(resolvedSnapshotUrl);
      cacheIdeaChart(idea.id, { type: "snapshot", value: resolvedSnapshotUrl });
      renderCleanDetailStatus(idea);
      return;
    }
  }

  if (showCachedChartIfAny(idea)) {
    renderCleanDetailStatus(idea);
    return;
  }

  let payload = null;
  if (hasRenderableCandles(idea)) {
    payload = mergeChartPayloadWithIdeaOverlays(idea.chartData || idea.chart_data, idea);
  } else {
    payload = mergeChartPayloadWithIdeaOverlays(await resolveChartData(idea), idea);
  }
  if (requestId !== detailRequestId || activeIdea?.id !== idea.id) return;

  const levelLines = buildIdeaLevelLines(idea, idea?.chart_overlays || payload?.chart_overlays);
  if (
    showLiveChart(payload, { levelLines })
    || showLiveChart(mergeChartPayloadWithIdeaOverlays(idea.chartData, idea), { levelLines })
  ) {
    const livePayload = hasCandles(payload) ? payload : idea.chartData;
    if (hasCandles(livePayload)) {
      cacheIdeaChart(idea.id, { type: "live", value: livePayload });
    }
    renderCleanDetailStatus(idea);
    return;
  }

  if (hasLevelFallback) {
    const levelsPayload = buildBasicLevelsChartPayload(idea);
    if (levelsPayload && showLiveChart(levelsPayload, { levelLines: levelsPayload.level_lines })) {
      renderCleanDetailStatus(idea);
      return;
    }
  }

  if (isSameIdeaRefresh && (chartDisplayMode === "live" || chartDisplayMode === "snapshot")) {
    console.debug("[ideas-chart] keeping previously rendered chart", {
      ideaId: idea?.id,
      mode: chartDisplayMode,
    });
    renderCleanDetailStatus(idea);
    return;
  }

  console.debug("[ideas-chart] fallback placeholder used", { reason: "no_chart_payload" });
  showUnavailableChart("График недоступен");
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
    const totalIdeasReceived = normalizedIdeas.length;
    if (!normalizedIdeas.length && ENABLE_MOCK_IDEAS_ON_EMPTY) {
      console.warn("Используем временный mock идей: активирован ideas_mock=1.");
      normalizedIdeas = normalizeIdeas({ ideas: TEMP_MOCK_IDEAS });
    }

    normalizedIdeas = aggregateIdeasBySymbol(normalizedIdeas);
    normalizedIdeas = dedupeIdeasById(normalizedIdeas);
    console.debug(`[ideas] received=${totalIdeasReceived} after_filter=${normalizedIdeas.length}`);

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
