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

function labelClass(label) {
  if (label === "BUY IDEA" || label === "ИДЕЯ ПОКУПКИ") return "idea-label-buy";
  if (label === "SELL IDEA" || label === "ИДЕЯ ПРОДАЖИ") return "idea-label-sell";
  return "idea-label-watch";
}

function normalizeChartImageUrl(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw) || raw.startsWith("/")) return raw;
  if (raw.startsWith("static/")) return `/${raw}`;
  if (raw.startsWith("./")) return `/${raw.slice(2)}`;
  return `/static/${raw.replace(/^\/+/, "")}`;
}

function createIdeaStableKey(idea) {
  const explicitId = idea?.id ?? idea?.idea_id ?? idea?.uid ?? idea?._id;
  if (explicitId !== undefined && explicitId !== null && String(explicitId).trim()) {
    return `id:${String(explicitId).trim()}`;
  }
  const symbol = String(idea?.instrument || idea?.symbol || "").trim();
  const signal = String(idea?.signal || idea?.label || "").trim();
  const entry = String(idea?.entry ?? "").trim();
  const sl = String(idea?.sl ?? idea?.stop_loss ?? "").trim();
  const tp = String(idea?.tp ?? idea?.target ?? "").trim();
  return `fp:${symbol}|${signal}|${entry}|${sl}|${tp}`;
}

function createIdeaComparableState(idea) {
  return {
    status: String(idea?.status ?? "").trim(),
    entry: String(idea?.entry ?? "").trim(),
    sl: String(idea?.sl ?? idea?.stop_loss ?? "").trim(),
    tp: String(idea?.tp ?? idea?.target ?? "").trim(),
    signal: String(idea?.signal ?? idea?.label ?? "").trim(),
  };
}

function voiceSymbolLabel(symbolRaw) {
  const symbol = String(symbolRaw || "").trim().toUpperCase();
  if (symbol === "EURUSD") return "евродоллар";
  if (symbol === "USDJPY") return "доллар йена";
  if (symbol === "GBPUSD") return "фунт доллар";
  if (symbol === "XAUUSD") return "золото";
  return "инструмент";
}

function voiceActionLabel(signalRaw) {
  const signal = String(signalRaw || "").trim().toUpperCase();
  if (signal === "BUY" || signal === "ИДЕЯ ПОКУПКИ") return "покупка";
  if (signal === "SELL" || signal === "ИДЕЯ ПРОДАЖИ") return "продажа";
  return "ожидание";
}

function formatVoiceMessage(type, idea) {
  const symbol = String(idea?.instrument || idea?.symbol || "").trim();
  const signal = String(idea?.signal || idea?.label || "").trim();
  const symbolLabel = voiceSymbolLabel(symbol);
  const actionLabel = voiceActionLabel(signal);
  return `${symbolLabel} ${actionLabel}`;
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
  if (localStorage.getItem(VOICE_STORAGE_KEY) !== "1" && localStorage.getItem(VOICE_STORAGE_KEY) !== "0") {
    setVoiceEnabled(false);
  }

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

function enqueueVoiceMessage(message) {
  if (!message || !("speechSynthesis" in window)) return;

  const now = Date.now();
  for (const [text, ts] of recentVoiceMessages.entries()) {
    if (now - ts > VOICE_REPEAT_WINDOW_MS) recentVoiceMessages.delete(text);
  }
  if (recentVoiceMessages.has(message)) return;

  recentVoiceMessages.set(message, now);
  voicePendingQueue.push(message);
  if (voicePendingQueue.length > VOICE_MAX_QUEUE) {
    voicePendingQueue = voicePendingQueue.slice(-VOICE_MAX_QUEUE);
  }

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
    const key = createIdeaStableKey(idea);
    const state = createIdeaComparableState(idea);
    nextState.set(key, state);

    const prev = previousIdeasState.get(key);
    if (!prev) {
      notifications.push(formatVoiceMessage("new", idea));
      return;
    }

    if (
      prev.status !== state.status ||
      prev.entry !== state.entry ||
      prev.sl !== state.sl ||
      prev.tp !== state.tp ||
      prev.signal !== state.signal
    ) {
      notifications.push(formatVoiceMessage("updated", idea));
    }
  });

  previousIdeasState = nextState;
  return notifications;
}

function renderIdeaCard(idea) {
  const chartImageUrl = normalizeChartImageUrl(idea.chartImageUrl || idea.chart_image || "");
  console.log("chart_image:", chartImageUrl || null);
  console.log("snapshot_status:", idea.chartSnapshotStatus || idea.chart_snapshot_status || "");
  const tradePlan = idea.trade_plan || {};
  const updates = Array.isArray(idea.updates) ? idea.updates.slice(-5).reverse() : [];
  const reasoning = resolveVisibleNarrative(idea);
  const compactSummary = String(idea?.compact_summary || "").trim();
  const analysisMode = String(idea.analysis_mode || "").toLowerCase() === "professional" ? "профессиональный" : "упрощённый";
  const providerLabel = String(idea.data_provider || "").toLowerCase() === "twelvedata" ? "TwelveData" : "Yahoo fallback";
  const warningText = String(idea.warning || "").trim();
  const sentiment = idea?.sentiment || {};
  const hasSentiment = Number.isFinite(Number(sentiment.long_pct)) && Number.isFinite(Number(sentiment.short_pct));
  const sentimentLabel = hasSentiment
    ? `Sentiment: Long ${Number(sentiment.long_pct)}% / Short ${Number(sentiment.short_pct)}%`
    : "Sentiment: нет данных";

  return `
    <article class="idea-card">
      <div class="idea-card-top">
        <div class="idea-card-meta">
          <div class="idea-instrument">${escapeHtml(idea.instrument || "РЫНОК")}</div>
          <h3 class="idea-title">${escapeHtml(idea.title || "AI-идея")}</h3>
          <div class="idea-news-line">Основание: ${escapeHtml(idea.news_title || "Рыночная новость")}</div>
          <div class="idea-news-line">Статус: <strong>${escapeHtml(idea.status || "ожидание")}</strong></div>
        </div>
        <div class="idea-label ${labelClass(idea.label)}">${escapeHtml(idea.label || "НАБЛЮДЕНИЕ")}</div>
      </div>

      <div class="idea-summary">${escapeHtml(reasoning)}</div>
      ${compactSummary ? `<div class="idea-news-line">МТФ: ${escapeHtml(compactSummary)}</div>` : ""}
      <div class="idea-news-line">Режим: <strong>${escapeHtml(analysisMode)}</strong></div>
      <div class="idea-news-line">Источник: <strong>${escapeHtml(providerLabel)}</strong></div>
      ${warningText ? `<div class="idea-warning">${escapeHtml(warningText)}</div>` : ""}
      <div class="idea-news-line">Источник описания: <strong>${escapeHtml(idea.narrative_source || "резервный_шаблон")}</strong></div>

      ${renderChartBlock(idea, chartImageUrl)}
      <div class="idea-sentiment-badge">${escapeHtml(sentimentLabel)}</div>

      <section class="idea-section idea-section-plan">
        <h4>Единый нарратив</h4>
        <p>${escapeHtml(reasoning)}</p>
      </section>

      <section class="idea-section idea-section-plan">
        <h4>Торговый сценарий</h4>
        <ul class="trade-plan-list">
          <li><strong>Уклон:</strong> ${escapeHtml(tradePlan.уклон || tradePlan.bias || "нейтральный")}</li>
          <li><strong>Зона работы:</strong> ${escapeHtml(tradePlan.entry_zone || "")}</li>
          <li><strong>Инвалидация:</strong> ${escapeHtml(tradePlan.invalidation || "")}</li>
          <li><strong>Цель 1:</strong> ${escapeHtml(tradePlan.target_1 || "")}</li>
          <li><strong>Цель 2:</strong> ${escapeHtml(tradePlan.target_2 || "")}</li>
          <li><strong>Альтернатива:</strong> ${escapeHtml(tradePlan.alternative_scenario_ru || "")}</li>
        </ul>
      </section>

      <section class="idea-section idea-section-plan">
        <h4>Лента обновлений</h4>
        ${
          updates.length
            ? `<ul class="trade-plan-list">${updates
                .map(
                  (item) =>
                    `<li><strong>${escapeHtml(item.event_type || "updated")}:</strong> ${escapeHtml(item.explanation || "")} <em>(${escapeHtml(
                      formatUpdatedAt(item.timestamp),
                    )})</em></li>`,
                )
                .join("")}</ul>`
            : "<p>Пока нет событий в lifecycle.</p>"
        }
      </section>
    </article>
  `;
}

function resolveVisibleNarrative(idea) {
  const sanitize = (value) => String(value || "").replace(/\(\s*none\s*\)/gi, "").replace(/\bnone\b/gi, "").trim();
  const unified = sanitize(idea?.unified_narrative);
  if (unified) return unified;
  const fullText = sanitize(idea?.full_text);
  if (fullText) return fullText;
  const confluence = sanitize(idea?.confluence_summary_ru);
  if (confluence) return confluence;
  const reason = sanitize(idea?.reason_ru);
  if (reason) return reason;
  const description = sanitize(idea?.description_ru);
  if (description) return description;
  const shortScenario = sanitize(idea?.short_scenario_ru);
  if (shortScenario) return shortScenario;
  const rationale = sanitize(idea?.rationale);
  if (rationale) return rationale;
  const currentReasoning = sanitize(idea?.current_reasoning);
  if (currentReasoning) return currentReasoning;
  return "Идея основана на структуре рынка, ликвидности и текущем импульсе. Сценарий ожидает подтверждения или обновления рыночных данных.";
}

function renderChartBlock(idea, chartImageUrl) {
  if (chartImageUrl) {
    return `<div class="idea-chart-wrap">
      <img class="idea-chart-image" src="${escapeHtml(chartImageUrl)}?t=${Date.now()}" alt="${escapeHtml(idea.title || "chart")}" />
    </div>`;
  }
  const fallbackCandles = idea?.chartData?.candles || idea?.chart_data?.candles || [];
  const fallbackSvg = buildFallbackSvg(fallbackCandles);
  if (fallbackSvg) {
    return `<div class="idea-chart-wrap">${fallbackSvg}</div>`;
  }
  return `<div class="idea-chart-missing">Снапшот графика недоступен (${escapeHtml(idea.chartSnapshotStatus || idea.chart_snapshot_status || "no_data")}).</div>`;
}

function buildFallbackSvg(candles) {
  if (!Array.isArray(candles) || candles.length < 2) return "";
  const closes = candles.map((c) => Number(c?.close)).filter((v) => Number.isFinite(v));
  if (closes.length < 2) return "";
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const width = 400;
  const height = 180;
  const step = width / Math.max(closes.length - 1, 1);
  const points = closes
    .map((value, index) => {
      const x = index * step;
      const ratio = max === min ? 0.5 : (value - min) / (max - min);
      const y = height - ratio * (height - 20) - 10;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  return `<svg class="idea-chart-image" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="fallback candles chart">
    <rect x="0" y="0" width="${width}" height="${height}" fill="#0b1220"></rect>
    <polyline fill="none" stroke="#22d3ee" stroke-width="2" points="${points}"></polyline>
  </svg>`;
}

function renderIdeas(payload) {
  const ideas = payload?.ideas || [];

  if (ideasUpdatedAt) {
    ideasUpdatedAt.textContent = `Обновление: ${formatUpdatedAt(payload?.updated_at_utc)}`;
  }

  if (!ideas.length) {
    ideasContainer.innerHTML = `<div class="ideas-loading">Идеи пока недоступны.</div>`;
    return;
  }

  ideasContainer.innerHTML = ideas.map(renderIdeaCard).join("");
}

async function loadIdeas() {
  try {
    const payload = await getJson("/ideas/market");
    const ideas = Array.isArray(payload?.ideas) ? payload.ideas : [];
    const voiceMessages = collectVoiceNotifications(ideas);

    renderIdeas(payload);

    if (hasLoadedIdeasOnce && isVoiceEnabled()) {
      voiceMessages.forEach(enqueueVoiceMessage);
    }
    hasLoadedIdeasOnce = true;
  } catch {
    ideasContainer.innerHTML = `<div class="ideas-loading">Не удалось загрузить идеи.</div>`;
    if (ideasUpdatedAt) ideasUpdatedAt.textContent = "Обновление: ошибка загрузки";
  }
}

function startIdeasPage() {
  initVoiceToggle();
  loadIdeas();
  setInterval(loadIdeas, 60000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startIdeasPage);
} else {
  startIdeasPage();
}
