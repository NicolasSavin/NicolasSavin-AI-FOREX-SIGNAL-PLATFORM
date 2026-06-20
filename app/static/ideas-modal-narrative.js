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

  function isTemplateNarrative(value) {
    const text = String(value || "").toLowerCase();
    if (!text) return false;
    const banned = [
      "торгуется в сценарии",
      "подтверждены:",
      "частично подтверждены:",
      "вход допустим только",
      "план строится от уровней",
      "подтверждённые элементы",
      "рабочие уровни",
      "простая логика",
      "сценарий близок к рабочему входу",
      "текущая ситуация",
      "структура m15",
      "ключевые уровни",
      "support/resistance",
      "основной сценарий",
      "альтернативный сценарий",
      "риск-менеджмент",
      "последние 30 свечей",
      "последние 80 свечей",
      "данные ohlc",
      "tick-volume",
    ];
    return banned.some((marker) => text.includes(marker));
  }

  function useOnlyIfGrokInstitutional(value) {
    const text = String(value ?? "").trim();
    if (!text || isTemplateNarrative(text)) return "";
    const lowered = text.toLowerCase();
    const hasInstitutionalTerms = [
      "крупн", "smart money", "ликвид", "inducement", "sweep", "fvg", "order block", "ордерблок", "дисбаланс", "распредел", "накоплен", "stop run", "стоп",
    ].some((token) => lowered.includes(token));
    return hasInstitutionalTerms ? text : "";
  }

  function extractReplyText(data) {
    const rawReply = String(data?.reply || "").trim();
    let parsed = null;
    if (rawReply) {
      const cleaned = rawReply.replace(/^```json/i, "").replace(/^```/i, "").replace(/```$/i, "").trim();
      const match = cleaned.match(/\{[\s\S]*\}/);
      for (const candidate of [cleaned, match ? match[0] : ""].filter(Boolean)) {
        try {
          parsed = JSON.parse(candidate);
          break;
        } catch (error) {
          // Try the next JSON candidate.
        }
      }
    }
    const candidates = [
      data?.article_ru,
      data?.idea_article_ru,
      data?.institutional_narrative,
      data?.unified_narrative,
      data?.full_text,
      data?.text,
      parsed?.full_text,
      parsed?.institutional_narrative,
      parsed?.unified_narrative,
      parsed?.institutional_thesis,
      parsed?.cause_effect_chain,
      rawReply,
    ];
    for (const candidate of candidates) {
      const accepted = useOnlyIfGrokInstitutional(candidate);
      if (accepted) return accepted;
    }
    return "";
  }

  function grokUnavailableText(reason) {
    return [
      "Grok narrative unavailable.",
      "Fallback отключён специально, чтобы не показывать технический шаблон вместо ответа нейросети.",
      `Причина: ${reason || "OpenRouter/Grok не вернул валидный Smart Money narrative"}.`,
      "Проверь Network → POST /api/chat и Render Logs: chat_openrouter_request, explanation_mode=true, chat_openrouter_success или chat_openrouter_request_failed.",
    ].join("\n\n");
  }

  async function generateRemoteArticle(idea) {
    const key = `${getSymbol(idea)}:${idea?.id || idea?.idea_id || idea?.entry || ""}:${idea?.prop_score || ""}`;
    if (narrativeCache.has(key)) return narrativeCache.get(key);

    const payload = {
      force_institutional_narrative: true,
      symbol: getSymbol(idea),
      direction: directionRu(idea),
      signal: idea?.signal || idea?.action || idea?.label,
      timeframe: idea?.timeframe || idea?.tf || "M15",
      status: idea?.status || "ACTIVE",
      entry: idea?.entry ?? idea?.entry_price,
      sl: idea?.sl ?? idea?.stop_loss,
      stop_loss: idea?.sl ?? idea?.stop_loss,
      tp: idea?.tp ?? idea?.take_profit ?? idea?.target,
      take_profit: idea?.tp ?? idea?.take_profit ?? idea?.target,
      rr: idea?.rr ?? idea?.risk_reward,
      confidence: idea?.confidence ?? idea?.prop_score,
      prop_score: idea?.prop_score,
      prop_grade: idea?.prop_grade,
      prop_mode: idea?.prop_mode,
      advisor_allowed: idea?.advisor_allowed,
      prop_decision_ru: idea?.prop_decision_ru,
      criteria: compactCriteria(idea),
      blockers: idea?.prop_signal_score?.blockers || [],
      liquidity: idea?.market_structure?.liquidity || idea?.liquidity_context_ru || idea?.summary_structured?.liquidity || "",
      zone: idea?.market_structure?.zone || idea?.fvg_context_ru || idea?.order_block_ru || idea?.summary_structured?.zone || idea?.selected_zone_type || "",
      news: idea?.news_context_ru || idea?.fundamental_context_ru || idea?.sentiment?.summary || "",
      options: idea?.options_summary_ru || idea?.options_analysis?.summary_ru || "",
      margin: idea?.prop_signal_score?.margin_zone_confluence || "",
      delta: idea?.prop_signal_score?.delta_divergence || "",
    };

    let data = null;
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          message: `FORCE_IDEA_EXPLANATION. Объясни сигнал ${payload.symbol} как Smart Money institutional narrative. Обязательно раскрой: что делал крупный участник, где была ликвидность, какой inducement/sweep/FVG/OB мог использоваться, зачем это делалось, к какой ликвидности доставляется цена и что отменяет гипотезу. Запрещены фразы: текущая ситуация, ключевые уровни, основной сценарий, альтернативный сценарий, риск-менеджмент, торгуется в сценарии, подтверждены, частично подтверждены, вход допустим. Верни JSON с full_text на русском в 3-5 абзацах.`,
          context: payload,
        }),
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      data = await response.json();
      const article = extractReplyText(data);
      if (article) {
        narrativeCache.set(key, article);
        return article;
      }
      return grokUnavailableText(`невалидный ответ Grok/OpenRouter; source=${data?.source || "unknown"}; status=${data?.dataStatus || "unknown"}`);
    } catch (error) {
      return grokUnavailableText(error && error.message ? error.message : "ошибка запроса /api/chat");
    }
  }

  function ensureArticleSection(modal) {
    const body = modal && modal.querySelector(".ideas-modal-body");
    if (!body) return null;
    let section = body.querySelector(".ai-generated-article-section");
    if (!section) {
      section = document.createElement("section");
      section.className = "modal-section ai-generated-article-section";
      section.innerHTML = `<h4>Institutional Narrative</h4><div class="modal-text ai-generated-article">Запрашиваю Grok/OpenRouter...</div>`;
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
      target.textContent = "Запрашиваю Grok/OpenRouter...";
      generateRemoteArticle(idea).then((article) => {
        target.innerHTML = escapeHtml(article).replace(/\n+/g, "<br><br>");
      });
    };
  }
})();