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

function injectPropUiStyles() {
  if (document.getElementById("prop-score-ui-styles")) return;
  const style = document.createElement("style");
  style.id = "prop-score-ui-styles";
  style.textContent = `
    .prop-score-panel {
      margin-top: 18px;
      padding: 18px;
      border-radius: 22px;
      border: 1px solid rgba(69,202,255,.28);
      background:
        radial-gradient(circle at 0% 0%, rgba(69,202,255,.18), transparent 35%),
        linear-gradient(145deg, rgba(6,24,46,.95), rgba(2,10,22,.98));
      box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 18px 40px rgba(0,0,0,.25);
    }
    .prop-score-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      align-items: center;
      margin-bottom: 14px;
    }
    .prop-score-kicker {
      color: rgba(185,214,248,.72);
      font-size: 11px;
      font-weight: 950;
      text-transform: uppercase;
      letter-spacing: .1em;
    }
    .prop-score-title {
      margin-top: 4px;
      font-size: 18px;
      font-weight: 950;
      color: #f4f8ff;
    }
    .prop-grade-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 58px;
      min-height: 58px;
      padding: 10px;
      border-radius: 18px;
      font-size: 26px;
      font-weight: 950;
      border: 1px solid rgba(255,255,255,.16);
    }
    .prop-grade-a { color: #022616; background: linear-gradient(180deg, #8dffc9, #10b981); }
    .prop-grade-b { color: #251a00; background: linear-gradient(180deg, #fff08a, #facc15); }
    .prop-grade-c { color: #291100; background: linear-gradient(180deg, #ffc478, #fb923c); }
    .prop-grade-d, .prop-grade-unknown { color: #fff2f5; background: linear-gradient(180deg, #ff7f99, #be123c); }
    .prop-score-meter {
      position: relative;
      height: 13px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(255,255,255,.08);
      border: 1px solid rgba(255,255,255,.1);
      margin: 10px 0 12px;
    }
    .prop-score-fill {
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #ef4444, #f59e0b, #22c55e);
      box-shadow: 0 0 18px rgba(34,197,94,.28);
    }
    .prop-score-meta {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 9px;
      margin-top: 12px;
    }
    .prop-score-meta div,
    .prop-blocker,
    .prop-criterion-mini {
      padding: 10px 12px;
      border-radius: 14px;
      background: rgba(3,14,28,.7);
      border: 1px solid rgba(95,156,230,.22);
      min-width: 0;
    }
    .prop-score-meta span,
    .prop-criteria-title,
    .prop-blockers-title {
      display: block;
      margin-bottom: 4px;
      color: rgba(185,214,248,.72);
      font-size: 10px;
      font-weight: 950;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .prop-score-meta strong {
      display: block;
      color: #f4f8ff;
      font-size: 14px;
      overflow-wrap: anywhere;
    }
    .prop-decision {
      margin-top: 12px;
      padding: 12px 14px;
      border-radius: 16px;
      background: rgba(69,202,255,.08);
      border: 1px solid rgba(69,202,255,.24);
      color: rgba(244,248,255,.94);
      font-size: 14px;
      line-height: 1.6;
      overflow-wrap: anywhere;
    }
    .prop-blockers-list,
    .prop-criteria-mini-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 12px;
    }
    .prop-blocker { color: #fecdd3; border-color: rgba(248,113,113,.25); background: rgba(127,29,29,.22); }
    .prop-criterion-mini strong { color: #f4f8ff; font-size: 13px; }
    .prop-criterion-confirmed { border-color: rgba(34,197,94,.36); }
    .prop-criterion-partial { border-color: rgba(250,204,21,.32); }
    .prop-criterion-missing { border-color: rgba(248,113,113,.28); opacity: .82; }
    .prop-filter-row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin: 0 0 18px;
    }
    .prop-filter-btn {
      border: 1px solid rgba(95,156,230,.36);
      background: rgba(3,14,28,.72);
      color: #dbeeff;
      border-radius: 999px;
      padding: 9px 13px;
      font-size: 12px;
      font-weight: 900;
      cursor: pointer;
    }
    .prop-filter-btn.active { background: rgba(69,202,255,.2); border-color: rgba(69,202,255,.62); }
    @media (max-width: 860px) {
      .prop-score-head, .prop-score-meta, .prop-blockers-list, .prop-criteria-mini-grid { grid-template-columns: 1fr; }
      .prop-grade-badge { width: fit-content; }
    }
  `;
  document.head.appendChild(style);
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
    const key = createIdeaStableKey(idea);
    const state = createIdeaComparableState(idea);
    nextState.set(key, state);
    const prev = previousIdeasState.get(key);
    if (!prev) {
      notifications.push(formatVoiceMessage("new", idea));
      return;
    }
    if (prev.status !== state.status || prev.entry !== state.entry || prev.sl !== state.sl || prev.tp !== state.tp || prev.signal !== state.signal) {
      notifications.push(formatVoiceMessage("updated", idea));
    }
  });
  previousIdeasState = nextState;
  return notifications;
}

function sanitizeNarrative(value) {
  return String(value || "").replace(/\(\s*none\s*\)/gi, "").replace(/\bnone\b/gi, "").trim();
}

function firstText(...values) {
  for (const value of values) {
    const text = sanitizeNarrative(value);
    if (text) return text;
  }
  return "";
}

function asArray(value) { return Array.isArray(value) ? value : []; }

function formatNumber(value) {
  if (value === undefined || value === null || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return escapeHtml(value);
  return String(num);
}

function getIdeaSymbol(idea) { return String(idea.instrument || idea.symbol || idea.pair || "РЫНОК").toUpperCase(); }

function getIdeaDirection(idea) {
  const raw = String(idea.signal || idea.label || idea.direction || idea.action || "").toUpperCase();
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
    idea?.decision_reason_ru,
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
  return null;
}

function propGradeClass(grade) {
  const normalized = String(grade || "unknown").trim().toLowerCase();
  return `prop-grade-${["a", "b", "c", "d"].includes(normalized) ? normalized : "unknown"}`;
}

function propModeLabel(mode) {
  const value = String(mode || "").trim();
  const labels = {
    prop_entry: "PROP ENTRY",
    watchlist: "WATCHLIST",
    research_only: "RESEARCH ONLY",
    no_trade: "NO TRADE",
  };
  return labels[value] || value || "нет данных";
}

function renderPropScoreBlock(idea) {
  const prop = getPropScore(idea);
  if (!prop) {
    return `<section class="prop-score-panel"><div class="prop-score-title">Prop Score: нет данных</div></section>`;
  }
  const score = Math.max(0, Math.min(100, Number(prop.score) || 0));
  const grade = String(prop.grade || "D").toUpperCase();
  const blockers = asArray(prop.blockers).filter(Boolean);
  const criteria = asArray(prop.criteria).slice(0, 8);
  return `
    <section class="prop-score-panel">
      <div class="prop-score-head">
        <div>
          <div class="prop-score-kicker">Prop Decision Engine</div>
          <div class="prop-score-title">Score ${escapeHtml(score)} / 100 · ${escapeHtml(propModeLabel(prop.mode))}</div>
        </div>
        <div class="prop-grade-badge ${propGradeClass(grade)}">${escapeHtml(grade)}</div>
      </div>
      <div class="prop-score-meter" aria-label="Prop score ${escapeHtml(score)} из 100">
        <div class="prop-score-fill" style="width:${score}%"></div>
      </div>
      <div class="prop-score-meta">
        <div><span>Решение</span><strong>${escapeHtml(propModeLabel(prop.mode))}</strong></div>
        <div><span>Направление</span><strong>${escapeHtml(prop.direction || idea.action || idea.signal || "WAIT")}</strong></div>
        <div><span>Grade</span><strong>${escapeHtml(grade)}</strong></div>
        <div><span>Score</span><strong>${escapeHtml(score)}</strong></div>
      </div>
      <div class="prop-decision">${escapeHtml(prop.decision_ru || idea.prop_decision_ru || "Решение недоступно.")}</div>
      ${blockers.length ? `
        <div class="prop-blockers-title" style="margin-top:14px;">Blockers</div>
        <div class="prop-blockers-list">
          ${blockers.map((item) => `<div class="prop-blocker">❌ ${escapeHtml(item)}</div>`).join("")}
        </div>` : ""}
      ${criteria.length ? `
        <div class="prop-criteria-title" style="margin-top:14px;">Критерии score</div>
        <div class="prop-criteria-mini-grid">
          ${criteria.map((item) => `
            <div class="prop-criterion-mini prop-criterion-${escapeHtml(item.status || "missing")}">
              <span>${escapeHtml(item.label_ru || item.key)}</span>
              <strong>${escapeHtml(item.score ?? 0)} / ${escapeHtml(item.weight ?? "—")} · ${escapeHtml(item.status || "—")}</strong>
            </div>`).join("")}
        </div>` : ""}
    </section>
  `;
}

function renderCriteriaBlock(idea) {
  const rows = [
    ["SMC", ["smart_money_ru", "smc_ru", "smc.summary", "smart_money.summary"]],
    ["ICT", ["ict_ru", "ict.summary", "ict"]],
    ["Ликвидность", ["liquidity_ru", "liquidity.summary", "liquidity"]],
    ["Order Blocks", ["order_blocks_ru", "order_blocks.summary", "orderBlocks", "order_blocks"]],
    ["Опционы", ["options_ru", "options_summary_ru", "options_analysis.summary_ru", "options_analysis.prop_bias", "options_analysis.bias", "options_analysis.summary"]],
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
    </section>`;
}

function renderChartBlock(idea, chartImageUrl) {
  if (chartImageUrl) {
    return `<div class="idea-chart-wrap"><img class="idea-chart-image" src="${escapeHtml(chartImageUrl)}?t=${Date.now()}" alt="${escapeHtml(idea.title || "chart")}" loading="lazy" /></div>`;
  }
  const fallbackCandles = idea?.chartData?.candles || idea?.chart_data?.candles || [];
  const fallbackSvg = buildFallbackSvg(fallbackCandles, idea);
  if (fallbackSvg) return `<div class="idea-chart-wrap">${fallbackSvg}</div>`;
  return `<div class="idea-chart-missing">Снапшот графика недоступен (${escapeHtml(idea.chartSnapshotStatus || idea.chart_snapshot_status || "no_data")}).</div>`;
}

function buildFallbackSvg(candles, idea = {}) {
  if (!Array.isArray(candles) || candles.length < 2) return "";
  const closes = candles.map((c) => Number(c?.close)).filter((v) => Number.isFinite(v));
  if (closes.length < 2) return "";
  const levels = [
    { label: "Entry", value: idea.entry },
    { label: "TP", value: idea.tp ?? idea.target ?? idea.take_profit },
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
  const points = closes.map((value, index) => `${(padX + index * step).toFixed(2)},${yOf(value).toFixed(2)}`).join(" ");
  const levelMarkup = levels.map((level) => {
    const y = yOf(Number(level.value));
    return `<g><line class="idea-chart-level" x1="${padX}" y1="${y.toFixed(2)}" x2="${width - padX}" y2="${y.toFixed(2)}"></line><text class="idea-chart-level-label" x="${width - padX - 120}" y="${(y - 6).toFixed(2)}">${escapeHtml(level.label)} ${escapeHtml(formatNumber(level.value))}</text></g>`;
  }).join("");
  return `<svg class="idea-chart-image idea-chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="fallback candles chart"><rect x="0" y="0" width="${width}" height="${height}" fill="#0b1220"></rect>${levelMarkup}<polyline class="idea-chart-path" fill="none" stroke="#22d3ee" stroke-width="3" points="${points}"></polyline></svg>`;
}

function renderIdeaCard(idea) {
  const chartImageUrl = normalizeChartImageUrl(idea.chartImageUrl || idea.chart_image || "");
  const tradePlan = idea.trade_plan || {};
  const updates = asArray(idea.updates).slice(-5).reverse();
  const reasoning = resolveVisibleNarrative(idea);
  const compactSummary = String(idea?.compact_summary || "").trim();
  const analysisMode = String(idea.analysis_mode || "").toLowerCase() === "professional" ? "профессиональный" : "упрощённый";
  const providerLabel = String(idea.data_provider || idea.provider || "").trim() || "нет данных";
  const warningText = String(idea.warning || "").trim();
  const sentiment = idea?.sentiment || {};
  const optionsAnalysis = idea?.options_analysis || {};
  const renderLevels = (arr) => Array.isArray(arr) && arr.length ? arr.join(", ") : "нет данных";
  const hasSentiment = Number.isFinite(Number(sentiment.long_pct)) && Number.isFinite(Number(sentiment.short_pct));
  const sentimentLabel = hasSentiment ? `Sentiment: Long ${Number(sentiment.long_pct)}% / Short ${Number(sentiment.short_pct)}%` : "Sentiment: нет данных";
  const symbol = getIdeaSymbol(idea);
  const actionLabel = idea.label || idea.signal || idea.action || getIdeaDirection(idea);

  return `
    <article class="idea-card">
      <div class="idea-card-top">
        <div class="idea-card-meta">
          <div class="idea-instrument">${escapeHtml(symbol)}</div>
          <h3 class="idea-title">${escapeHtml(idea.title || `${symbol} · AI-идея`)}</h3>
          <div class="idea-news-line">Новостной/фундаментальный контекст: ${escapeHtml(resolveNewsContext(idea))}</div>
          <div class="idea-news-line">Статус: <strong>${escapeHtml(idea.status || idea.trade_permission === false ? "ожидание" : "активно")}</strong></div>
        </div>
        <div class="idea-label ${labelClass(actionLabel)}">${escapeHtml(actionLabel)}</div>
      </div>

      <div class="idea-key-levels">
        <div><span>Направление</span><strong>${escapeHtml(getIdeaDirection(idea))}</strong></div>
        <div><span>Entry</span><strong>${escapeHtml(formatNumber(idea.entry ?? idea.entry_price))}</strong></div>
        <div><span>SL</span><strong>${escapeHtml(formatNumber(idea.sl ?? idea.stop_loss))}</strong></div>
        <div><span>TP</span><strong>${escapeHtml(formatNumber(idea.tp ?? idea.target ?? idea.take_profit))}</strong></div>
        <div><span>R/R</span><strong>${escapeHtml(formatNumber(idea.rr ?? idea.risk_reward))}</strong></div>
      </div>

      ${renderPropScoreBlock(idea)}

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
        <h4>Торговый сценарий</h4>
        <ul class="trade-plan-list">
          <li><strong>Уклон:</strong> ${escapeHtml(tradePlan.уклон || tradePlan.bias || idea.action || "нет данных")}</li>
          <li><strong>Зона работы:</strong> ${escapeHtml(tradePlan.entry_zone || idea.entry_source || "нет данных")}</li>
          <li><strong>Инвалидация:</strong> ${escapeHtml(tradePlan.invalidation || idea.stop_loss || "нет данных")}</li>
          <li><strong>Цель 1:</strong> ${escapeHtml(tradePlan.target_1 || idea.take_profit || idea.tp || "нет данных")}</li>
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
          <li><strong>Pinning risk:</strong> ${escapeHtml(optionsAnalysis.pinningRisk || "нет данных")}</li>
          <li><strong>Range risk:</strong> ${escapeHtml(optionsAnalysis.rangeRisk || "нет данных")}</li>
        </ul>
      </section>

      <section class="idea-section idea-section-plan">
        <h4>Лента обновлений</h4>
        ${updates.length ? `<ul class="trade-plan-list">${updates.map((item) => `<li><strong>${escapeHtml(item.event_type || "updated")}:</strong> ${escapeHtml(item.explanation || "")} <em>(${escapeHtml(formatUpdatedAt(item.timestamp))})</em></li>`).join("")}</ul>` : "<p>Пока нет событий в lifecycle.</p>"}
      </section>
    </article>`;
}

let currentPropFilter = "all";

function filterIdeasByProp(ideas) {
  if (currentPropFilter === "all") return ideas;
  if (currentPropFilter === "ab") return ideas.filter((idea) => ["A", "B"].includes(String(getPropScore(idea)?.grade || "").toUpperCase()));
  if (currentPropFilter === "entry") return ideas.filter((idea) => String(getPropScore(idea)?.mode || "") === "prop_entry");
  if (currentPropFilter === "no_trade") return ideas.filter((idea) => String(getPropScore(idea)?.mode || "") === "no_trade");
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
  if (ideasUpdatedAt) ideasUpdatedAt.textContent = `Обновление: ${formatUpdatedAt(payload?.updated_at_utc)}`;
  if (!rawIdeas.length) {
    ideasContainer.innerHTML = `<div class="ideas-loading">Идеи пока недоступны.</div>`;
    return;
  }
  ideasContainer.innerHTML = renderPropFilters() + (ideas.length ? ideas.map(renderIdeaCard).join("") : `<div class="ideas-loading">Нет идей под выбранный фильтр.</div>`);
  ideasContainer.querySelectorAll("[data-prop-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      currentPropFilter = btn.getAttribute("data-prop-filter") || "all";
      renderIdeas(payload);
    });
  });
}

async function loadIdeas() {
  try {
    const payload = await getJson("/ideas/market");
    const ideas = Array.isArray(payload?.ideas) ? payload.ideas : Array.isArray(payload?.signals) ? payload.signals : [];
    const voiceMessages = collectVoiceNotifications(ideas);
    renderIdeas(payload);
    if (hasLoadedIdeasOnce && isVoiceEnabled()) voiceMessages.forEach(enqueueVoiceMessage);
    hasLoadedIdeasOnce = true;
  } catch {
    ideasContainer.innerHTML = `<div class="ideas-loading">Не удалось загрузить идеи.</div>`;
    if (ideasUpdatedAt) ideasUpdatedAt.textContent = "Обновление: ошибка загрузки";
  }
}

function startIdeasPage() {
  if (!ideasContainer) return;
  injectPropUiStyles();
  initVoiceToggle();
  loadIdeas();
  setInterval(loadIdeas, 60000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startIdeasPage);
} else {
  startIdeasPage();
}
