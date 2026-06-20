(function () {
  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function getSymbol(idea) {
    return String(idea?.instrument || idea?.symbol || idea?.pair || "").toUpperCase();
  }

  function pickText(data) {
    const reply = String(data?.reply || "").trim();
    let parsed = null;
    if (reply) {
      const cleaned = reply.replace(/^```json/i, "").replace(/^```/i, "").replace(/```$/i, "").trim();
      const match = cleaned.match(/\{[\s\S]*\}/);
      for (const item of [cleaned, match ? match[0] : ""].filter(Boolean)) {
        try {
          parsed = JSON.parse(item);
          break;
        } catch (error) {}
      }
    }
    return String(
      parsed?.full_text ||
      parsed?.institutional_narrative ||
      parsed?.unified_narrative ||
      parsed?.institutional_thesis ||
      data?.full_text ||
      data?.institutional_narrative ||
      data?.unified_narrative ||
      reply ||
      ""
    ).trim();
  }

  async function loadNarrative(idea) {
    const payload = {
      force_institutional_narrative: true,
      symbol: getSymbol(idea),
      direction: idea?.direction || idea?.signal || idea?.action || idea?.label || "",
      timeframe: idea?.timeframe || idea?.tf || "M15",
      status: idea?.status || "ACTIVE",
      entry: idea?.entry ?? idea?.entry_price,
      sl: idea?.sl ?? idea?.stop_loss,
      tp: idea?.tp ?? idea?.take_profit ?? idea?.target,
      rr: idea?.rr ?? idea?.risk_reward,
      score: idea?.prop_score ?? idea?.confidence,
      context: idea,
    };

    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        message: "FX idea explanation request. Use context only. Return JSON with full_text.",
        context: payload,
      }),
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const text = pickText(data);
    if (!text || data?.data_source === "mt4_bridge" || data?.article_ru) {
      throw new Error(`Unexpected response source=${data?.source || data?.data_source || "unknown"}`);
    }
    return text;
  }

  function ensureArticleSection(modal) {
    const body = modal && modal.querySelector(".ideas-modal-body");
    if (!body) return null;
    let section = body.querySelector(".ai-generated-article-section");
    if (!section) {
      section = document.createElement("section");
      section.className = "modal-section ai-generated-article-section";
      section.innerHTML = `<h4>Institutional Narrative</h4><div class="modal-text ai-generated-article">Requesting Grok/OpenRouter...</div>`;
      body.insertBefore(section, body.firstChild);
    }
    return section.querySelector(".ai-generated-article");
  }

  const originalOpenIdeaModal = window.openIdeaModal;
  if (typeof originalOpenIdeaModal === "function") {
    window.openIdeaModal = function openIdeaModalWithGrokNarrative(idea) {
      originalOpenIdeaModal(idea);
      const target = ensureArticleSection(document.getElementById("ideasModal"));
      if (!target) return;
      target.textContent = "Requesting Grok/OpenRouter...";
      loadNarrative(idea)
        .then((text) => {
          target.innerHTML = escapeHtml(text).replace(/\n+/g, "<br><br>");
        })
        .catch((error) => {
          target.innerHTML = escapeHtml(`Grok narrative unavailable.\n\n${error.message || error}`);
        });
    };
  }
})();