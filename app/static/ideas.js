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
let currentPropFilter = "all";
let modalChart = null;

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

function formatNumber(value) {
  if (value === undefined || value === null || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return escapeHtml(value);
  return String(num);
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
    idea.news_title,
    idea.fundamental_context_ru,
    idea.fundamental_ru,
    idea.news_context_ru,
    idea.why_moves_ru,
    idea.market_impact_ru,
  ) || "нет данных";
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
    .page-shell { max-width:1500px; margin:0 auto; padding:26px 24px 56px; }
    .site-header { margin-bottom:20px; }
    .site-header h1 { margin:6px 0; font-size:clamp(30px,3vw,46px); }
    .lead { color:#b9d6f8; }
    .idea-instrument, .nav-link { display:inline-flex; width:fit-content; padding:7px 12px; border-radius:999px; background:rgba(99,102,241,.18); color:#c7d2fe; border:1px solid rgba(99,102,241,.26); font-weight:800; text-decoration:none; font-size:12px; }
    .panel { background:rgba(8,25,48,.72); border:1px solid rgba(95,156,230,.2); border-radius:22px; padding:18px; }
    .prop-filter-row { display:flex; gap:10px; flex-wrap:wrap; margin:0 0 18px; }
    .prop-filter-btn { border:1px solid rgba(95,156,230,.46); background:rgba(3,14,28,.72); color:#dbeeff; border-radius:999px; padding:9px 13px; font-size:12px; font-weight:900; cursor:pointer; }
    .prop-filter-btn.active { background:rgba(69,202,255,.2); border-color:rgba(69,202,255,.72); }
    .ideas-container { display:grid; gap:16px; }
    .ideas-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; }
    .ideas-loading { padding:18px; border:1px solid rgba(95,156,230,.25); border-radius:16px; background:rgba(3,14,28,.66); color:#b9d6f8; }
    .idea-card { min-height:300px; padding:17px; border-radius:22px; border:1px solid rgba(95,156,230,.42); background:radial-gradient(circle at 85% 0%, rgba(69,202,255,.16), transparent 35%), linear-gradient(155deg, rgba(20,52,92,.96), rgba(7,22,42,.98) 70%); box-shadow:0 22px 54px rgba(0,0,0,.34), inset 0 1px 0 rgba(255,255,255,.1); cursor:pointer; transition:.18s ease; }
    .idea-card:hover { transform:translateY(-4px); border-color:rgba(124,184,255,.75); }
    .idea-card-top { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:12px; }
    .idea-title { margin:8px 0 6px; font-size:22px; line-height:1.15; }
    .idea-news-line { color:#b9d6f8; font-size:12px; line-height:1.5; }
    .idea-label,.badge { border-radius:999px; padding:8px 12px; font-size:12px; font-weight:950; white-space:nowrap; }
    .badge-buy,.idea-label-buy { color:#00150c; background:linear-gradient(180deg,#54ffb5,#31f59d); }
    .badge-sell,.idea-label-sell { color:#fff; background:linear-gradient(180deg,#d93f5b,#8f2034); }
    .badge-wait,.idea-label-watch { color:#efeaff; background:linear-gradient(180deg,#5266bd,#293c78); }
    .compact-levels { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin:12px 0; }
    .compact-levels div, .modal-meta div { padding:9px 10px; border:1px solid rgba(95,156,230,.28); border-radius:12px; background:rgba(3,14,28,.62); }
    .compact-levels span, .modal-meta span { display:block; color:#9bb8d8; font-size:10px; font-weight:950; text-transform:uppercase; margin-bottom:3px; }
    .compact-levels strong, .modal-meta strong { color:#f4f8ff; font-size:13px; }
    .compact-score { margin-top:12px; padding:12px; border-radius:16px; background:rgba(69,202,255,.08); border:1px solid rgba(69,202,255,.24); }
    .compact-score-head { display:flex; justify-content:space-between; align-items:center; gap:10px; }
    .compact-score strong { font-size:16px; }
    .prop-grade-badge { display:inline-flex; align-items:center; justify-content:center; min-width:46px; min-height:46px; border-radius:14px; padding:8px; font-size:22px; font-weight:950; }
    .prop-grade-a { color:#022616; background:linear-gradient(180deg,#8dffc9,#10b981); }
    .prop-grade-b { color:#251a00; background:linear-gradient(180deg,#fff08a,#facc15); }
    .prop-grade-c { color:#291100; background:linear-gradient(180deg,#ffc478,#fb923c); }
    .prop-grade-d { color:#fff2f5; background:linear-gradient(180deg,#ff7f99,#be123c); }
    .score-meter { height:10px; margin-top:10px; border-radius:999px; overflow:hidden; background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.1); }
    .score-fill { height:100%; border-radius:inherit; background:linear-gradient(90deg,#ef4444,#f59e0b,#22c55e); }
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
    @media(max-width:720px){ .ideas-grid{grid-template-columns:1fr;} .compact-levels,.criteria-grid,.modal-meta{grid-template-columns:1fr;} .chart-area,#ideaModalChart{height:390px;min-height:390px;} }
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

function renderIdeaCard(idea, index) {
  const symbol = getIdeaSymbol(idea);
  const action = idea.action || idea.signal || idea.label || "WAIT";
  return `<article class="idea-card" data-idea-index="${index}" tabindex="0" role="button" aria-label="Открыть идею ${escapeHtml(symbol)}">
    <div class="idea-card-top">
      <div>
        <div class="idea-instrument">${escapeHtml(symbol)}</div>
        <h3 class="idea-title">${escapeHtml(symbol)} · AI-идея</h3>
        <div class="idea-news-line">Новости/фундаментал: ${escapeHtml(resolveNewsContext(idea))}</div>
        <div class="idea-news-line">Статус: <strong>${escapeHtml(idea.trade_permission === false ? "ожидание" : idea.status || "активно")}</strong></div>
      </div>
      <div class="badge ${getActionBadgeClass(idea)}">${escapeHtml(action)}</div>
    </div>
    <div class="compact-levels">
      <div><span>Entry</span><strong>${escapeHtml(formatNumber(idea.entry ?? idea.entry_price))}</strong></div>
      <div><span>SL</span><strong>${escapeHtml(formatNumber(idea.sl ?? idea.stop_loss))}</strong></div>
      <div><span>TP</span><strong>${escapeHtml(formatNumber(idea.tp ?? idea.take_profit ?? idea.target))}</strong></div>
      <div><span>R/R</span><strong>${escapeHtml(formatNumber(idea.rr ?? idea.risk_reward))}</strong></div>
    </div>
    ${renderPropCompact(idea)}
    <div class="idea-summary-compact">${escapeHtml(resolveNarrative(idea))}</div>
  </article>`;
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
  title.textContent = `${symbol} · ${getIdeaDirection(idea)}`;
  body.innerHTML = `<div class="modal-grid">
      <div>
        ${renderPropDetails(idea)}
        <section class="modal-section" style="margin-top:16px;">
          <h4>Основная идея</h4>
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
      </div>
      <p class="modal-text"><strong>Новости/фундаментал:</strong> ${escapeHtml(resolveNewsContext(idea))}</p>
      <p class="modal-text"><strong>Источник:</strong> ${escapeHtml(idea.data_provider || idea.provider || "нет данных")}</p>
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
    try { modalChart.remove(); } catch {}
    modalChart = null;
  }
}

function renderModalChart(idea) {
  const container = document.getElementById("ideaModalChart");
  if (!container || !("LightweightCharts" in window)) return;
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
  const entry = Number(idea.entry ?? idea.entry_price);
  const sl = Number(idea.sl ?? idea.stop_loss);
  const tp = Number(idea.tp ?? idea.take_profit ?? idea.target);
  if (Number.isFinite(entry)) series.createPriceLine({ price: entry, color: "#ffd84d", lineWidth: 2, title: "ENTRY" });
  if (Number.isFinite(sl)) series.createPriceLine({ price: sl, color: "#ff5f7a", lineWidth: 2, title: "SL" });
  if (Number.isFinite(tp)) series.createPriceLine({ price: tp, color: "#31f59d", lineWidth: 2, title: "TP" });
  modalChart.timeScale().fitContent();
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
  if (ideasUpdatedAt) ideasUpdatedAt.textContent = `Обновление: ${formatUpdatedAt(payload?.updated_at_utc)}`;
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
  }
}

function startIdeasPage() {
  if (!ideasContainer) return;
  injectUiStyles();
  initVoiceToggle();
  createIdeasModal();
  loadIdeas();
  setInterval(loadIdeas, 60000);
}

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeIdeaModal();
});

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startIdeasPage);
} else {
  startIdeasPage();
}
