(function () {
  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function num(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function safeArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function fmtPrice(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    if (Math.abs(n) >= 100) return n.toFixed(2);
    if (Math.abs(n) >= 10) return n.toFixed(3);
    return n.toFixed(5);
  }

  function ideaSymbol(item) {
    return String((item && (item.symbol || item.pair || item.instrument)) || "—").toUpperCase();
  }

  function ideaAction(item) {
    return String((item && (item.action || item.signal || item.final_signal || item.direction)) || "—").toUpperCase();
  }

  function ideaResult(item) {
    const status = String((item && (item.status || item.lifecycle_status || item.result)) || "").toLowerCase();
    if (status.includes("tp") || status === "win") return "TP";
    if (status.includes("sl") || status === "loss") return "SL";
    if (status.includes("cancel") || status.includes("expired") || status.includes("invalid")) return "CANCEL";
    return String((item && item.result) || status || "—").toUpperCase();
  }

  function ensureStyles() {
    if (document.getElementById("ideas-lifecycle-panel-style")) return;
    const style = document.createElement("style");
    style.id = "ideas-lifecycle-panel-style";
    style.textContent = `
      .lifecycle-panel {
        margin: 16px 0 18px;
        padding: 16px;
        border: 1px solid rgba(95,156,230,.30);
        border-radius: 20px;
        background: linear-gradient(145deg, rgba(8,25,48,.92), rgba(3,14,28,.96));
        box-shadow: 0 18px 48px rgba(0,0,0,.22);
      }
      .lifecycle-panel-head { display:flex; justify-content:space-between; align-items:flex-start; gap:14px; flex-wrap:wrap; margin-bottom:12px; }
      .lifecycle-panel-title { color:#f4f8ff; font-weight:950; font-size:17px; letter-spacing:.02em; }
      .lifecycle-panel-subtitle { margin-top:4px; color:#9bb8d8; font-size:12px; line-height:1.45; }
      .lifecycle-panel-refresh { border:1px solid rgba(84,255,181,.48); background:rgba(84,255,181,.12); color:#c9ffe5; border-radius:999px; padding:8px 12px; font-size:12px; font-weight:900; cursor:pointer; }
      .lifecycle-stats-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:10px; margin-bottom:12px; }
      .lifecycle-stat { border:1px solid rgba(95,156,230,.28); border-radius:15px; padding:10px 12px; background:rgba(2,10,20,.45); }
      .lifecycle-stat span { display:block; color:#9bb8d8; font-size:11px; text-transform:uppercase; letter-spacing:.08em; }
      .lifecycle-stat strong { display:block; margin-top:4px; color:#fff; font-size:20px; }
      .lifecycle-section-title { margin:12px 0 8px; color:#cfe7ff; font-size:13px; font-weight:950; text-transform:uppercase; letter-spacing:.08em; }
      .lifecycle-table-wrap { overflow:auto; border:1px solid rgba(95,156,230,.20); border-radius:16px; }
      .lifecycle-table { width:100%; border-collapse:collapse; min-width:720px; }
      .lifecycle-table th, .lifecycle-table td { padding:10px 11px; border-bottom:1px solid rgba(95,156,230,.14); text-align:left; color:#eaf4ff; font-size:12px; }
      .lifecycle-table th { color:#9bb8d8; background:rgba(2,10,20,.38); text-transform:uppercase; letter-spacing:.08em; font-size:10px; }
      .lifecycle-table tr:last-child td { border-bottom:0; }
      .lifecycle-pill { display:inline-flex; align-items:center; padding:5px 8px; border-radius:999px; font-size:11px; font-weight:950; border:1px solid rgba(255,255,255,.14); }
      .lifecycle-pill.buy { color:#bbf7d0; background:rgba(34,197,94,.16); border-color:rgba(34,197,94,.34); }
      .lifecycle-pill.sell { color:#fecdd3; background:rgba(244,63,94,.16); border-color:rgba(244,63,94,.34); }
      .lifecycle-pill.tp { color:#bbf7d0; background:rgba(34,197,94,.16); border-color:rgba(34,197,94,.34); }
      .lifecycle-pill.sl { color:#fecdd3; background:rgba(244,63,94,.16); border-color:rgba(244,63,94,.34); }
      .lifecycle-muted { color:#9bb8d8; }
      .lifecycle-error { color:#fecdd3; border:1px solid rgba(244,63,94,.28); border-radius:14px; padding:10px 12px; background:rgba(244,63,94,.10); }
    `;
    document.head.appendChild(style);
  }

  function normalizeActive(payload) {
    const ideas = safeArray(payload && (payload.ideas || payload.signals));
    return ideas.filter((item) => String((item && (item.lifecycle_status || item.status)) || "").toLowerCase() === "active" || item && item.locked_until_tp_sl);
  }

  function activeRows(items) {
    if (!items.length) return `<tr><td colspan="7" class="lifecycle-muted">Активных зафиксированных идей пока нет.</td></tr>`;
    return items.slice(0, 8).map((item) => {
      const action = ideaAction(item);
      return `
        <tr>
          <td>${escapeHtml(ideaSymbol(item))}</td>
          <td><span class="lifecycle-pill ${action === "BUY" ? "buy" : action === "SELL" ? "sell" : ""}">${escapeHtml(action)}</span></td>
          <td>${fmtPrice(item.entry || item.entry_price)}</td>
          <td>${fmtPrice(item.sl || item.stop_loss)}</td>
          <td>${fmtPrice(item.tp || item.take_profit || item.target)}</td>
          <td>${escapeHtml(item.prop_grade || (item.advisor_signal && item.advisor_signal.grade) || "—")}</td>
          <td>${escapeHtml(item.created_at_utc || item.created_at || "—")}</td>
        </tr>`;
    }).join("");
  }

  function archiveRows(items) {
    if (!items.length) return `<tr><td colspan="8" class="lifecycle-muted">Закрытых идей пока нет. Архив заполнится после TP или SL.</td></tr>`;
    return items.slice(0, 10).map((item) => {
      const action = ideaAction(item.final_action ? Object.assign({}, item, { action: item.final_action }) : item);
      const result = ideaResult(item);
      return `
        <tr>
          <td>${escapeHtml(ideaSymbol(item))}</td>
          <td><span class="lifecycle-pill ${action === "BUY" ? "buy" : action === "SELL" ? "sell" : ""}">${escapeHtml(action)}</span></td>
          <td><span class="lifecycle-pill ${result === "TP" ? "tp" : result === "SL" ? "sl" : ""}">${escapeHtml(result)}</span></td>
          <td>${fmtPrice(item.entry)}</td>
          <td>${fmtPrice(item.close_price)}</td>
          <td>${escapeHtml(item.result_r ?? "—")}</td>
          <td>${escapeHtml(item.closed_at_utc || "—")}</td>
          <td>${escapeHtml(item.idea_id || "—")}</td>
        </tr>`;
    }).join("");
  }

  async function loadJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    return response.json();
  }

  async function renderLifecyclePanel() {
    ensureStyles();
    let panel = document.getElementById("lifecyclePanel");
    if (!panel) {
      panel = document.createElement("section");
      panel.id = "lifecyclePanel";
      panel.className = "lifecycle-panel";
      const updated = document.getElementById("ideasUpdatedAt");
      if (updated && updated.parentNode) updated.parentNode.insertBefore(panel, updated.nextSibling);
      else (document.querySelector("main.page") || document.body).prepend(panel);
    }
    panel.innerHTML = `<div class="lifecycle-muted">Загружаю статистику и архив…</div>`;
    try {
      const [ideasPayload, statsPayload, archivePayload] = await Promise.all([
        loadJson("/api/ideas"),
        loadJson("/api/stats"),
        loadJson("/api/archive"),
      ]);
      const active = normalizeActive(ideasPayload);
      const archive = safeArray(archivePayload && archivePayload.archive);
      const stats = statsPayload || {};
      panel.innerHTML = `
        <div class="lifecycle-panel-head">
          <div>
            <div class="lifecycle-panel-title">Журнал идей: активные, архив, статистика</div>
            <div class="lifecycle-panel-subtitle">Активная идея фиксируется до TP или SL. Новая идея по символу создаётся только после закрытия старой.</div>
          </div>
          <button type="button" class="lifecycle-panel-refresh" id="lifecycleRefreshBtn">Обновить</button>
        </div>
        <div class="lifecycle-stats-grid">
          <div class="lifecycle-stat"><span>Активные</span><strong>${escapeHtml(stats.active ?? active.length)}</strong></div>
          <div class="lifecycle-stat"><span>Архив</span><strong>${escapeHtml(stats.archived ?? stats.total ?? archive.length)}</strong></div>
          <div class="lifecycle-stat"><span>TP</span><strong>${escapeHtml(stats.tp ?? 0)}</strong></div>
          <div class="lifecycle-stat"><span>SL</span><strong>${escapeHtml(stats.sl ?? 0)}</strong></div>
          <div class="lifecycle-stat"><span>Winrate</span><strong>${escapeHtml(stats.winrate ?? 0)}%</strong></div>
        </div>
        <div class="lifecycle-section-title">Активные идеи</div>
        <div class="lifecycle-table-wrap">
          <table class="lifecycle-table">
            <thead><tr><th>Символ</th><th>Сигнал</th><th>Entry</th><th>SL</th><th>TP</th><th>Grade</th><th>Создана</th></tr></thead>
            <tbody>${activeRows(active)}</tbody>
          </table>
        </div>
        <div class="lifecycle-section-title">Последние закрытые идеи</div>
        <div class="lifecycle-table-wrap">
          <table class="lifecycle-table">
            <thead><tr><th>Символ</th><th>Сигнал</th><th>Результат</th><th>Entry</th><th>Close</th><th>R</th><th>Закрыта</th><th>ID</th></tr></thead>
            <tbody>${archiveRows(archive)}</tbody>
          </table>
        </div>
      `;
      const btn = document.getElementById("lifecycleRefreshBtn");
      if (btn) btn.addEventListener("click", renderLifecyclePanel);
    } catch (error) {
      panel.innerHTML = `<div class="lifecycle-error">Не удалось загрузить статистику/архив: ${escapeHtml(error && error.message ? error.message : error)}</div>`;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderLifecyclePanel);
  } else {
    renderLifecyclePanel();
  }
  window.renderLifecyclePanel = renderLifecyclePanel;
})();
