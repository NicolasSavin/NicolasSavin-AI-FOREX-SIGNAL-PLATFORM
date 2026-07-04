(function () {
  const PROVIDER_LABEL = "OpenRouter";
  const MODEL_LABEL = "Grok";

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }

  function timeAgo(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
    if (seconds < 60) return `${seconds} сек назад`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} мин назад`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} ч назад`;
    return `${Math.floor(hours / 24)} дн назад`;
  }

  function normalizeOrderflowSource(payload) {
    const idea = Array.isArray(payload?.ideas) ? payload.ideas.find((item) => item && typeof item === "object") : (Array.isArray(payload) ? payload.find((item) => item && typeof item === "object") : payload);
    const label = idea?.data_source_label || idea?.orderflow_source_label || "Unknown Source";
    const raw = String(idea?.data_source || idea?.orderflow_provider || label || "").toLowerCase();
    const status = String(idea?.data_source_status || idea?.orderflow_status || "").toLowerCase();
    const unavailable = status.includes("unavailable") || status.includes("offline") || raw === "unavailable";
    if (/databento|cme/.test(raw) || /databento|cme/i.test(label)) return { icon: "🟢", label: label === "Unknown Source" ? "Databento CME" : label };
    if (/mt4|bridge|broker/.test(raw) || /mt4|bridge/i.test(label)) return { icon: "🟡", label: label === "Unknown Source" ? "MT4 Bridge" : label };
    if (/cache|histor/.test(raw) || /cache|histor/i.test(label)) return { icon: "🟠", label: label === "Unknown Source" ? "Cache" : label };
    if (unavailable) return { icon: "🔴", label: label === "Unknown Source" ? "Offline" : label };
    return { icon: "⚪", label };
  }

  function renderAiStatus(root, status, orderflowSource) {
    const active = Boolean(status?.llm_available);
    const error = status?.last_error || (!status?.api_key_configured ? "OPENROUTER_API_KEY не настроен" : "LLM недоступна");
    root.classList.toggle("ai-status-card--active", active);
    root.classList.toggle("ai-status-card--offline", !active);
    root.innerHTML = `
      <div class="ai-status-card__head">
        <span class="ai-status-card__dot" aria-hidden="true">${active ? "🟢" : "🔴"}</span>
        <div>
          <p class="ai-status-card__kicker">AI Status</p>
          <strong>${active ? "AI Active" : "AI Offline"}</strong>
        </div>
      </div>
      <div class="ai-status-card__grid">
        <span>Provider: <b>${escapeHtml(status?.provider || PROVIDER_LABEL)}</b></span>
        <span>Model: <b>${escapeHtml(MODEL_LABEL)}</b></span>
        <span>${active ? "Last Success" : "Last Error"}: <b>${active ? escapeHtml(timeAgo(status?.last_success_time)) : escapeHtml(error)}</b></span>
        <span>OrderFlow: <b>${escapeHtml(orderflowSource ? `${orderflowSource.icon} ${orderflowSource.label}` : "Unknown Source")}</b></span>
      </div>
    `;
  }

  async function loadAiStatus() {
    const roots = Array.from(document.querySelectorAll("[data-ai-status-root]"));
    if (!roots.length) return;
    try {
      const response = await fetch("/api/ai/status", { cache: "no-store" });
      if (!response.ok) throw new Error(`status_${response.status}`);
      const status = await response.json();
      let orderflowSource = null;
      try {
        const ideasResponse = await fetch("/api/ideas/market", { cache: "no-store" });
        if (ideasResponse.ok) orderflowSource = normalizeOrderflowSource(await ideasResponse.json());
      } catch (_) {}
      roots.forEach((root) => renderAiStatus(root, status, orderflowSource));
    } catch (error) {
      roots.forEach((root) => renderAiStatus(root, { llm_available: false, provider: PROVIDER_LABEL, last_error: error.message }, null));
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    loadAiStatus();
    window.setInterval(loadAiStatus, 60000);
  });
})();
