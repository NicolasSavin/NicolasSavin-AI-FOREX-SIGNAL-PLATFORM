(function () {
  function statusRu(idea) {
    const raw = String((idea && idea.status) || "").trim().toLowerCase();
    const mode = String((idea && idea.prop_mode) || (idea && idea.prop_signal_score && idea.prop_signal_score.mode) || "").trim().toLowerCase();

    if (raw === "active" || raw === "активно") return "активно";
    if (raw === "triggered" || raw === "trigger" || raw === "сработало") return "сработало";
    if (raw === "created" || raw === "new") return "новая";
    if (raw === "tp_hit") return "TP достигнут";
    if (raw === "sl_hit") return "SL достигнут";
    if (raw === "archived" || raw === "closed") return "архив";

    if (mode === "prop_entry") return "активно";
    if (mode === "watchlist") return "наблюдение";
    if (mode === "no_trade" || mode === "research_only") return "заблокировано";

    return raw || "наблюдение";
  }

  function patchRenderedStatuses() {
    const cards = document.querySelectorAll(".idea-card");
    cards.forEach((card) => {
      const lines = card.querySelectorAll(".idea-news-line");
      lines.forEach((line) => {
        if (!/Статус:/i.test(line.textContent || "")) return;
        const strong = line.querySelector("strong");
        if (!strong) return;
        const text = String(strong.textContent || "").trim().toLowerCase();
        if (text === "ожидание" && card.textContent && /PROP ENTRY|Score\s+\d+\s*\/\s*100/i.test(card.textContent)) {
          // Do not blindly promote all waiting cards. This patch is intentionally conservative;
          // the durable fix is in renderIdeaCard override below when idea.status is available.
        }
      });
    });
  }

  if (typeof window.renderIdeaCard === "function") {
    const originalRenderIdeaCard = window.renderIdeaCard;
    window.renderIdeaCard = function renderIdeaCardStatusFixed(idea, index) {
      let html = originalRenderIdeaCard(idea, index);
      html = html.replace(/ · AI-идея/g, " · " + (typeof window.getIdeaDirection === "function" ? window.getIdeaDirection(idea) : "Идея"));
      const status = statusRu(idea);
      html = html.replace(/Статус:\s*<strong>[^<]*<\/strong>/g, "Статус: <strong>" + status + "</strong>");
      return html;
    };
  }

  const observer = new MutationObserver(() => patchRenderedStatuses());
  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
  }
})();
