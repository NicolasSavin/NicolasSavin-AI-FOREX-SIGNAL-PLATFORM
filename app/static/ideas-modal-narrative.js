(function () {
  const narrativeCache = new Map();

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function getSymbol(idea) {
    return String(idea?.instrument || idea?.symbol || idea?.pair || "РЫНОК").toUpperCase();
  }

  function directionRu(idea) {
    const raw = String(idea?.signal || idea?.label || idea?.direction || idea?.action || "WAIT").toUpperCase();
    if (raw.includes("BUY") || raw.includes("ПОКУП")) return "покупка";
    if (raw.includes("SELL") || raw.includes("ПРОДА")) return "продажа";
    return "наблюдение";
  }

  function compactCriteria(idea) {
    const rows = Array.isArray(idea?.prop_signal_score?.criteria) ? idea.prop_signal_score.criteria : [];
    return rows.map((row) => `${row.label_ru || row.key}: ${row.score}/${row.weight} ${row.status}; ${row.text_ru || ""}`).slice(0, 12);
  }

  function localFallbackArticle(idea) {
    const prop = idea?.prop_signal_score || {};
    const symbol = getSymbol(idea);
    const direction = directionRu(idea);
    const entry = idea?.entry ?? idea?.entry_price ?? "—";
    const sl = idea?.sl ?? idea?.stop_loss ?? "—";
    const tp = idea?.tp ?? idea?.take_profit ?? idea?.target ?? "—";
    const rr = idea?.rr ?? idea?.risk_reward ?? prop?.trade_geometry?.rr ?? "—";
    const mode = prop?.mode || idea?.prop_mode || "watchlist";
    const score = prop?.score ?? idea?.prop_score ?? "—";
    const grade = prop?.grade ?? idea?.prop_grade ?? "—";
    const blockers = Array.isArray(prop?.blockers) && prop.blockers.length ? ` Главные ограничения: ${prop.blockers.join("; ")}.` : "";
    const confirmed = Array.isArray(prop?.criteria)
      ? prop.criteria.filter((row) => row.status === "confirmed").map((row) => row.label_ru || row.key).slice(0, 5).join(", ")
      : "";
    const partial = Array.isArray(prop?.criteria)
      ? prop.criteria.filter((row) => row.status === "partial").map((row) => row.label_ru || row.key).slice(0, 5).join(", ")
      : "";
    const statusLine = mode === "prop_entry"
      ? "Сценарий близок к рабочему входу, но цена всё равно должна подтвердить реакцию в зоне."
      : mode === "watchlist"
        ? "Это watchlist-сценарий: идея интересная, но вход нужен только после дополнительного триггера."
        : "Это исследовательская идея, а не готовый вход. Система пока просит наблюдать, а не торговать.";
    return `${symbol}: ${direction}. Score ${score}/100, grade ${grade}. ${statusLine} Подтверждённые элементы: ${confirmed || "нет сильного набора подтверждений"}. Частичные элементы: ${partial || "нет"}. Рабочие уровни: entry ${entry}, SL ${sl}, TP ${tp}, R/R ${rr}. ${blockers} Простая логика такая: сначала цена должна показать удержание рабочей зоны и импульс в сторону идеи, затем можно оценивать вход; если цена уходит к SL или ломает структуру, сценарий отменяется.`;
  }

  async function generateRemoteArticle(idea) {
    const key = `${getSymbol(idea)}:${idea?.id || idea?.entry || ""}:${idea?.prop_score || ""}:${Date.now()}`;
    if (narrativeCache.has(key)) return narrativeCache.get(key);
    const payload = {
      symbol: getSymbol(idea),
      direction: directionRu(idea),
      signal: idea?.signal || idea?.action || idea?.label,
      timeframe: idea?.timeframe || idea?.tf || "M15",
      entry: idea?.entry ?? idea?.entry_price,
      sl: idea?.sl ?? idea?.stop_loss,
      tp: idea?.tp ?? idea?.take_profit ?? idea?.target,
      rr: idea?.rr ?? idea?.risk_reward,
      prop_score: idea?.prop_score,
      prop_grade: idea?.prop_grade,
      prop_mode: idea?.prop_mode,
      advisor_allowed: idea?.advisor_allowed,
      prop_decision_ru: idea?.prop_decision_ru,
      criteria: compactCriteria(idea),
      blockers: idea?.prop_signal_score?.blockers || [],
      news: idea?.news_context_ru || idea?.fundamental_context_ru || idea?.sentiment?.summary || "",
      options: idea?.options_summary_ru || idea?.options_analysis?.summary_ru || "",
      margin: idea?.prop_signal_score?.margin_zone_confluence || "",
      delta: idea?.prop_signal_score?.delta_divergence || "",
      request: "Сгенерируй одну простую понятную статью на русском, 8-12 предложений, без списков и блоков, уникальную для этой пары и текущих данных. Не придумывай уровни."
    };
    try {
      const response = await fetch("/api/idea-narrative", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const article = String(data.article_ru || data.unified_narrative || data.text || "").trim();
      if (article) {
        narrativeCache.set(key, article);
        return article;
      }
    } catch (error) {
      // Fallback below keeps modal useful even when AI quota/API is unavailable.
    }
    return localFallbackArticle(idea);
  }

  function ensureArticleSection(modal) {
    const body = modal && modal.querySelector(".ideas-modal-body");
    if (!body) return null;
    let section = body.querySelector(".ai-generated-article-section");
    if (!section) {
      section = document.createElement("section");
      section.className = "modal-section ai-generated-article-section";
      section.innerHTML = `<h4>Понятное описание идеи</h4><div class="modal-text ai-generated-article">Генерирую уникальное описание идеи...</div>`;
      body.insertBefore(section, body.firstChild);
    }
    return section.querySelector(".ai-generated-article");
  }

  const originalOpenIdeaModal = window.openIdeaModal;
  if (typeof originalOpenIdeaModal === "function") {
    window.openIdeaModal = function openIdeaModalWithGeneratedNarrative(idea) {
      originalOpenIdeaModal(idea);
      const modal = document.getElementById("ideasModal");
      const target = ensureArticleSection(modal);
      if (!target) return;
      target.textContent = "Генерирую уникальное описание идеи...";
      generateRemoteArticle(idea).then((article) => {
        target.innerHTML = escapeHtml(article).replace(/\n+/g, "<br><br>");
      });
    };
  }
})();
