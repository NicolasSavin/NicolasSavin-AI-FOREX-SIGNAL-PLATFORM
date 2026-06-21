const ideasContainer = document.getElementById("ideasContainer");
const ideasUpdatedAt = document.getElementById("ideasUpdatedAt");

const VOICE_STORAGE_KEY = "voice_notifications_enabled";
const VOICE_REPEAT_WINDOW_MS = 60000;
const VOICE_DEBOUNCE_MS = 1200;
const VOICE_MAX_QUEUE = 3;

let hasLoadedIdeasOnce = false;
let previousIdeasState = new Map();
let voiceDebounceTimer = null;
let voicePendingQueue = [];
let recentVoiceMessages = new Map();
let lastPayload = null;
let ideasPollTimer = null;
let isIdeasLoading = false;
let currentPropFilter = "all";
let modalChart = null;
let modalOverlayCanvas = null;
let modalOverlayContext = null;
let modalOverlayState = null;
let modalOverlayVisibility = { fvg: true, ob: true, liquidity: true, structure: true, signals: true };

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatUpdatedAt(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date) + " UTC";
}

async function getJson(url) {
  const resp = await fetch(url, { cache: "no-store" });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function sanitizeText(value) {
  return String(value || "")
    .replace(/\(\s*none\s*\)/gi, "")
    .replace(/\bnone\b/gi, "")
    .trim();
}

function firstText(...values) {
  for (const value of values) {
    const text = sanitizeText(value);
    if (text) return text;
  }
  return "";
}

function toFiniteNumber(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function pickFirstFiniteNumber(...values) {
  for (const value of values) {
    const num = toFiniteNumber(value);
    if (num !== null) return num;
  }
  return null;
}

function collectOverlayRanges(raw) {
  const items = Array.isArray(raw) ? raw : raw && typeof raw === "object" ? [raw] : [];
  return items
    .map((item) => ({
      low: pickFirstFiniteNumber(item?.low, item?.bottom, item?.min, item?.price_low, item?.zone_low, item?.from, item?.start, item?.y1),
      high: pickFirstFiniteNumber(item?.high, item?.top, item?.max, item?.price_high, item?.zone_high, item?.to, item?.end, item?.y2),
      price: pickFirstFiniteNumber(item?.price, item?.level, item?.value, item?.strike),
    }))
    .map((entry) => {
      if (entry.low === null && entry.high === null && entry.price !== null) {
        return { low: entry.price, high: entry.price };
      }
      if (entry.low !== null && entry.high === null) return { low: entry.low, high: entry.low };
      if (entry.high !== null && entry.low === null) return { low: entry.high, high: entry.high };
      return entry.low !== null && entry.high !== null ? { low: Math.min(entry.low, entry.high), high: Math.max(entry.low, entry.high) } : null;
    })
    .filter(Boolean);
}

function formatNumber(value) {
  if (value === undefined || value === null || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return escapeHtml(value);
  return String(num);
}

function formatListValue(value) {
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (value === undefined || value === null || value === "") return "—";
  return String(value);
}

function resolveOptionsSourceLabel(idea) {
  const source = firstText(idea.options_source, idea.optionsSource, idea.external_options_source);
  return source === "MT4_OptionsFX" || String(source).toLowerCase() === "mt4_optionsfx" ? "MT4_OptionsFX" : "CME_OptionsFX";
}

function resolveExternalOptionsRu(idea) {
  return firstText(
    idea.options_summary_ru,
    idea.optionsSummaryRu,
    idea.external_options_ru,
    idea.advisor_signal?.external_options_filter?.text_ru,
    idea.prop_signal_score?.external_options_filter?.text_ru,
  ) || `${resolveOptionsSourceLabel(idea)}: нет данных, слой не блокирует сделку`;
}

function resolveExternalOptionsBias(idea) {
  return firstText(
    idea.options_bias,
    idea.optionsBias,
    idea.external_options_bias,
    idea.advisor_signal?.external_options_filter?.option_bias,
    idea.prop_signal_score?.external_options_filter?.option_bias,
  ) || "neutral";
}

function resolveOptionsKeyStrikes(idea) {
  return idea.key_strikes || idea.keyStrikes || idea.key_levels || idea.keyLevels || idea.external_options_key_strikes;
}

function resolveOptionsMaxPain(idea) {
  return idea.max_pain ?? idea.maxPain ?? idea.external_options_max_pain;
}

function renderExternalOptionsCompact(idea) {
  return `<div class="idea-news-line">${escapeHtml(resolveOptionsSourceLabel(idea))}: <strong>${escapeHtml(resolveExternalOptionsBias(idea))}</strong> · strikes: ${escapeHtml(formatListValue(resolveOptionsKeyStrikes(idea)))} · max pain: ${escapeHtml(formatListValue(resolveOptionsMaxPain(idea)))}</div>`;
}



function normalizeOptionsLayer(idea) {
  const containers = [
    idea,
    idea?.options_analysis,
    idea?.options_overlay,
    idea?.options_layer,
    idea?.advisor_signal?.external_options_filter,
    idea?.prop_signal_score?.external_options_filter,
  ].filter((item) => item && typeof item === "object");
  const pick = (...keys) => {
    for (const box of containers) {
      for (const key of keys) {
        const value = box[key];
        if (value !== undefined && value !== null && value !== "") return value;
      }
    }
    return undefined;
  };
  const source = firstText(pick("options_source", "source", "optionsSource", "external_options_source"), resolveOptionsSourceLabel(idea));
  const bias = firstText(pick("options_bias", "bias", "option_bias", "optionsBias", "external_options_bias"), resolveExternalOptionsBias(idea));
  return {
    options_source: source,
    options_bias: bias,
    prop_bias: pick("prop_bias", "propBias", "prop_direction", "direction"),
    prop_score: pick("prop_score", "propScore", "score"),
    key_strikes: pick("key_strikes", "keyStrikes", "key_levels", "keyLevels", "external_options_key_strikes") ?? resolveOptionsKeyStrikes(idea),
    max_pain: pick("max_pain", "maxPain", "external_options_max_pain") ?? resolveOptionsMaxPain(idea),
    call_walls: pick("call_walls", "callWalls", "call_wall", "callWall"),
    put_walls: pick("put_walls", "putWalls", "put_wall", "putWall"),
    pinning_risk: pick("pinning_risk", "pinningRisk", "pin_risk"),
    range_risk: pick("range_risk", "rangeRisk"),
    target_levels: pick("target_levels", "targetLevels", "targets"),
    hedge_levels: pick("hedge_levels", "hedgeLevels", "hedges"),
    summary_text: resolveExternalOptionsRu(idea),
  };
}

function hasFreshOptionsLayer(layer) {
  const source = String(layer.options_source || "").toLowerCase();
  const meaningful = ["options_bias", "key_strikes", "max_pain", "call_walls", "put_walls", "pinning_risk", "range_risk", "target_levels", "hedge_levels"]
    .some((key) => formatListValue(layer[key]) !== "—" && !/^(neutral|unavailable|нет данных)$/i.test(String(layer[key] || "")));
  return source.includes("mt4_optionsfx") && meaningful;
}

function optionsTone(value) {
  const raw = String(value || "").toLowerCase();
  if (raw.includes("bull") || raw.includes("buy") || raw.includes("покуп")) return "bullish";
  if (raw.includes("bear") || raw.includes("sell") || raw.includes("прода")) return "bearish";
  return "neutral";
}

function optionsRiskTone(value) {
  const raw = String(value || "").toLowerCase();
  if (raw.includes("high") || raw.includes("выс")) return "high";
  if (raw.includes("medium") || raw.includes("mid") || raw.includes("сред")) return "medium";
  if (raw.includes("low") || raw.includes("низ")) return "low";
  return raw || "neutral";
}

function resolveOptionsAlignment(idea, layer) {
  const optionTone = optionsTone(layer.options_bias);
  const directionTone = optionsTone(layer.prop_bias || getIdeaDirectionRaw(idea));
  if (optionTone === "neutral" || directionTone === "neutral") return "Options neutral";
  return optionTone === directionTone ? "Options aligned" : "Options conflict";
}

function renderOptionPill(label, tone) {
  return `<span class="options-layer-pill options-layer-pill--${escapeHtml(tone)}">${escapeHtml(label)}</span>`;
}

function renderOptionsLayer(idea, { compact = false } = {}) {
  const layer = normalizeOptionsLayer(idea);
  if (!hasFreshOptionsLayer(layer)) {
    return `<section class="options-layer ${compact ? "options-layer--compact" : ""}"><div class="options-layer__head"><h4>🧩 Options Layer</h4></div><p class="options-layer__empty">Options: no fresh MT4_OptionsFX data</p></section>`;
  }
  const biasTone = optionsTone(layer.options_bias);
  const pinTone = optionsRiskTone(layer.pinning_risk);
  const rangeTone = optionsRiskTone(layer.range_risk);
  const fields = [
    ["Источник", layer.options_source],
    ["Options bias", layer.options_bias],
    ["Prop bias", layer.prop_bias],
    ["Prop score", layer.prop_score],
    ["Key strikes", layer.key_strikes],
    ["Max pain", layer.max_pain],
    ["Call walls", layer.call_walls],
    ["Put walls", layer.put_walls],
    ["Pinning risk", layer.pinning_risk],
    ["Range risk", layer.range_risk],
    ["Target levels", layer.target_levels],
    ["Hedge levels", layer.hedge_levels],
  ].filter(([, value]) => formatListValue(value) !== "—");
  return `<section class="options-layer ${compact ? "options-layer--compact" : ""}">
    <div class="options-layer__head"><h4>🧩 Options Layer</h4><strong>${escapeHtml(resolveOptionsAlignment(idea, layer))}</strong></div>
    <div class="options-layer__pills">
      ${renderOptionPill(`BIAS ${String(layer.options_bias || "neutral").toUpperCase()}`, biasTone)}
      ${pinTone === "high" ? renderOptionPill("PINNING HIGH", "warning") : ""}
      ${layer.range_risk !== undefined ? renderOptionPill(`RANGE ${String(layer.range_risk).toUpperCase()}`, rangeTone) : ""}
    </div>
    <div class="options-layer__grid">${fields.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(formatListValue(value))}</strong></div>`).join("")}</div>
    ${layer.summary_text ? `<p class="options-layer__summary">${escapeHtml(layer.summary_text)}</p>` : ""}
  </section>`;
}

function resolveVolumeDelta(idea) {
  const prop = getPropScore(idea);
  const vd = (idea?.volume_delta && typeof idea.volume_delta === "object")
    ? idea.volume_delta
    : (prop?.volume_delta && typeof prop.volume_delta === "object")
      ? prop.volume_delta
      : {};
  return {
    source: vd.source || idea?.volume_delta_source || prop?.volume_delta_source || "unavailable",
    delta: vd.delta,
    cumdelta: vd.cumdelta ?? vd.cum_delta ?? vd.cumulative_delta,
    isProxy: vd.is_proxy === true,
    priority: vd.priority_used ?? "—",
    divergence: Boolean(vd.delta_divergence || idea?.delta_divergence || prop?.delta_divergence),
    priceTrend: vd.price_trend || "—",
    cumdeltaTrend: vd.cumdelta_trend || "—",
  };
}

function volumeDeltaSourceLabel(source) {
  const raw = String(source || "unavailable");
  const labels = {
    FutureDelta: "FutureDelta",
    FutureVolume: "FutureVolume",
    tick_volume: "tick_volume",
    unavailable: "нет данных",
  };
  return labels[raw] || raw;
}

function renderVolumeDeltaCompact(idea) {
  const vd = resolveVolumeDelta(idea);
  return `<div class="volume-delta-pill ${vd.divergence ? "divergent" : ""}">
    <span>CumDelta source</span><strong>${escapeHtml(volumeDeltaSourceLabel(vd.source))}</strong>
    <em>${vd.isProxy ? "proxy" : "real"} · priority ${escapeHtml(vd.priority)}</em>
    <small>Δ ${escapeHtml(formatNumber(vd.delta))} · CumΔ ${escapeHtml(formatNumber(vd.cumdelta))}${vd.divergence ? " · Delta divergence" : ""}</small>
  </div>`;
}

function getIdeaSymbol(idea) {
  return String(idea.instrument || idea.symbol || idea.pair || "РЫНОК").toUpperCase();
}

function getIdeaDirectionRaw(idea) {
  return String(idea.signal || idea.label || idea.direction || idea.action || "WAIT").toUpperCase();
}

function getIdeaDirection(idea) {
  const raw = getIdeaDirectionRaw(idea);
  if (raw.includes("BUY") || raw.includes("ПОКУП")) return "Покупка";
  if (raw.includes("SELL") || raw.includes("ПРОДА")) return "Продажа";
  return "Наблюдение";
}

function getActionBadgeClass(idea) {
  const raw = getIdeaDirectionRaw(idea);
  if (raw.includes("BUY") || raw.includes("ПОКУП")) return "badge-buy";
  if (raw.includes("SELL") || raw.includes("ПРОДА")) return "badge-sell";
  return "badge-wait";
}

function getCardDirectionClass(idea) {
  const raw = getIdeaDirectionRaw(idea);
  if (raw.includes("BUY") || raw.includes("ПОКУП")) return "idea-card--buy";
  if (raw.includes("SELL") || raw.includes("ПРОДА")) return "idea-card--sell";
  return "idea-card--wait";
}

function getActionIcon(idea) {
  const raw = getIdeaDirectionRaw(idea);
  if (raw.includes("BUY") || raw.includes("ПОКУП")) return "🟢";
  if (raw.includes("SELL") || raw.includes("ПРОДА")) return "🔴";
  return "⛔";
}

function getPropScore(idea) {
  const score = idea?.prop_signal_score;
  if (score && typeof score === "object") return score;
  if (idea?.prop_score !== undefined || idea?.prop_grade || idea?.prop_mode) {
    return {
      score: Number(idea.prop_score) || 0,
      grade: idea.prop_grade || "D",
      mode: idea.prop_mode || "no_trade",
      decision_ru: idea.prop_decision_ru || "Оценка доступна частично.",
      blockers: [],
      criteria: [],
    };
  }
  return { score: 0, grade: "D", mode: "no_trade", decision_ru: "Prop Score недоступен.", blockers: [], criteria: [] };
}

function propModeLabel(mode) {
  const labels = {
    prop_entry: "PROP ENTRY",
    watchlist: "WATCHLIST",
    research_only: "RESEARCH ONLY",
    no_trade: "NO TRADE",
  };
  return labels[String(mode || "")] || String(mode || "нет данных");
}

function propGradeClass(grade) {
  const value = String(grade || "D").toLowerCase();
  return ["a", "b", "c", "d"].includes(value) ? `prop-grade-${value}` : "prop-grade-d";
}

function resolveNarrative(idea) {
  return firstText(
    idea.unified_narrative,
    idea.idea_thesis,
    idea.full_text,
    idea.article_ru,
    idea.journalistic_summary_ru,
    idea.confluence_summary_ru,
    idea.reason_ru,
    idea.description_ru,
    idea.decision_reason_ru,
    idea.fallback_narrative,
  ) || "Описание идеи временно недоступно.";
}

function resolveNewsContext(idea) {
  return firstText(
    idea.fundamental_summary_ru,
    idea.news_fundamental_ru,
    idea.newsFundamentalRu,
    idea.fundamental_context_ru,
    idea.fundamental_ru,
    idea.news_context_ru,
    idea.why_moves_ru,
    idea.market_impact_ru,
  ) || "Календарь новостей временно недоступен; фундаментальный слой не блокирует сделку.";
}

function normalizeChartImageUrl(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw) || raw.startsWith("/")) return raw;
  if (raw.startsWith("static/")) return `/${raw}`;
  if (raw.startsWith("./")) return `/${raw.slice(2)}`;
  return `/static/${raw.replace(/^\/+/, "")}`;
}

function collectCandles(idea) {
  const candidates = [
    idea.candles,
    idea.chartData?.candles,
    idea.chart_data?.candles,
    idea.chart?.candles,
    idea.market_data?.candles,
    idea.market_context?.candles,
    idea.history,
    idea.ohlc,
  ];
  for (const candidate of candidates) {
    if (Array.isArray(candidate) && candidate.length >= 2) return candidate;
  }
  return [];
}

function normalizeCandle(candle, index) {
  const time = candle.time || candle.timestamp || candle.t || Math.floor(Date.now() / 1000) - (200 - index) * 900;
  return {
    time: typeof time === "number" ? time : Math.floor(new Date(time).getTime() / 1000),
    open: Number(candle.open ?? candle.o ?? candle.close ?? candle.c),
    high: Number(candle.high ?? candle.h ?? candle.close ?? candle.c),
    low: Number(candle.low ?? candle.l ?? candle.close ?? candle.c),
    close: Number(candle.close ?? candle.c),
  };
}

function createIdeaStableKey(idea) {
  const explicitId = idea?.id ?? idea?.idea_id ?? idea?.uid ?? idea?._id;
  if (explicitId !== undefined && explicitId !== null && String(explicitId).trim()) return `id:${String(explicitId).trim()}`;
  return `fp:${getIdeaSymbol(idea)}|${getIdeaDirectionRaw(idea)}|${idea.entry ?? idea.entry_price ?? ""}|${idea.sl ?? idea.stop_loss ?? ""}|${idea.tp ?? idea.take_profit ?? idea.target ?? ""}`;
}

function createIdeaComparableState(idea) {
  const prop = getPropScore(idea);
  return {
    status: String(idea?.status ?? "").trim(),
    entry: String(idea?.entry ?? idea?.entry_price ?? "").trim(),
    sl: String(idea?.sl ?? idea?.stop_loss ?? "").trim(),
    tp: String(idea?.tp ?? idea?.take_profit ?? idea?.target ?? "").trim(),
    signal: String(idea?.signal ?? idea?.label ?? idea?.action ?? "").trim(),
    grade: String(prop?.grade ?? "").trim(),
    mode: String(prop?.mode ?? "").trim(),
  };
}

function isVoiceEnabled() {
  return localStorage.getItem(VOICE_STORAGE_KEY) === "1";
}

function setVoiceEnabled(isEnabled) {
  localStorage.setItem(VOICE_STORAGE_KEY, isEnabled ? "1" : "0");
}

function updateVoiceToggleLabel(button) {
  if (!button) return;
  button.textContent = `Голос: ${isVoiceEnabled() ? "ON" : "OFF"}`;
}

function initVoiceToggle() {
  if (!document.body || document.getElementById("voice-toggle-btn")) return;
  if (localStorage.getItem(VOICE_STORAGE_KEY) !== "1" && localStorage.getItem(VOICE_STORAGE_KEY) !== "0") setVoiceEnabled(false);
  const button = document.createElement("button");
  button.id = "voice-toggle-btn";
  button.type = "button";
  button.style.position = "fixed";
  button.style.top = "16px";
  button.style.right = "16px";
  button.style.zIndex = "999999";
  button.style.padding = "8px 12px";
  button.style.fontSize = "12px";
  button.style.borderRadius = "8px";
  button.style.cursor = "pointer";
  button.style.background = "#111827";
  button.style.color = "#f9fafb";
  button.style.border = "1px solid #374151";
  button.style.boxShadow = "0 4px 12px rgba(0, 0, 0, 0.35)";
  updateVoiceToggleLabel(button);
  button.addEventListener("click", () => {
    setVoiceEnabled(!isVoiceEnabled());
    updateVoiceToggleLabel(button);
  });
  document.body.appendChild(button);
}

function voiceSymbolLabel(symbolRaw) {
  const symbol = String(symbolRaw || "").trim().toUpperCase();
  if (symbol === "EURUSD") return "евродоллар";
  if (symbol === "USDJPY") return "доллар йена";
  if (symbol === "GBPUSD") return "фунт доллар";
  if (symbol === "XAUUSD") return "золото";
  return symbol || "инструмент";
}

function enqueueVoiceMessage(message) {
  if (!message || !("speechSynthesis" in window)) return;
  const now = Date.now();
  for (const [text, ts] of recentVoiceMessages.entries()) {
    if (now - ts > VOICE_REPEAT_WINDOW_MS) recentVoiceMessages.delete(text);
  }
  if (recentVoiceMessages.has(message)) return;
  recentVoiceMessages.set(message, now);
  voicePendingQueue.push(message);
  if (voicePendingQueue.length > VOICE_MAX_QUEUE) voicePendingQueue = voicePendingQueue.slice(-VOICE_MAX_QUEUE);
  if (voiceDebounceTimer) clearTimeout(voiceDebounceTimer);
  voiceDebounceTimer = setTimeout(() => {
    const batch = voicePendingQueue.splice(0, VOICE_MAX_QUEUE);
    batch.forEach((text) => {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "ru-RU";
      window.speechSynthesis.speak(utterance);
    });
  }, VOICE_DEBOUNCE_MS);
}

function collectVoiceNotifications(ideas) {
  const nextState = new Map();
  const notifications = [];
  ideas.forEach((idea) => {
    const prop = getPropScore(idea);
    const isAEntry = String(prop.grade).toUpperCase() === "A" && prop.mode === "prop_entry";
    const key = createIdeaStableKey(idea);
    const state = createIdeaComparableState(idea);
    nextState.set(key, state);
    const prev = previousIdeasState.get(key);
    if (isAEntry && (!prev || JSON.stringify(prev) !== JSON.stringify(state))) {
      notifications.push(`${voiceSymbolLabel(getIdeaSymbol(idea))}: сильный сигнал A, ${getIdeaDirection(idea)}`);
    }
  });
  previousIdeasState = nextState;
  return notifications;
}

function injectUiStyles() {
  if (document.getElementById("ideas-compact-ui-styles")) return;
  const style = document.createElement("style");
  style.id = "ideas-compact-ui-styles";
  style.textContent = `
    body { background:#06111f; color:#f4f8ff; }
    body::before { content:""; position:fixed; inset:0; pointer-events:none; background:radial-gradient(circle at 18% 8%, rgba(45,212,191,.18), transparent 28%), radial-gradient(circle at 82% 0%, rgba(244,63,94,.14), transparent 25%), linear-gradient(135deg, rgba(15,23,42,.92), rgba(2,6,23,.98)); z-index:-2; }
    body::after { content:""; position:fixed; inset:0; pointer-events:none; background-image:linear-gradient(rgba(148,163,184,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(148,163,184,.035) 1px, transparent 1px); background-size:42px 42px; mask-image:linear-gradient(to bottom, rgba(0,0,0,.75), transparent 82%); z-index:-1; }
    .page { max-width:1500px; margin:0 auto; padding:26px 24px 56px; }
    .page-shell { max-width:1500px; margin:0 auto; padding:26px 24px 56px; }
    .site-header { margin-bottom:20px; }
    .hero { position:relative; padding:4px 0 10px; }
    .ideas-page-header { position:relative; overflow:hidden; border:1px solid rgba(95,156,230,.22); border-radius:28px; padding:28px; background:linear-gradient(135deg, rgba(8,25,48,.9), rgba(3,14,28,.72)); box-shadow:0 28px 90px rgba(0,0,0,.42), inset 0 1px 0 rgba(255,255,255,.08); }
    .ideas-page-header::before { content:""; position:absolute; inset:-1px; background:radial-gradient(circle at 18% 0%, rgba(69,202,255,.22), transparent 34%), radial-gradient(circle at 85% 20%, rgba(84,255,181,.12), transparent 30%); pointer-events:none; }
    .ideas-page-header > * { position:relative; z-index:1; }
    .ideas-page-header h1, .site-header h1 { margin:6px 0; font-size:clamp(34px,4vw,58px); letter-spacing:-.04em; }
    .lead { color:#b9d6f8; }
    .idea-instrument, .nav-link { display:inline-flex; width:fit-content; padding:7px 12px; border-radius:999px; background:rgba(99,102,241,.18); color:#c7d2fe; border:1px solid rgba(99,102,241,.26); font-weight:800; text-decoration:none; font-size:12px; }
    .panel { background:rgba(8,25,48,.72); border:1px solid rgba(95,156,230,.2); border-radius:22px; padding:18px; }
    .prop-filter-row { display:flex; gap:10px; flex-wrap:wrap; margin:0 0 18px; }
    .prop-filter-btn { border:1px solid rgba(95,156,230,.46); background:rgba(3,14,28,.72); color:#dbeeff; border-radius:999px; padding:9px 13px; font-size:12px; font-weight:900; cursor:pointer; }
    .prop-filter-btn.active { background:rgba(69,202,255,.2); border-color:rgba(69,202,255,.72); }
    .market-status-row { display:flex; gap:10px; flex-wrap:wrap; margin-top:18px; }
    .health-pill { display:inline-flex; align-items:center; gap:7px; padding:8px 11px; border-radius:999px; border:1px solid rgba(95,156,230,.28); background:rgba(3,14,28,.68); color:#cfe7ff; font-size:12px; font-weight:850; }
    .health-pill.good { border-color:rgba(52,211,153,.36); box-shadow:0 0 22px rgba(52,211,153,.08); }
    .health-pill.warn { border-color:rgba(250,204,21,.34); color:#fde68a; }
    .ideas-container { display:grid; gap:18px; }
    .ideas-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:18px; }
    .ideas-loading { padding:18px; border:1px solid rgba(95,156,230,.25); border-radius:16px; background:rgba(3,14,28,.66); color:#b9d6f8; }
    .idea-card { position:relative; min-height:300px; padding:18px; border-radius:24px; border:1px solid transparent; background:linear-gradient(#061426,#061426) padding-box, linear-gradient(135deg, rgba(148,163,184,.45), rgba(69,202,255,.16)) border-box; box-shadow:0 28px 80px rgba(0,0,0,.44), inset 0 1px 0 rgba(255,255,255,.1); cursor:pointer; transition:transform .22s ease, box-shadow .22s ease, filter .22s ease; overflow:hidden; }
    .idea-card::before { content:""; position:absolute; inset:0; background:radial-gradient(circle at 80% 0%, rgba(69,202,255,.18), transparent 35%), linear-gradient(155deg, rgba(20,52,92,.82), rgba(3,14,28,.94) 72%); pointer-events:none; }
    .idea-card > * { position:relative; z-index:1; }
    .idea-card--buy { background:linear-gradient(#061426,#061426) padding-box, linear-gradient(135deg, #54ffb5, #45caff 55%, rgba(95,156,230,.2)) border-box; }
    .idea-card--sell { background:linear-gradient(#061426,#061426) padding-box, linear-gradient(135deg, #ff5f7a, #f472b6 55%, rgba(95,156,230,.2)) border-box; }
    .idea-card--wait { background:linear-gradient(#061426,#061426) padding-box, linear-gradient(135deg, #94a3b8, #475569 55%, rgba(95,156,230,.18)) border-box; }
    .idea-card:hover { transform:translateY(-6px); filter:saturate(1.08); box-shadow:0 36px 100px rgba(0,0,0,.56), 0 0 36px rgba(69,202,255,.12), inset 0 1px 0 rgba(255,255,255,.12); }
    .idea-card-top { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:12px; }
    .idea-title { margin:8px 0 6px; font-size:22px; line-height:1.15; }
    .idea-news-line { color:#b9d6f8; font-size:12px; line-height:1.5; }

    .options-layer { margin:12px 0; padding:13px; border-radius:18px; border:1px solid rgba(69,202,255,.28); background:linear-gradient(135deg, rgba(14,35,62,.82), rgba(3,14,28,.74)); box-shadow:inset 0 1px 0 rgba(255,255,255,.08); }
    .options-layer__head { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:10px; }
    .options-layer__head h4 { margin:0; color:#e8f3ff; font-size:13px; letter-spacing:.04em; text-transform:none; }
    .options-layer__head strong { color:#b9f8ff; font-size:12px; white-space:nowrap; }
    .options-layer__pills { display:flex; flex-wrap:wrap; gap:7px; margin:8px 0 10px; }
    .options-layer-pill { display:inline-flex; padding:6px 9px; border-radius:999px; border:1px solid rgba(148,163,184,.32); background:rgba(15,23,42,.72); color:#cbd5e1; font-size:10px; font-weight:950; letter-spacing:.05em; }
    .options-layer-pill--bullish { border-color:rgba(45,212,191,.52); color:#99f6e4; background:rgba(20,184,166,.12); }
    .options-layer-pill--bearish { border-color:rgba(244,114,182,.52); color:#fbcfe8; background:rgba(190,24,93,.16); }
    .options-layer-pill--neutral { border-color:rgba(96,165,250,.38); color:#bfdbfe; background:rgba(30,64,175,.15); }
    .options-layer-pill--warning, .options-layer-pill--high { border-color:rgba(250,204,21,.52); color:#fde68a; background:rgba(120,84,10,.22); }
    .options-layer-pill--medium { border-color:rgba(251,146,60,.45); color:#fed7aa; background:rgba(154,52,18,.18); }
    .options-layer-pill--low { border-color:rgba(45,212,191,.38); color:#a7f3d0; background:rgba(6,78,59,.16); }
    .options-layer__grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
    .options-layer__grid div { padding:8px 9px; border:1px solid rgba(95,156,230,.22); border-radius:12px; background:rgba(3,14,28,.58); min-width:0; }
    .options-layer__grid span { display:block; color:#9bb8d8; font-size:10px; font-weight:950; text-transform:uppercase; margin-bottom:3px; }
    .options-layer__grid strong { color:#f4f8ff; font-size:12px; overflow-wrap:anywhere; }
    .options-layer__summary, .options-layer__empty { margin:10px 0 0; color:#b9d6f8; font-size:12px; line-height:1.5; }
    .options-layer__empty { color:#94a3b8; }
    .volume-delta-pill { margin:10px 0; padding:10px 11px; border-radius:14px; border:1px solid rgba(69,202,255,.24); background:rgba(69,202,255,.08); display:grid; gap:2px; color:#dbeeff; }
    .volume-delta-pill span { color:#9bb8d8; font-size:10px; font-weight:950; text-transform:uppercase; letter-spacing:.06em; }
    .volume-delta-pill strong { color:#f4f8ff; font-size:14px; }
    .volume-delta-pill em, .volume-delta-pill small { color:#b9d6f8; font-style:normal; font-size:12px; }
    .volume-delta-pill.divergent { border-color:rgba(248,113,113,.48); background:rgba(127,29,29,.24); }
    .volume-delta-pill.divergent strong { color:#fecdd3; }
    .idea-label,.badge { border-radius:999px; padding:8px 12px; font-size:12px; font-weight:950; white-space:nowrap; }
    .badge-buy,.idea-label-buy { color:#00150c; background:linear-gradient(180deg,#54ffb5,#31f59d); }
    .badge-sell,.idea-label-sell { color:#fff; background:linear-gradient(180deg,#d93f5b,#8f2034); }
    .badge-wait,.idea-label-watch { color:#efeaff; background:linear-gradient(180deg,#5266bd,#293c78); }
    .compact-levels { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin:12px 0; }
    .compact-levels div, .modal-meta div { padding:9px 10px; border:1px solid rgba(95,156,230,.28); border-radius:12px; background:rgba(3,14,28,.62); }
    .compact-levels span, .modal-meta span { display:block; color:#9bb8d8; font-size:10px; font-weight:950; text-transform:uppercase; margin-bottom:3px; }
    .compact-levels strong, .modal-meta strong { color:#f4f8ff; font-size:13px; }
    .status-pill-row { display:flex; flex-wrap:wrap; gap:7px; margin:12px 0; }
    .status-pill { display:inline-flex; align-items:center; gap:6px; padding:7px 10px; border-radius:999px; border:1px solid rgba(95,156,230,.28); background:rgba(3,14,28,.7); color:#dbeeff; font-size:11px; font-weight:950; letter-spacing:.04em; }
    .status-pill.hot { border-color:rgba(84,255,181,.45); color:#9fffd0; }
    .status-pill.danger { border-color:rgba(248,113,113,.42); color:#fecdd3; }
    .status-pill.warn { border-color:rgba(250,204,21,.38); color:#fde68a; }
    .compact-score { margin-top:12px; padding:14px; border-radius:18px; background:linear-gradient(135deg, rgba(69,202,255,.13), rgba(139,92,246,.08)); border:1px solid rgba(69,202,255,.28); box-shadow:inset 0 1px 0 rgba(255,255,255,.08); }
    .compact-score-head { display:flex; justify-content:space-between; align-items:center; gap:10px; }
    .compact-score strong { font-size:16px; }
    .prop-grade-badge { display:inline-flex; align-items:center; justify-content:center; min-width:46px; min-height:46px; border-radius:14px; padding:8px; font-size:22px; font-weight:950; }
    .prop-grade-a { color:#022616; background:linear-gradient(180deg,#8dffc9,#10b981); }
    .prop-grade-b { color:#251a00; background:linear-gradient(180deg,#fff08a,#facc15); }
    .prop-grade-c { color:#291100; background:linear-gradient(180deg,#ffc478,#fb923c); }
    .prop-grade-d { color:#fff2f5; background:linear-gradient(180deg,#ff7f99,#be123c); }
    .score-meter { height:10px; margin-top:10px; border-radius:999px; overflow:hidden; background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.1); }
    .score-fill { height:100%; border-radius:inherit; background:linear-gradient(90deg,#ef4444,#f59e0b,#22c55e,#45caff); animation:scoreSweep 1.2s ease both; box-shadow:0 0 18px rgba(69,202,255,.38); }
    @keyframes scoreSweep { from { width:0; } }
    .institutional-sections { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:12px; }
    .institutional-section { padding:10px; border-radius:14px; border:1px solid rgba(95,156,230,.22); background:rgba(3,14,28,.58); min-width:0; }
    .institutional-section h4 { margin:0 0 6px; color:#b9d6f8; font-size:11px; text-transform:uppercase; letter-spacing:.08em; }
    .institutional-section p { margin:0; color:#e8f3ff; font-size:12px; line-height:1.45; overflow-wrap:anywhere; }
    .idea-summary-compact { margin-top:12px; color:#dbeeff; font-size:13px; line-height:1.55; max-height:62px; overflow:hidden; }
    .ideas-modal-backdrop { position:fixed; inset:0; z-index:9999; display:none; align-items:center; justify-content:center; padding:20px; background:rgba(0,0,0,.78); backdrop-filter:blur(10px); }
    .ideas-modal-backdrop.open { display:flex; }
    .ideas-modal-card { width:min(1500px,96vw); height:92vh; overflow:hidden; display:flex; flex-direction:column; border-radius:26px; background:linear-gradient(160deg,rgba(24,58,103,.98),rgba(5,17,33,.98) 72%); border:1px solid rgba(124,184,255,.62); box-shadow:0 34px 110px rgba(0,0,0,.68); }
    .ideas-modal-header { flex:0 0 auto; padding:18px 20px; display:flex; justify-content:space-between; gap:16px; border-bottom:1px solid rgba(255,255,255,.08); }
    .ideas-modal-title { margin:0; font-size:clamp(24px,2.8vw,36px); }
    .ideas-modal-close { border:1px solid rgba(255,255,255,.16); background:rgba(3,14,28,.7); color:#fff; border-radius:12px; padding:8px 12px; cursor:pointer; height:fit-content; }
    .ideas-modal-body { flex:1; overflow:auto; padding:18px 20px 24px; display:grid; gap:16px; }
    .modal-grid { display:grid; grid-template-columns:1fr 1.5fr; gap:16px; }
    .modal-section { padding:15px; border-radius:18px; border:1px solid rgba(95,156,230,.25); background:rgba(3,14,28,.62); }
    .modal-section h4 { margin:0 0 10px; color:#b9d6f8; text-transform:uppercase; letter-spacing:.08em; font-size:12px; }
    .modal-text { line-height:1.7; font-size:14px; color:#e8f3ff; }
    .modal-meta { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; }
    .chart-area { min-height:520px; border-radius:18px; overflow:hidden; background:#06111f; border:1px solid rgba(95,156,230,.28); }
    #ideaModalChart { width:100%; height:520px; }
    .chart-image { width:100%; max-height:560px; object-fit:contain; display:block; background:#06111f; }
    .criteria-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
    .criterion { padding:10px; border-radius:12px; border:1px solid rgba(95,156,230,.22); background:rgba(3,14,28,.62); }
    .criterion.confirmed { border-color:rgba(34,197,94,.36); }
    .criterion.partial { border-color:rgba(250,204,21,.32); }
    .criterion.missing { border-color:rgba(248,113,113,.28); opacity:.82; }
    .blocker { padding:10px; border-radius:12px; border:1px solid rgba(248,113,113,.25); background:rgba(127,29,29,.22); color:#fecdd3; }
    @media(max-width:1100px){ .ideas-grid{grid-template-columns:repeat(2,minmax(0,1fr));} .modal-grid{grid-template-columns:1fr;} .modal-meta{grid-template-columns:repeat(2,minmax(0,1fr));} }
    @media(max-width:720px){ .page{padding:16px 12px 42px;} .ideas-page-header{padding:20px;} .ideas-grid{grid-template-columns:1fr;} .compact-levels,.criteria-grid,.modal-meta,.institutional-sections{grid-template-columns:1fr;} .idea-card-top{flex-direction:column;} .badge{white-space:normal;} .chart-area,#ideaModalChart{height:390px;min-height:390px;} }
  `;
  document.head.appendChild(style);
}

function renderPropCompact(idea) {
  const prop = getPropScore(idea);
  const score = Math.max(0, Math.min(100, Number(prop.score) || 0));
  const grade = String(prop.grade || "D").toUpperCase();
  return `<div class="compact-score">
    <div class="compact-score-head">
      <div><span class="idea-news-line">PROP DECISION ENGINE</span><br><strong>Score ${escapeHtml(score)} / 100 · ${escapeHtml(propModeLabel(prop.mode))}</strong></div>
      <div class="prop-grade-badge ${propGradeClass(grade)}">${escapeHtml(grade)}</div>
    </div>
    <div class="score-meter"><div class="score-fill" style="width:${score}%"></div></div>
  </div>`;
}

function resolveDpoc(idea) {
  const dpocPrice = pickFirstFiniteNumber(idea.dpoc_price, idea.market_structure?.dpoc_price, idea.dpoc?.dpoc_price);
  const distance = pickFirstFiniteNumber(idea.distance_to_dpoc_pips, idea.market_structure?.distance_to_dpoc_pips, idea.dpoc?.distance_to_dpoc_pips);
  return {
    price: dpocPrice !== null && dpocPrice > 0 ? formatNumber(dpocPrice) : "—",
    distance: distance !== null ? `${distance > 0 ? "+" : ""}${distance.toFixed(1)} пипс` : "—",
  };
}

function valueOrDash(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return "—";
}

function renderStatusPills(idea) {
  const prop = getPropScore(idea);
  const grade = String(prop.grade || "D").toUpperCase();
  const mode = String(prop.mode || "");
  const vd = resolveVolumeDelta(idea);
  const optionsBias = String(resolveExternalOptionsBias(idea)).toLowerCase();
  const pills = [
    ["ACTIVE", "hot", true],
    ["🚀 PROP ENTRY", "hot", mode === "prop_entry"],
    [`GRADE ${grade}`, grade === "A" || grade === "B" ? "hot" : grade === "C" ? "warn" : "danger", true],
    ["📰 NEWS LOCK", "danger", Boolean(idea.news_lock_active)],
    ["🔥 HEATMAP", "hot", Boolean(idea.heatmap_available)],
    ["⚡ DELTA DIVERGENCE", "warn", vd.divergence || Boolean(idea.cvd_divergence)],
    ["🧩 OPTIONS ALIGNED", "hot", ["bullish", "bearish", "aligned", "buy", "sell"].includes(optionsBias)],
  ].filter(([, , show]) => show);
  return `<div class="status-pill-row">${pills.map(([label, cls]) => `<span class="status-pill ${cls}">${escapeHtml(label)}</span>`).join("")}</div>`;
}

function renderInstitutionalSections(idea) {
  const vd = resolveVolumeDelta(idea);
  const newsEvent = valueOrDash(idea.news_event, resolveNewsContext(idea));
  const newsImpact = valueOrDash(idea.news_impact, idea.impact, "—");
  const minutes = valueOrDash(idea.minutes_to_event, idea.news_minutes_to_event, "—");
  const heatmap = idea.heatmap_available
    ? `🔥 ${valueOrDash(idea.heatmap_bias)} · wall ↑ ${valueOrDash(idea.heatmap_wall_above)} / ↓ ${valueOrDash(idea.heatmap_wall_below)}`
    : "🔥 heatmap: нет подтверждённых данных";
  return `<div class="institutional-sections">
    <section class="institutional-section"><h4>Market Structure</h4><p>BOS ${escapeHtml(valueOrDash(idea.market_structure?.bos))} · Sweep ${escapeHtml(valueOrDash(idea.liquidity?.sweep))} · HTF ${escapeHtml(valueOrDash(idea.htf_bias, idea.market_structure?.trend_regime))}</p></section>
    <section class="institutional-section"><h4>Orderflow</h4><p>⚡ DOM ${escapeHtml(valueOrDash(idea.dom_bias))} · Absorption ${escapeHtml(valueOrDash(idea.absorption))} · CVD div ${escapeHtml(valueOrDash(idea.cvd_divergence, vd.divergence))}<br>${escapeHtml(heatmap)}</p></section>
    <section class="institutional-section"><h4>Options</h4><p>🧩 Bias ${escapeHtml(resolveExternalOptionsBias(idea))} · Max Pain ${escapeHtml(formatListValue(resolveOptionsMaxPain(idea)))} · Strikes ${escapeHtml(formatListValue(resolveOptionsKeyStrikes(idea)))}</p></section>
    <section class="institutional-section"><h4>News/Fundamental</h4><p>📰 ${escapeHtml(newsEvent)} · Impact ${escapeHtml(newsImpact)} · до события ${escapeHtml(minutes)} мин.</p></section>
    <section class="institutional-section"><h4>Risk/Execution</h4><p>🛡️ Execution ${escapeHtml(valueOrDash(idea.execution_score))} · Final ${escapeHtml(valueOrDash(idea.final_score, idea.score))} · Risk ${escapeHtml(valueOrDash(idea.risk_per_trade_pct, idea.recommended_risk_percent))}%</p></section>
  </div>`;
}

function getAiSourceMeta(idea) {
  const source = String(idea?.narrative_source || idea?.ai_status || idea?.llm_source || idea?.ai_provider || "").toLowerCase();
  const isFallback = Boolean(idea?.is_fallback || idea?.fallback_used || idea?.ai_fallback_used) || /fallback/.test(source);
  if (/grok|openrouter|model|llm/.test(source) && !isFallback) return { label: "grok", tone: "grok", line: "narrative_source = grok" };
  if (isFallback) return { label: "fallback", tone: "fallback", line: "narrative_source = fallback" };
  return { label: "fallback", tone: "rule", line: "narrative_source = fallback" };
}

function renderIdeaCard(idea, index) {
  const symbol = getIdeaSymbol(idea);
  const action = idea.action || idea.signal || idea.label || "WAIT";
  const dpoc = resolveDpoc(idea);
  const aiSource = getAiSourceMeta(idea);
  return `<article class="idea-card ${getCardDirectionClass(idea)}" data-idea-index="${index}" tabindex="0" role="button" aria-label="Открыть идею ${escapeHtml(symbol)}">
    <div class="idea-card-top">
      <div>
        <div class="idea-instrument">${escapeHtml(symbol)}</div>
        <h3 class="idea-title">${escapeHtml(symbol)} · AI-идея</h3>
        <div class="idea-news-line">Новости/фундаментал: ${escapeHtml(resolveNewsContext(idea))}</div>
        <div class="idea-news-line">Статус: <strong>${escapeHtml(idea.trade_permission === false ? "ожидание" : idea.status || "активно")}</strong></div>
      </div>
      <div class="badge ${getActionBadgeClass(idea)}">${getActionIcon(idea)} ${escapeHtml(action)}</div>
    </div>
    <div class="compact-levels">
      <div><span>Setup</span><strong>${escapeHtml(idea.setup_type || "—")}</strong></div>
      <div><span>Entry</span><strong>${escapeHtml(formatNumber(idea.entry ?? idea.entry_price))}</strong></div>
      <div><span>SL</span><strong>${escapeHtml(formatNumber(idea.sl ?? idea.stop_loss))}</strong></div>
      <div><span>TP</span><strong>${escapeHtml(formatNumber(idea.tp ?? idea.take_profit ?? idea.target))}</strong></div>
      <div><span>R/R</span><strong>${escapeHtml(formatNumber(idea.rr ?? idea.risk_reward))}</strong></div>
      <div><span>DPOC / дистанция</span><strong>${escapeHtml(dpoc.price)} · ${escapeHtml(dpoc.distance)}</strong></div>
    </div>
    ${renderStatusPills(idea)}
    <div class="ai-source-row">
      <span>AI Source:</span>
      <span class="ai-source-chip ai-source-chip-${escapeHtml(aiSource.tone)}">${escapeHtml(aiSource.label)}</span>
      <span class="ai-generated-line">${escapeHtml(aiSource.line)}</span>
    </div>
    ${renderPropCompact(idea)}
    ${renderVolumeDeltaCompact(idea)}
    ${renderOptionsLayer(idea, { compact: true })}
    ${renderInstitutionalSections(idea)}
    ${idea.institutional_thesis ? `<div class="idea-news-line"><strong>Institutional Thesis:</strong> ${escapeHtml(idea.institutional_thesis)}</div>` : ""}
    <div class="idea-summary-compact">${escapeHtml(resolveNarrative(idea))}</div>
  </article>`;
}


function renderExecutionAnalysis(idea) {
  const rows = [
    ["Killzone", `${idea.killzone_status || "—"} (${idea.killzone_bonus ?? "—"})`, idea.killzone_reason_ru || "—"],
    ["ATR", `${formatNumber(idea.atr_pips)} пипс`, idea.atr_filter_passed === false ? "Ниже prop-порога" : "Фильтр пройден"],
    ["RVOL", formatNumber(idea.rvol), idea.rvol_status || "—"],
    ["VWAP", formatNumber(idea.vwap), idea.vwap_alignment === true ? "Согласован с направлением" : idea.vwap_alignment === false ? "Против направления" : "—"],
    ["News Lock", idea.news_lock_active ? "ACTIVE" : "OFF", idea.news_minutes_to_event ?? "—"],
    ["Correlation", idea.correlation_block ? "BLOCK" : "OK", `USD exposure: ${idea.usd_exposure_count ?? "—"}`],
    ["Regime", idea.market_regime || "—", `Score: ${idea.regime_score ?? "—"}`],
    ["Dynamic Risk", `${idea.risk_per_trade_pct ?? idea.recommended_risk_percent ?? "—"}%`, `Lot: ${idea.recommended_lot ?? "—"}`],
  ];
  return `<section class="modal-section execution-analysis" style="margin-top:16px;">
    <h4>Execution Analysis</h4>
    <div class="modal-meta">
      <div><span>Base score</span><strong>${escapeHtml(idea.base_score ?? "—")}</strong></div>
      <div><span>Execution score</span><strong>${escapeHtml(idea.execution_score ?? "—")}</strong></div>
      <div><span>Final score</span><strong>${escapeHtml(idea.final_score ?? idea.score ?? "—")}</strong></div>
      <div><span>Mode</span><strong>${escapeHtml(idea.mode || "—")}</strong></div>
    </div>
    <div class="criteria-grid">${rows.map(([label, value, note]) => `<div class="criterion"><strong>${escapeHtml(label)}</strong><br>${escapeHtml(value)} · ${escapeHtml(note)}</div>`).join("")}</div>
  </section>`;
}

function renderPropDetails(idea) {
  const prop = getPropScore(idea);
  const score = Math.max(0, Math.min(100, Number(prop.score) || 0));
  const grade = String(prop.grade || "D").toUpperCase();
  const criteria = asArray(prop.criteria);
  const blockers = asArray(prop.blockers).filter(Boolean);
  return `<section class="modal-section">
    <h4>Prop Decision Engine</h4>
    <div class="compact-score" style="margin:0 0 12px;">
      <div class="compact-score-head">
        <div><strong>Score ${escapeHtml(score)} / 100 · ${escapeHtml(propModeLabel(prop.mode))}</strong></div>
        <div class="prop-grade-badge ${propGradeClass(grade)}">${escapeHtml(grade)}</div>
      </div>
      <div class="score-meter"><div class="score-fill" style="width:${score}%"></div></div>
    </div>
    <div class="modal-meta">
      <div><span>Decision</span><strong>${escapeHtml(propModeLabel(prop.mode))}</strong></div>
      <div><span>Direction</span><strong>${escapeHtml(prop.direction || getIdeaDirectionRaw(idea))}</strong></div>
      <div><span>Grade</span><strong>${escapeHtml(grade)}</strong></div>
      <div><span>Advisor</span><strong>${idea.advisor_allowed ? "ALLOWED" : "BLOCKED"}</strong></div>
    </div>
    <p class="modal-text">${escapeHtml(prop.decision_ru || idea.prop_decision_ru || "Решение недоступно.")}</p>
    ${blockers.length ? `<h4>Blockers</h4><div class="criteria-grid">${blockers.map((b) => `<div class="blocker">❌ ${escapeHtml(b)}</div>`).join("")}</div>` : ""}
    ${criteria.length ? `<h4>Критерии score</h4><div class="criteria-grid">${criteria.map((item) => `<div class="criterion ${escapeHtml(item.status || "missing")}"><strong>${escapeHtml(item.label_ru || item.key)}</strong><br>${escapeHtml(item.score ?? 0)} / ${escapeHtml(item.weight ?? "—")} · ${escapeHtml(item.status || "—")}</div>`).join("")}</div>` : ""}
  </section>`;
}

function renderChartContainer(idea) {
  const imageUrl = normalizeChartImageUrl(idea.chartImageUrl || idea.chart_image || idea.chart_url || "");
  if (imageUrl) return `<img class="chart-image" src="${escapeHtml(imageUrl)}?t=${Date.now()}" alt="График ${escapeHtml(getIdeaSymbol(idea))}">`;
  return `<div id="ideaModalChart"></div>`;
}

function openIdeaModal(idea) {
  const symbol = getIdeaSymbol(idea);
  const modal = document.getElementById("ideasModal") || createIdeasModal();
  const body = modal.querySelector(".ideas-modal-body");
  const title = modal.querySelector(".ideas-modal-title");
  const zoneType = sanitizeText(idea.selected_zone_type);
  const dpoc = resolveDpoc(idea);
  const aiSource = getAiSourceMeta(idea);
  title.textContent = zoneType ? `${symbol} · ${getIdeaDirection(idea)} · ${zoneType}` : `${symbol} · ${getIdeaDirection(idea)}`;
  body.innerHTML = `<div class="modal-grid">
      <div>
        ${renderPropDetails(idea)}
        ${renderExecutionAnalysis(idea)}
        <section class="modal-section" style="margin-top:16px;">
          <h4>Smart Money Narrative</h4>
          <p class="modal-text"><strong>narrative_source:</strong> ${escapeHtml(idea.narrative_source || aiSource.label)}</p>
          ${idea.institutional_thesis ? `<p class="modal-text"><strong>Institutional Thesis:</strong> ${escapeHtml(idea.institutional_thesis)}</p>` : ""}
          <div class="modal-text">${escapeHtml(resolveNarrative(idea))}</div>
        </section>
      </div>
      <section class="modal-section">
        <h4>График</h4>
        <div class="chart-area">${renderChartContainer(idea)}</div>
      </section>
    </div>
    <section class="modal-section">
      <h4>Уровни и контекст</h4>
      <div class="modal-meta">
        <div><span>Entry</span><strong>${escapeHtml(formatNumber(idea.entry ?? idea.entry_price))}</strong></div>
        <div><span>SL</span><strong>${escapeHtml(formatNumber(idea.sl ?? idea.stop_loss))}</strong></div>
        <div><span>TP</span><strong>${escapeHtml(formatNumber(idea.tp ?? idea.take_profit ?? idea.target))}</strong></div>
        <div><span>R/R</span><strong>${escapeHtml(formatNumber(idea.rr ?? idea.risk_reward))}</strong></div>
        <div><span>DPOC</span><strong>${escapeHtml(dpoc.price)}</strong></div>
        <div><span>До DPOC</span><strong>${escapeHtml(dpoc.distance)}</strong></div>
      </div>
      <p class="modal-text"><strong>Новости/фундаментал:</strong> ${escapeHtml(resolveNewsContext(idea))}</p>
      ${renderOptionsLayer(idea)}
      <p class="modal-text"><strong>CumDelta source:</strong> ${escapeHtml(volumeDeltaSourceLabel(resolveVolumeDelta(idea).source))}; <strong>Delta divergence:</strong> ${resolveVolumeDelta(idea).divergence ? "true" : "false"}; <strong>Price/CumDelta:</strong> ${escapeHtml(resolveVolumeDelta(idea).priceTrend)} / ${escapeHtml(resolveVolumeDelta(idea).cumdeltaTrend)}</p>
      <p class="modal-text"><strong>Источник:</strong> ${escapeHtml(idea.data_provider || idea.provider || "нет данных")}</p>
      <p class="modal-text"><strong>Setup:</strong> ${escapeHtml(idea.setup_type || "—")}; <strong>BOS:</strong> ${escapeHtml(idea.market_structure?.bos || "—")}; <strong>Sweep:</strong> ${escapeHtml(idea.liquidity?.sweep || "—")}; <strong>FVG:</strong> ${escapeHtml(idea.fvg?.type || idea.selected_zone_type || "—")}; <strong>HTF bias:</strong> ${escapeHtml(idea.htf_bias || idea.market_structure?.trend_regime || "—")}</p>
    </section>`;
  modal.classList.add("open");
  document.body.style.overflow = "hidden";
  requestAnimationFrame(() => renderModalChart(idea));
}

function createIdeasModal() {
  const modal = document.createElement("div");
  modal.id = "ideasModal";
  modal.className = "ideas-modal-backdrop";
  modal.innerHTML = `<div class="ideas-modal-card" role="dialog" aria-modal="true">
    <div class="ideas-modal-header">
      <div><h2 class="ideas-modal-title"></h2><div class="idea-news-line">Клик вне окна или Esc закрывает карточку</div></div>
      <button class="ideas-modal-close" type="button">Закрыть</button>
    </div>
    <div class="ideas-modal-body"></div>
  </div>`;
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeIdeaModal();
  });
  modal.querySelector(".ideas-modal-close").addEventListener("click", closeIdeaModal);
  document.body.appendChild(modal);
  return modal;
}

function closeIdeaModal() {
  const modal = document.getElementById("ideasModal");
  if (!modal) return;
  modal.classList.remove("open");
  document.body.style.overflow = "";
  if (modalChart) {
    modalChart.remove();
    modalChart = null;
  }
  if (modalOverlayCanvas) {
    modalOverlayCanvas.remove();
    modalOverlayCanvas = null;
    modalOverlayContext = null;
    modalOverlayState = null;
  }
  isIdeasLoading = false;
}

function renderModalChart(idea) {
  const container = document.getElementById("ideaModalChart");
  if (!container || !("LightweightCharts" in window)) return;
  container.style.position = "relative";
  const candles = collectCandles(idea).map(normalizeCandle).filter((c) => Number.isFinite(c.open) && Number.isFinite(c.high) && Number.isFinite(c.low) && Number.isFinite(c.close));
  if (candles.length < 2) {
    container.innerHTML = `<div class="ideas-loading">График недоступен: API не передал свечи или chartImageUrl. Уровни идеи всё равно показаны выше.</div>`;
    return;
  }
  if (modalChart) {
    try { modalChart.remove(); } catch {}
  }
  modalChart = LightweightCharts.createChart(container, {
    layout: { background: { color: "#06111f" }, textColor: "#dbeeff" },
    grid: { vertLines: { color: "rgba(255,255,255,.06)" }, horzLines: { color: "rgba(255,255,255,.06)" } },
    rightPriceScale: { borderColor: "rgba(95,156,230,.24)" },
    timeScale: { borderColor: "rgba(95,156,230,.24)" },
  });
  const series = modalChart.addCandlestickSeries({
    upColor: "#31f59d",
    downColor: "#ff5f7a",
    borderUpColor: "#31f59d",
    borderDownColor: "#ff5f7a",
    wickUpColor: "#31f59d",
    wickDownColor: "#ff5f7a",
  });
  series.setData(candles);
  mountOverlayControls(container);
  ensureModalOverlayCanvas(container);
  const entry = Number(idea.entry ?? idea.entry_price);
  const sl = Number(idea.sl ?? idea.stop_loss);
  const tp = Number(idea.tp ?? idea.take_profit ?? idea.target);
  if (Number.isFinite(entry)) series.createPriceLine({ price: entry, color: "#ffd84d", lineWidth: 2, title: "ENTRY" });
  if (Number.isFinite(sl)) series.createPriceLine({ price: sl, color: "#ff5f7a", lineWidth: 2, title: "SL" });
  if (Number.isFinite(tp)) series.createPriceLine({ price: tp, color: "#31f59d", lineWidth: 2, title: "TP" });
  const dpocPrice = Number(idea.dpoc_price ?? idea.market_structure?.dpoc_price ?? idea.dpoc?.dpoc_price);
  if (Number.isFinite(dpocPrice) && dpocPrice > 0) series.createPriceLine({ price: dpocPrice, color: "#f59e0b", lineWidth: 2, lineStyle: 2, title: "DPOC" });

  const selectedZoneLow = pickFirstFiniteNumber(idea.selected_zone_low, idea.selectedZoneLow);
  const selectedZoneHigh = pickFirstFiniteNumber(idea.selected_zone_high, idea.selectedZoneHigh);
  if (selectedZoneLow !== null) series.createPriceLine({ price: selectedZoneLow, color: "#5cc8ff", lineWidth: 2, lineStyle: 1, title: "SELECTED ZONE LOW" });
  if (selectedZoneHigh !== null) series.createPriceLine({ price: selectedZoneHigh, color: "#5cc8ff", lineWidth: 2, lineStyle: 1, title: "SELECTED ZONE HIGH" });

  const orderBlockRanges = collectOverlayRanges(idea.order_blocks ?? idea.orderBlocks);
  orderBlockRanges.forEach((range) => {
    series.createPriceLine({ price: range.low, color: "#ff9f43", lineWidth: 1, lineStyle: 1, title: "OB" });
    if (range.high !== range.low) series.createPriceLine({ price: range.high, color: "#ff9f43", lineWidth: 1, lineStyle: 1, title: "OB" });
  });

  const fvgRanges = collectOverlayRanges(idea.fvg ?? idea.fair_value_gaps);
  fvgRanges.forEach((range) => {
    series.createPriceLine({ price: range.low, color: "#8b7bff", lineWidth: 1, lineStyle: 1, title: "FVG" });
    if (range.high !== range.low) series.createPriceLine({ price: range.high, color: "#8b7bff", lineWidth: 1, lineStyle: 1, title: "FVG" });
  });

  const liquidityValues = [
    ...(Array.isArray(idea.liquidity) ? idea.liquidity : []),
    ...(Array.isArray(idea.liquidity_levels) ? idea.liquidity_levels : []),
    ...(Array.isArray(idea.liquidity_zones) ? idea.liquidity_zones : []),
  ];
  collectOverlayRanges(liquidityValues).forEach((range) => {
    series.createPriceLine({ price: range.low, color: "#34d399", lineWidth: 1, lineStyle: 2, title: "LIQ" });
    if (range.high !== range.low) series.createPriceLine({ price: range.high, color: "#34d399", lineWidth: 1, lineStyle: 2, title: "LIQ" });
  });

  const optionsLevels = [
    ...(Array.isArray(idea.options_analysis?.keyLevels) ? idea.options_analysis.keyLevels : []),
    ...(Array.isArray(idea.keyStrikes) ? idea.keyStrikes : []),
  ];
  collectOverlayRanges(optionsLevels).forEach((range) => {
    series.createPriceLine({ price: range.low, color: "#f472b6", lineWidth: 1, lineStyle: 3, title: "OPT" });
    if (range.high !== range.low) series.createPriceLine({ price: range.high, color: "#f472b6", lineWidth: 1, lineStyle: 3, title: "OPT" });
  });

  modalChart.timeScale().fitContent();
  renderInstitutionalOverlay({ idea, candles, series });
  const redraw = () => renderInstitutionalOverlay({ idea, candles, series });
  modalChart.timeScale().subscribeVisibleLogicalRangeChange(redraw);
  modalChart.timeScale().subscribeVisibleTimeRangeChange(redraw);
  window.addEventListener("resize", redraw, { passive: true });
}

function mountOverlayControls(container) {
  const old = container.querySelector(".smc-overlay-toggles");
  if (old) old.remove();
  const controls = document.createElement("div");
  controls.className = "smc-overlay-toggles";
  controls.style.cssText = "position:absolute;top:8px;left:8px;z-index:40;display:flex;gap:6px;flex-wrap:wrap;";
  const items = [
    { key: "fvg", label: "FVG" },
    { key: "ob", label: "OB" },
    { key: "liquidity", label: "Ликвидность" },
    { key: "structure", label: "Структура" },
    { key: "signals", label: "Сигналы" },
  ];
  items.forEach(({ key, label }) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label;
    btn.style.cssText = "border:1px solid rgba(150,190,255,.35);background:rgba(4,17,31,.72);color:#e7f0ff;border-radius:10px;padding:4px 8px;font-size:11px;cursor:pointer;";
    if (!modalOverlayVisibility[key]) btn.style.opacity = "0.4";
    btn.addEventListener("click", () => {
      modalOverlayVisibility[key] = !modalOverlayVisibility[key];
      btn.style.opacity = modalOverlayVisibility[key] ? "1" : "0.4";
      modalOverlayState?.redraw?.();
    });
    controls.appendChild(btn);
  });
  container.appendChild(controls);
}

function ensureModalOverlayCanvas(container) {
  if (modalOverlayCanvas) modalOverlayCanvas.remove();
  modalOverlayCanvas = document.createElement("canvas");
  modalOverlayCanvas.style.cssText = "position:absolute;inset:0;pointer-events:none;z-index:30;";
  container.appendChild(modalOverlayCanvas);
  modalOverlayContext = modalOverlayCanvas.getContext("2d");
}

function fitModalOverlayCanvas() {
  if (!modalOverlayCanvas || !modalOverlayContext) return;
  const rect = modalOverlayCanvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  modalOverlayCanvas.width = Math.max(1, Math.floor(rect.width * dpr));
  modalOverlayCanvas.height = Math.max(1, Math.floor(rect.height * dpr));
  modalOverlayContext.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function renderInstitutionalOverlay({ idea, candles, series }) {
  if (!modalChart || !modalOverlayCanvas || !modalOverlayContext || !candles.length) return;
  fitModalOverlayCanvas();
  const ctx = modalOverlayContext;
  const width = modalOverlayCanvas.clientWidth;
  const height = modalOverlayCanvas.clientHeight;
  ctx.clearRect(0, 0, width, height);
  const maxIndex = candles.length - 1;
  const timeScale = modalChart.timeScale();
  const xAt = (idx) => timeScale.timeToCoordinate(candles[Math.max(0, Math.min(maxIndex, idx))]?.time);
  const yAt = (price) => series.priceToCoordinate(Number(price));
  const drawZone = (range, style, label) => {
    const y1 = yAt(range.high);
    const y2 = yAt(range.low);
    const x1 = xAt(range.from_index ?? (maxIndex - 60));
    const x2 = xAt(range.to_index ?? maxIndex);
    if (y1 == null || y2 == null || x1 == null || x2 == null) return;
    const left = Math.min(x1, x2);
    const top = Math.min(y1, y2);
    ctx.fillStyle = style.fill;
    ctx.strokeStyle = style.stroke;
    ctx.fillRect(left, top, Math.max(6, Math.abs(x2 - x1)), Math.max(4, Math.abs(y2 - y1)));
    ctx.strokeRect(left, top, Math.max(6, Math.abs(x2 - x1)), Math.max(4, Math.abs(y2 - y1)));
    ctx.fillStyle = style.stroke;
    ctx.font = "11px Inter, sans-serif";
    ctx.fillText(label, left + 4, Math.max(11, top - 3));
  };
  const orderBlocks = collectOverlayRanges(idea.order_blocks ?? idea.orderBlocks ?? idea.chart_overlays?.order_blocks).slice(-8);
  const fvgs = collectOverlayRanges(idea.fvg ?? idea.fair_value_gaps ?? idea.chart_overlays?.fvg).slice(-10);
  const liquidity = collectOverlayRanges([...(idea.liquidity || []), ...(idea.liquidity_levels || []), ...(idea.chart_overlays?.liquidity || [])]).slice(-10);
  const structure = collectOverlayRanges(idea.structure_levels ?? idea.chart_overlays?.structure_levels ?? []).slice(-8);
  const entries = collectOverlayRanges(idea.entry_zones ?? idea.chart_overlays?.entry_zones ?? []).slice(-4);
  const premiumDiscount = collectOverlayRanges(idea.premium_discount_zones ?? idea.chart_overlays?.premium_discount_zones ?? []).slice(-4);
  if (modalOverlayVisibility.ob) orderBlocks.forEach((z) => drawZone(z, { fill: "rgba(251,146,60,.15)", stroke: "rgba(251,146,60,.75)" }, "OB"));
  if (modalOverlayVisibility.fvg) fvgs.forEach((z) => drawZone(z, { fill: "rgba(167,139,250,.12)", stroke: "rgba(196,181,253,.85)" }, "FVG"));
  if (modalOverlayVisibility.structure) entries.forEach((z) => drawZone(z, { fill: "rgba(45,212,191,.08)", stroke: "rgba(45,212,191,.75)" }, "ENTRY"));
  if (modalOverlayVisibility.structure) premiumDiscount.forEach((z) => drawZone(z, { fill: "rgba(56,189,248,.06)", stroke: "rgba(56,189,248,.58)" }, "P/D"));
  const drawLevel = (range, color, label) => {
    const y = yAt(range.low);
    if (y == null) return;
    ctx.strokeStyle = color;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(8, y);
    ctx.lineTo(width - 8, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color;
    ctx.font = "11px Inter, sans-serif";
    ctx.fillText(label, width - 72, y - 3);
  };
  if (modalOverlayVisibility.liquidity) liquidity.slice(0, 5).forEach((r) => drawLevel(r, "rgba(52,211,153,.95)", "LIQ"));
  if (modalOverlayVisibility.structure) structure.slice(0, 5).forEach((r) => drawLevel(r, "rgba(250,204,21,.95)", "BOS/MSS"));
  if (modalOverlayVisibility.signals) {
    const entry = Number(idea.entry ?? idea.entry_price);
    const y = yAt(entry);
    const x = xAt(maxIndex) ?? (width - 14);
    if (Number.isFinite(entry) && y != null) {
      const c = String(idea.direction || "").toLowerCase().includes("sell") ? "#fb7185" : "#34d399";
      ctx.fillStyle = c;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x - 10, y - 9);
      ctx.lineTo(x - 10, y + 9);
      ctx.closePath();
      ctx.fill();
    }
  }
  modalOverlayState = { redraw: () => renderInstitutionalOverlay({ idea, candles, series }) };
}

function filterIdeasByProp(ideas) {
  if (currentPropFilter === "all") return ideas;
  if (currentPropFilter === "ab") return ideas.filter((idea) => ["A", "B"].includes(String(getPropScore(idea).grade || "").toUpperCase()));
  if (currentPropFilter === "entry") return ideas.filter((idea) => String(getPropScore(idea).mode || "") === "prop_entry");
  if (currentPropFilter === "no_trade") return ideas.filter((idea) => String(getPropScore(idea).mode || "") === "no_trade");
  return ideas;
}

function renderPropFilters() {
  return `<div class="prop-filter-row">
    <button class="prop-filter-btn ${currentPropFilter === "all" ? "active" : ""}" data-prop-filter="all">Все идеи</button>
    <button class="prop-filter-btn ${currentPropFilter === "ab" ? "active" : ""}" data-prop-filter="ab">Только A/B</button>
    <button class="prop-filter-btn ${currentPropFilter === "entry" ? "active" : ""}" data-prop-filter="entry">PROP ENTRY</button>
    <button class="prop-filter-btn ${currentPropFilter === "no_trade" ? "active" : ""}" data-prop-filter="no_trade">NO TRADE</button>
  </div>`;
}

function renderIdeas(payload) {
  const rawIdeas = Array.isArray(payload?.ideas) ? payload.ideas : Array.isArray(payload?.signals) ? payload.signals : [];
  const ideas = filterIdeasByProp(rawIdeas);
  lastPayload = payload;
  if (ideasUpdatedAt) ideasUpdatedAt.innerHTML = `<div class="market-status-row"><span class="health-pill good">● Рынок: мониторинг</span><span class="health-pill good">API: ${escapeHtml(rawIdeas.length)} идей</span><span class="health-pill warn">Обновлено: ${escapeHtml(formatUpdatedAt(payload?.updated_at_utc))}</span><span class="health-pill">Источники: MT4 / CME / News / Proxy chart</span></div>`;
  if (!rawIdeas.length) {
    ideasContainer.innerHTML = `<div class="ideas-loading">Идеи пока недоступны.</div>`;
    return;
  }
  ideasContainer.innerHTML = renderPropFilters() + (ideas.length ? `<div class="ideas-grid">${ideas.map(renderIdeaCard).join("")}</div>` : `<div class="ideas-loading">Нет идей под выбранный фильтр.</div>`);
  ideasContainer.querySelectorAll("[data-prop-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      currentPropFilter = btn.getAttribute("data-prop-filter") || "all";
      renderIdeas(lastPayload);
    });
  });
  ideasContainer.querySelectorAll("[data-idea-index]").forEach((card) => {
    card.addEventListener("click", () => openIdeaModal(ideas[Number(card.getAttribute("data-idea-index"))]));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") openIdeaModal(ideas[Number(card.getAttribute("data-idea-index"))]);
    });
  });
}

async function loadIdeas() {
  if (isIdeasLoading) return;
  isIdeasLoading = true;
  try {
    const payload = await getJson("/ideas/market");
    const ideas = Array.isArray(payload?.ideas) ? payload.ideas : Array.isArray(payload?.signals) ? payload.signals : [];
    const voiceMessages = collectVoiceNotifications(ideas);
    renderIdeas(payload);
    if (hasLoadedIdeasOnce && isVoiceEnabled()) voiceMessages.forEach(enqueueVoiceMessage);
    hasLoadedIdeasOnce = true;
  } catch (error) {
    console.error("ideas_load_failed", error);
    ideasContainer.innerHTML = `<div class="ideas-loading">Не удалось загрузить идеи.</div>`;
    if (ideasUpdatedAt) ideasUpdatedAt.textContent = "Обновление: ошибка загрузки";
  } finally {
    isIdeasLoading = false;
  }
}

function startIdeasPage() {
  if (!ideasContainer) return;
  injectUiStyles();
  initVoiceToggle();
  createIdeasModal();
  loadIdeas();
  if (ideasPollTimer) clearInterval(ideasPollTimer);
  ideasPollTimer = setInterval(loadIdeas, 60000);
}

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeIdeaModal();
});

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startIdeasPage);
} else {
  startIdeasPage();
}
