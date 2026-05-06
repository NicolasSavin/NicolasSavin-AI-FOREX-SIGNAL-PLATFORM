(function () {
  let currentMode = "active";
  let currentTf = "AUTO";
  let lastPayload = null;

  function safeArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function number(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatPrice(value) {
    const n = number(value);
    if (n === null) return "—";
    return String(n);
  }

  function getStatus(idea) {
    return String((idea && idea.status) || "").toLowerCase();
  }

  function archiveReason(idea) {
    const status = getStatus(idea);
    if (status === "tp_hit") return "TP достигнут";
    if (status === "sl_hit") return "SL достигнут";
    if (["cancelled", "canceled", "invalidated", "expired"].includes(status)) return "Идея отменена";
    if (status === "archived") return "Архив";
    return "Закрытая идея";
  }

  function archiveExplanation(idea) {
    const status = getStatus(idea);
    const symbol = escapeHtml(String((idea && (idea.symbol || idea.pair || idea.instrument)) || "Инструмент").toUpperCase());
    const entry = formatPrice(idea && (idea.entry || idea.entry_price));
    const sl = formatPrice(idea && (idea.sl || idea.stop_loss));
    const tp = formatPrice(idea && (idea.tp || idea.take_profit || idea.target));
    const base = String(
      (idea && (idea.close_reason_ru || idea.archive_reason_ru || idea.result_reason_ru || idea.invalidation || idea.unified_narrative || idea.summary_ru)) || ""
    ).trim();
    if (base) return escapeHtml(base);
    if (status === "tp_hit") return `${symbol}: цель TP ${tp} достигнута. Идея закрыта в плюс; исходные уровни: entry ${entry}, SL ${sl}, TP ${tp}.`;
    if (status === "sl_hit") return `${symbol}: защитный SL ${sl} достигнут. Идея закрыта по риску; исходные уровни: entry ${entry}, SL ${sl}, TP ${tp}.`;
    if (["cancelled", "canceled", "invalidated", "expired"].includes(status)) return `${symbol}: идея отменена/инвалидирована. Нового входа по этому сетапу нет; старые уровни остаются только для журнала.`;
    return `${symbol}: идея перенесена в архив. Причина закрытия не передана backend, уровни сохранены для журнала.`;
  }

  function ideaResult(idea) {
    const status = getStatus(idea);
    if (status === "tp_hit") return "win";
    if (status === "sl_hit") return "loss";
    if (["cancelled", "canceled", "invalidated", "expired"].includes(status)) return "cancel";
    return "other";
  }

  function computeStats(items) {
    const stats = { total: items.length, win: 0, loss: 0, cancel: 0, other: 0 };
    items.forEach((idea) => { stats[ideaResult(idea)]++; });
    stats.winrate = stats.win + stats.loss > 0 ? Math.round((stats.win / (stats.win + stats.loss)) * 100) : 0;
    return stats;
  }

  function ensureStyles() {
    if (document.getElementById("ideas-archive-fix-style")) return;
    const style = document.createElement("style");
    style.id = "ideas-archive-fix-style";
    style.textContent = `
      .ideas-journal-bar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:14px 0 18px; }
      .ideas-journal-btn { border:1px solid rgba(95,156,230,.48); background:rgba(3,14,28,.78); color:#dbeeff; border-radius:999px; padding:9px 13px; font-size:12px; font-weight:900; cursor:pointer; }
      .ideas-journal-btn.active { background:rgba(56,189,248,.20); border-color:rgba(56,189,248,.80); color:#fff; }
      .ideas-journal-note { color:#9bb8d8; font-size:12px; }
      .ideas-stats-panel { display:grid; grid-template-columns:repeat(auto-fit,minmax(135px,1fr)); gap:10px; width:100%; margin:4px 0 8px; }
      .ideas-stat-box { border:1px solid rgba(95,156,230,.30); background:rgba(8,25,48,.82); border-radius:14px; padding:10px 12px; }
      .ideas-stat-box span { display:block; color:#9bb8d8; font-size:11px; text-transform:uppercase; letter-spacing:.08em; }
      .ideas-stat-box strong { display:block; margin-top:4px; font-size:18px; color:#fff; }
      .archive-card { border-color:rgba(250,204,21,.40) !important; background:linear-gradient(155deg,rgba(44,36,12,.94),rgba(7,22,42,.98) 70%) !important; }
      .archive-result-pill { display:inline-flex; width:fit-content; margin:8px 0 10px; padding:7px 11px; border-radius:999px; font-size:12px; font-weight:950; border:1px solid rgba(255,255,255,.18); }
      .archive-result-pill.win { background:rgba(34,197,94,.18); color:#bbf7d0; border-color:rgba(34,197,94,.38); }
      .archive-result-pill.loss { background:rgba(244,63,94,.18); color:#fecdd3; border-color:rgba(244,63,94,.42); }
      .archive-result-pill.cancel { background:rgba(250,204,21,.16); color:#fde68a; border-color:rgba(250,204,21,.38); }
      .archive-result-pill.other { background:rgba(148,163,184,.14); color:#e2e8f0; border-color:rgba(148,163,184,.32); }
      .archive-explanation { margin-top:10px; border:1px solid rgba(95,156,230,.28); background:rgba(2,10,20,.48); border-radius:14px; padding:11px 12px; color:#e8f3ff; line-height:1.55; font-size:13px; }
      .tf-switcher { position:absolute; left:18px; bottom:18px; z-index:28; display:flex; gap:7px; padding:6px; border-radius:999px; background:rgba(3,14,28,.78); border:1px solid rgba(255,255,255,.14); backdrop-filter:blur(8px); }
      .tf-btn { border:1px solid transparent; background:transparent; color:#cfe7ff; border-radius:999px; padding:6px 10px; font-size:12px; font-weight:950; cursor:pointer; }
      .tf-btn.active { color:#001b2e; background:#7dd3fc; }
      .chart-fullscreen .tf-switcher { left:24px; bottom:24px; }
    `;
    document.head.appendChild(style);
  }

  function activeItems(payload) {
    return safeArray(payload && (payload.ideas || payload.signals));
  }

  function archiveItems(payload) {
    return safeArray(payload && payload.archive);
  }

  function addArchiveDetails() {
    if (currentMode !== "archive") return;
    const items = archiveItems(lastPayload);
    document.querySelectorAll(".idea-card").forEach((card, index) => {
      const idea = items[index];
      if (!idea) return;
      card.classList.add("archive-card");
      if (card.querySelector(".archive-result-pill")) return;
      const result = ideaResult(idea);
      const pill = document.createElement("div");
      pill.className = `archive-result-pill ${result}`;
      pill.textContent = archiveReason(idea);
      const explanation = document.createElement("div");
      explanation.className = "archive-explanation";
      explanation.innerHTML = archiveExplanation(idea);
      const anchor = card.querySelector(".idea-summary") || card;
      anchor.appendChild(pill);
      anchor.appendChild(explanation);
    });
  }

  function renderTopBar(payload) {
    const container = document.getElementById("ideasContainer");
    if (!container) return;
    const active = activeItems(payload);
    const archive = archiveItems(payload);
    const stats = computeStats(archive);
    const bar = document.createElement("div");
    bar.className = "ideas-journal-bar";
    bar.innerHTML = `
      <button class="ideas-journal-btn ${currentMode === "active" ? "active" : ""}" data-mode="active">Активные идеи (${active.length})</button>
      <button class="ideas-journal-btn ${currentMode === "archive" ? "active" : ""}" data-mode="archive">Архив (${archive.length})</button>
      <span class="ideas-journal-note">Архив хранит TP / SL / отмену с объяснением и статистикой</span>
      <div class="ideas-stats-panel">
        <div class="ideas-stat-box"><span>Всего закрыто</span><strong>${stats.total}</strong></div>
        <div class="ideas-stat-box"><span>TP</span><strong>${stats.win}</strong></div>
        <div class="ideas-stat-box"><span>SL</span><strong>${stats.loss}</strong></div>
        <div class="ideas-stat-box"><span>Отмена</span><strong>${stats.cancel}</strong></div>
        <div class="ideas-stat-box"><span>Winrate</span><strong>${stats.winrate}%</strong></div>
      </div>
    `;
    container.prepend(bar);
    bar.querySelectorAll("[data-mode]").forEach((btn) => {
      btn.addEventListener("click", () => {
        currentMode = btn.getAttribute("data-mode") || "active";
        if (typeof window.renderIdeas === "function") window.renderIdeas(lastPayload || {});
      });
    });
  }

  if (typeof window.renderIdeas === "function") {
    const original = window.renderIdeas;
    window.renderIdeas = function renderIdeasWithArchive(payload) {
      ensureStyles();
      lastPayload = payload || {};
      const items = currentMode === "archive" ? archiveItems(lastPayload) : activeItems(lastPayload);
      const patched = Object.assign({}, lastPayload, { ideas: items, signals: items });
      original(patched);
      renderTopBar(lastPayload);
      addArchiveDetails();
    };
  }

  function candlesForTf(idea, tf) {
    if (!idea || tf === "AUTO") return null;
    const candidates = [
      idea.candles_by_tf,
      idea.candlesByTf,
      idea.chart_data_by_tf,
      idea.chartDataByTf,
      idea.market_context && idea.market_context.candles_by_tf,
      idea.market_context && idea.market_context.candlesByTf,
    ];
    for (const source of candidates) {
      if (source && Array.isArray(source[tf]) && source[tf].length >= 2) return source[tf];
    }
    return null;
  }

  function patchIdeaForTf(idea, tf) {
    const candles = candlesForTf(idea, tf);
    if (!candles) return idea;
    return Object.assign({}, idea, { candles, timeframe: tf, tf });
  }

  function addTfSwitcher(idea) {
    const container = document.getElementById("ideaModalChart");
    const area = container && container.closest(".chart-area");
    if (!area || area.querySelector(".tf-switcher")) return;
    const row = document.createElement("div");
    row.className = "tf-switcher";
    const tfs = ["AUTO", "M15", "H1", "H4"];
    row.innerHTML = tfs.map((tf) => `<button type="button" class="tf-btn ${currentTf === tf ? "active" : ""}" data-tf="${tf}">${tf}</button>`).join("");
    row.querySelectorAll("[data-tf]").forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.stopPropagation();
        currentTf = btn.getAttribute("data-tf") || "AUTO";
        row.querySelectorAll(".tf-btn").forEach((x) => x.classList.toggle("active", x === btn));
        if (typeof window.renderModalChart === "function") window.renderModalChart(patchIdeaForTf(idea, currentTf));
      });
    });
    area.appendChild(row);
  }

  if (typeof window.renderModalChart === "function") {
    const originalChart = window.renderModalChart;
    window.renderModalChart = function renderModalChartWithTf(idea) {
      originalChart(patchIdeaForTf(idea, currentTf));
      setTimeout(() => addTfSwitcher(idea), 30);
    };
  }
})();
