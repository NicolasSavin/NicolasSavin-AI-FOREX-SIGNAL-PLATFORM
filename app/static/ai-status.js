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

  function renderAiStatus(root, status) {
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
      roots.forEach((root) => renderAiStatus(root, status));
    } catch (error) {
      roots.forEach((root) => renderAiStatus(root, { llm_available: false, provider: PROVIDER_LABEL, last_error: error.message }));
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    loadAiStatus();
    window.setInterval(loadAiStatus, 60000);
  });
})();
