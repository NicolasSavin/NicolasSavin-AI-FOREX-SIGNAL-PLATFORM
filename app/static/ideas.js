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
  const value = String(label || "").toUpperCase();
  if (value.includes("BUY") || value.includes("ПОКУП")) return "idea-label-buy";
  if (value.includes("SELL") || value.includes("ПРОДА")) return "idea-label-sell";
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
  return `${voiceSymbolLabel(symbol)} ${voiceActionLabel(signal)}`;
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

function sanitizeNarrative(value) {
  return String(value || "")
    .replace(/\(\s*none\s*\)/gi, "")
    .replace(/\bnone\b/gi, "")
    .trim();
}

function firstText(...values) {
  for (const value of values) {
    const text = sanitizeNarrative(value);
    if (text) return text;
  }
  return "";
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function formatNumber(value) {
  if (value === undefined || value === null || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return escapeHtml(value);
  return String(num);
}

function getIdeaSymbol(idea) {
  return String(idea.instrument || idea.symbol || "РЫНОК").toUpperCase();
}

function getIdeaDirection(idea) {
  const raw = String(idea.signal || idea.label || idea.direction || "").toUpperCase();
  if (raw.includes("BUY") || raw.includes("ПОКУП")) return "Покупка";
  if (raw.includes("SELL") || raw.includes("ПРОДА")) return "Продажа";
  return "Наблюдение";
}

function resolveVisibleNarrative(idea) {
  return firstText(
    idea?.unified_narrative,
    idea?.idea_thesis,
    idea?.full_text,
    idea?.article_ru,
    idea?.journalistic_summary_ru,
    idea?.confluence_summary_ru,
    idea?.reason_ru,
    idea?.description_ru,
    idea?.fallback_narrative,
  ) || "Сценарий в режиме fallback: модельный нарратив временно недоступен.";
}

function resolveNewsContext(idea) {
  return firstText(
    idea?.news_title,
    idea?.fundamental_context_ru,
    idea?.fundamental_ru,
    idea?.news_context_ru,
    idea?.why_moves_ru,
    idea?.market_impact_ru,
  ) || "нет данных";
}

function criterionValue(idea, keys) {
  for (const key of keys) {
    const value = key.split(".").reduce((obj, part) => obj?.[part], idea);
    const text = Array.isArray(value) ? value.filter(Boolean).join(", ") : sanitizeNarrative(value);
    if (text) return text;
  }
  return "нет данных";
}

function renderCriteriaBlock(idea) {
  const rows = [
    ["SMC", ["smart_money_ru", "smc_ru", "smc.summary", "smart_money.summary"]],
    ["ICT", ["ict_ru", "ict.summary", "ict"]],
    ["Ликвидность", ["liquidity_ru", "liquidity.summary", "liquidity"]],
    ["Order Blocks", ["order_blocks_ru", "order_blocks.summary", "orderBlocks", "order_blocks"]],
    ["Опционы", ["options_ru", "options_analysis.prop_bias", "options_analysis.bias", "options_analysis.summary"]],
    ["Объём", ["volume_ru", "volume.summary", "volume"]],
    ["CumDelta", ["cum_delta_ru", "cumDelta", "cum_delta", "delta_ru"]],
    ["Дивергенции", ["divergence_ru", "divergence.summary", "divergence"]],
    ["Новости/фундаментал", ["fundamental_context_ru", "fundamental_ru", "news_context_ru", "news_title", "why_moves_ru"]],
    ["Sentiment", ["sentiment.summary", "sentiment.bias", "sentiment_ru"]],
  ];

  return `
    <section class="idea-section idea-criteria">
      <h4>Критерии идеи</h4>
      <p class="idea-criteria-note">Новости и фундаментал показываются как контекст/подтверждение, а не как гарантированная причина входа.</p>
      <div class="idea-criteria-grid">
        ${rows.map(([label, keys]) => `
          <div class="idea-criteria-item">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(criterionValue(idea, keys))}</strong>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function renderChartBlock(idea, chartImageUrl) {
  if (chartImageUrl) {
    return `<div class="idea-chart-wrap">
      <img class="idea-chart-image" src="${escapeHtml(chartImageUrl)}?t=${Date.now()}" alt="${escapeHtml(idea.title || "chart")}" loading="lazy" />
    </div>`;
  }

  const fallbackCandles = idea?.chartData?.candles || idea?.chart_data?.candles || [];
  const fallbackSvg = buildFallbackSvg(fallbackCandles, idea);
  if (fallbackSvg) {
    return `<div class="idea-chart-wrap">${fallbackSvg}</div>`;
  }

  return `<div class="idea-chart-missing">Снапшот графика недоступен (${escapeHtml(idea.chartSnapshotStatus || idea.chart_snapshot_status || "no_data")}).</div>`;
}

function buildFallbackSvg(candles, idea = {}) {
  if (!Array.isArray(candles) || candles.length < 2) return "";
  const closes = candles.map((c) => Number(c?.close)).filter((v) => Number.isFinite(v));
  if (closes.length < 2) return "";

  const levels = [
    { label: "Entry", value: idea.entry },
    { label: "TP", value: idea.tp ?? idea.target },
    { label: "SL", value: idea.sl ?? idea.stop_loss },
  ].filter((level) => Number.isFinite(Number(level.value)));

  const allPrices = [...closes, ...levels.map((level) => Number(level.value))];
  const min = Math.min(...allPrices);
  const max = Math.max(...allPrices);
  const width = 900;
  const height = 260;
  const padX = 32;
  const padY = 24;
  const step = (width - padX * 2) / Math.max(closes.length - 1, 1);
  const yOf = (value) => {
    const ratio = max === min ? 0.5 : (value - min) / (max - min);
    return height - padY - ratio * (height - padY * 2);
  };
  const points = closes
    .map((value, index) => `${(padX + index * step).toFixed(2)},${yOf(value).toFixed(2)}`)
    .join(" ");
  const levelMarkup = levels.map((level) => {
    const y = yOf(Number(level.value));
    return `<g>
      <line class="idea-chart-level" x1="${padX}" y1="${y.toFixed(2)}" x2="${width - padX}" y2="${y.toFixed(2)}"></line>
      <text class="idea-chart-level-label" x="${width - padX - 120}" y="${(y - 6).toFixed(2)}">${escapeHtml(level.label)} ${escapeHtml(formatNumber(level.value))}</text>
    </g>`;
  }).join("");

  return `<svg class="idea-chart-image idea-chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="fallback candles chart">
    <rect x="0" y="0" width="${width}" height="${height}" fill="#0b1220"></rect>
    ${levelMarkup}
    <polyline class="idea-chart-path" fill="none" stroke="#22d3ee" stroke-width="3" points="${points}"></polyline>
  </svg>`;
}

function renderIdeaCard(idea) {
  const chartImageUrl = normalizeChartImageUrl(idea.chartImageUrl || idea.chart_image || "");
  const tradePlan = idea.trade_plan || {};
  const updates = asArray(idea.updates).slice(-5).reverse();
  const reasoning = resolveVisibleNarrative(idea);
  const compactSummary = String(idea?.compact_summary || "").trim();
  const analysisMode = String(idea.analysis_mode || "").toLowerCase() === "professional" ? "профессиональный" : "упрощённый";
  const providerLabel = String(idea.data_provider || "").trim() || "нет данных";
  const warningText = String(idea.warning || "").trim();
  const sentiment = idea?.sentiment || {};
  const optionsAnalysis = idea?.options_analysis || {};
  const renderLevels = (arr) => Array.isArray(arr) && arr.length ? arr.join(", ") : "нет данных";
  const straddleText = Array.isArray(optionsAnalysis.straddle) && optionsAnalysis.straddle.length
    ? optionsAnalysis.straddle.map((x) => x.price).join(", ")
    : "нет данных";
  const strangleText = Array.isArray(optionsAnalysis.strangle) && optionsAnalysis.strangle.length
    ? optionsAnalysis.strangle.map((x) => `${x.lower ?? "?"}-${x.upper ?? "?"}`).join(", ")
    : "нет данных";
  const hasSentiment = Number.isFinite(Number(sentiment.long_pct)) && Number.isFinite(Number(sentiment.short_pct));
  const sentimentLabel = hasSentiment
    ? `Sentiment: Long ${Number(sentiment.long_pct)}% / Short ${Number(sentiment.short_pct)}%`
    : "Sentiment: нет данных";
  const symbol = getIdeaSymbol(idea);

  return `
    <article class="idea-card">
      <div class="idea-card-top">
        <div class="idea-card-meta">
          <div class="idea-instrument">${escapeHtml(symbol)}</div>
          <h3 class="idea-title">${escapeHtml(idea.title || "AI-идея")}</h3>
          <div class="idea-news-line">Новостной/фундаментальный контекст: ${escapeHtml(resolveNewsContext(idea))}</div>
          <div class="idea-news-line">Статус: <strong>${escapeHtml(idea.status || "ожидание")}</strong></div>
        </div>
        <div class="idea-label ${labelClass(idea.label || idea.signal || idea.direction)}">${escapeHtml(idea.label || getIdeaDirection(idea))}</div>
      </div>

      <div class="idea-key-levels">
        <div><span>Направление</span><strong>${escapeHtml(getIdeaDirection(idea))}</strong></div>
        <div><span>Entry</span><strong>${escapeHtml(formatNumber(idea.entry))}</strong></div>
        <div><span>SL</span><strong>${escapeHtml(formatNumber(idea.sl ?? idea.stop_loss))}</strong></div>
        <div><span>TP</span><strong>${escapeHtml(formatNumber(idea.tp ?? idea.target))}</strong></div>
        <div><span>R/R</span><strong>${escapeHtml(formatNumber(idea.rr ?? idea.risk_reward))}</strong></div>
      </div>

      <div class="idea-summary">${escapeHtml(reasoning)}</div>
      ${compactSummary ? `<div class="idea-news-line">МТФ: ${escapeHtml(compactSummary)}</div>` : ""}
      <div class="idea-news-line">Режим: <strong>${escapeHtml(analysisMode)}</strong></div>
      <div class="idea-news-line">Источник: <strong>${escapeHtml(providerLabel)}</strong></div>
      ${warningText ? `<div class="idea-warning">${escapeHtml(warningText)}</div>` : ""}
      <div class="idea-news-line">Источник описания: <strong>${escapeHtml(idea.narrative_source || "резервный_шаблон")}</strong></div>

      ${renderChartBlock(idea, chartImageUrl)}
      <div class="idea-sentiment-badge">${escapeHtml(sentimentLabel)}</div>

      ${renderCriteriaBlock(idea)}

      <section class="idea-section idea-section-plan">
        <h4>Единый нарратив</h4>
        <p>${escapeHtml(reasoning)}</p>
      </section>

      <section class="idea-section idea-section-plan">
        <h4>Торговый сценарий</h4>
        <ul class="trade-plan-list">
          <li><strong>Уклон:</strong> ${escapeHtml(tradePlan.уклон || tradePlan.bias || "нет данных")}</li>
          <li><strong>Зона работы:</strong> ${escapeHtml(tradePlan.entry_zone || "нет данных")}</li>
          <li><strong>Инвалидация:</strong> ${escapeHtml(tradePlan.invalidation || "нет данных")}</li>
          <li><strong>Цель 1:</strong> ${escapeHtml(tradePlan.target_1 || "нет данных")}</li>
          <li><strong>Цель 2:</strong> ${escapeHtml(tradePlan.target_2 || "нет данных")}</li>
          <li><strong>Альтернатива:</strong> ${escapeHtml(tradePlan.alternative_scenario_ru || "нет данных")}</li>
        </ul>
      </section>

      <section class="idea-section idea-section-plan">
        <h4>Prop-level options</h4>
        <ul class="trade-plan-list">
          <li><strong>Prop bias:</strong> ${escapeHtml(optionsAnalysis.prop_bias || optionsAnalysis.bias || "нет данных")}</li>
          <li><strong>Score:</strong> ${escapeHtml(optionsAnalysis.prop_score ?? "нет данных")}</li>
          <li><strong>Call walls:</strong> ${escapeHtml(renderLevels(optionsAnalysis.callWalls))}</li>
          <li><strong>Put walls:</strong> ${escapeHtml(renderLevels(optionsAnalysis.putWalls))}</li>
          <li><strong>Target zones:</strong> ${escapeHtml(renderLevels(optionsAnalysis.targetLevels))}</li>
          <li><strong>Hedge zones:</strong> ${escapeHtml(renderLevels(optionsAnalysis.hedgeLevels))}</li>
          <li><strong>Straddle:</strong> ${escapeHtml(straddleText)}</li>
          <li><strong>Strangle:</strong> ${escapeHtml(strangleText)}</li>
          <li><strong>Pinning risk:</strong> ${escapeHtml(optionsAnalysis.pinningRisk || "нет данных")}</li>
          <li><strong>Range risk:</strong> ${escapeHtml(optionsAnalysis.rangeRisk || "нет данных")}</li>
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

function renderIdeas(payload) {
  const ideas = Array.isArray(payload?.ideas) ? payload.ideas : [];

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
  if (!ideasContainer) return;
  initVoiceToggle();
  loadIdeas();
  setInterval(loadIdeas, 60000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startIdeasPage);
} else {
  startIdeasPage();
}
