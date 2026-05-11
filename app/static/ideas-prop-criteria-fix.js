(function () {
  const CACHE_TTL_MS = 30000;
  let cache = { ts: 0, signals: [] };

  function norm(value) {
    return String(value || "").trim().toUpperCase();
  }

  function getSymbol(idea) {
    return norm(idea && (idea.instrument || idea.symbol || idea.pair || idea.id));
  }

  function getScoreObject(idea) {
    return idea && idea.prop_signal_score && typeof idea.prop_signal_score === "object" ? idea.prop_signal_score : null;
  }

  async function loadSignals() {
    if (Date.now() - cache.ts < CACHE_TTL_MS && cache.signals.length) return cache.signals;
    try {
      const response = await fetch("/api/ideas?ui_fix=1&t=" + Date.now(), { cache: "no-store" });
      if (!response.ok) return cache.signals;
      const payload = await response.json();
      const signals = Array.isArray(payload) ? payload : Array.isArray(payload.signals) ? payload.signals : [];
      cache = { ts: Date.now(), signals };
      return signals;
    } catch (error) {
      return cache.signals;
    }
  }

  function getOpenModalSymbol() {
    const title = document.querySelector(".ideas-modal-title") || document.querySelector(".modal h1, .modal-title, h1");
    const text = String(title && title.textContent || "").toUpperCase();
    const known = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "NASDAQ", "US500", "SPX500"];
    return known.find((symbol) => text.includes(symbol)) || "";
  }

  function findIdeaForDom(signals) {
    const symbol = getOpenModalSymbol();
    if (symbol) {
      const exact = signals.find((idea) => getSymbol(idea) === symbol);
      if (exact) return exact;
    }
    return signals[0] || null;
  }

  function criteriaMap(idea) {
    const score = getScoreObject(idea);
    const rows = Array.isArray(score && score.criteria) ? score.criteria : [];
    const map = new Map();
    rows.forEach((row) => {
      const key = norm(row.key || row.label_ru);
      const label = String(row.label_ru || row.key || "");
      if (key) map.set(key, row);
      if (label) map.set(norm(label), row);
    });
    return map;
  }

  function findCriterionForText(map, text) {
    const lower = String(text || "").toLowerCase();
    if (lower.includes("sentiment")) return map.get("SENTIMENT");
    if (lower.includes("новост") || lower.includes("фундамент")) return map.get("NEWS") || map.get("НОВОСТИ / ФУНДАМЕНТАЛ");
    if (lower.includes("маржин") || lower.includes("dealer")) return map.get("MARGIN_ZONES") || map.get("МАРЖИНАЛЬНЫЕ / DEALER ZONES");
    if (lower.includes("cumdelta") || lower.includes("delta")) return map.get("CUM_DELTA") || map.get("CUMDELTA / DELTA");
    if (lower.includes("опцион") || lower.includes("cme")) return map.get("OPTIONS") || map.get("ОПЦИОНЫ / CME СЛОЙ");
    return null;
  }

  function renderCriterion(row) {
    const score = Number.isFinite(Number(row.score)) ? Number(row.score) : 0;
    const weight = Number.isFinite(Number(row.weight)) ? Number(row.weight) : "—";
    const status = String(row.status || (score > 0 ? "partial" : "missing"));
    const label = String(row.label_ru || row.key || "Критерий");
    return `<strong>${escapeHtml(label)}</strong><br>${escapeHtml(score)} / ${escapeHtml(weight)} · ${escapeHtml(status)}`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function patchAdvisor(score, idea) {
    const advisorText = idea && (idea.advisor_allowed || (idea.advisor_signal && idea.advisor_signal.allowed)) ? "ALLOWED" : "BLOCKED";
    document.querySelectorAll(".modal-meta div").forEach((box) => {
      const txt = String(box.textContent || "").toLowerCase();
      if (!txt.includes("advisor")) return;
      const strong = box.querySelector("strong");
      if (strong) strong.textContent = advisorText;
    });
  }

  async function patchCriteria() {
    const signals = await loadSignals();
    const idea = findIdeaForDom(signals);
    if (!idea) return;
    const map = criteriaMap(idea);
    const score = getScoreObject(idea);
    document.querySelectorAll(".criterion").forEach((node) => {
      const row = findCriterionForText(map, node.textContent);
      if (!row) return;
      node.classList.remove("confirmed", "partial", "missing");
      node.classList.add(String(row.status || "partial"));
      node.innerHTML = renderCriterion(row);
    });
    patchAdvisor(score, idea);
  }

  const debouncedPatch = (() => {
    let timer = null;
    return () => {
      clearTimeout(timer);
      timer = setTimeout(patchCriteria, 150);
    };
  })();

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", debouncedPatch);
  } else {
    debouncedPatch();
  }
  setInterval(debouncedPatch, 5000);
  new MutationObserver(debouncedPatch).observe(document.documentElement, { childList: true, subtree: true });
})();
