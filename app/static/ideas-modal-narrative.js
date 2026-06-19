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

  function firstMeaningfulText(...values) {
    for (const value of values) {
      const text = String(value ?? "").trim();
      if (text) return text;
    }
    return "";
  }

  function formatLevel(value) {
    const text = String(value ?? "").trim();
    return text || "не задан";
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
    return firstMeaningfulText(
      data?.article_ru,
      data?.idea_article_ru,
      data?.unified_narrative,
      data?.full_text,
      data?.text,
      parsed?.institutional_narrative,
      parsed?.unified_narrative,
      parsed?.full_text,
      parsed?.summary,
      rawReply,
    );
  }

  function institutionalFallbackArticle(idea) {
    const symbol = getSymbol(idea);
    const direction = directionRu(idea);
    const entry = formatLevel(idea?.entry ?? idea?.entry_price);
    const sl = formatLevel(idea?.sl ?? idea?.stop_loss);
    const tp = formatLevel(idea?.tp ?? idea?.take_profit ?? idea?.target);
    const timeframe = String(idea?.timeframe || idea?.tf || "рабочий ТФ").toUpperCase();
    const liquidity = firstMeaningfulText(
      idea?.market_structure?.liquidity,
      idea?.liquidity_context_ru,
      idea?.prop_signal_score?.liquidity_sweep,
      idea?.summary_structured?.liquidity
    );
    const zone = firstMeaningfulText(
      idea?.market_structure?.zone,
      idea?.fvg_context_ru,
      idea?.order_block_ru,
      idea?.prop_signal_score?.fvg_ob_context,
      idea?.summary_structured?.zone
    );
    const invalidation = firstMeaningfulText(idea?.invalidation, idea?.risk_note, idea?.risk_logic);

    const sweepText = liquidity
      ? `Smart Money сначала работает с ликвидностью: ${liquidity}.`
      : `Smart Money рассматривает ${symbol} на ${timeframe} через снятие ближайшей ликвидности перед движением.`;
    const inducementText = direction === "покупка"
      ? "Inducement здесь — попытка заманить продавцов ниже локального диапазона, чтобы собрать встречные ордера перед разворотом вверх."
      : direction === "продажа"
        ? "Inducement здесь — попытка заманить покупателей выше локального диапазона, чтобы собрать встречные ордера перед разворотом вниз."
        : "Inducement здесь — ложное вовлечение участников в очевидный пробой, после которого важна реакция цены у зоны интереса.";
    const zoneText = zone
      ? `Дальше внимание на FVG/OB: ${zone}.`
      : `Дальше внимание на FVG/OB: цена должна оставить дисбаланс или вернуться в ордерблок, где крупный участник может защищать позицию.`;
    const objectiveText = `Цель крупного игрока — набрать позицию без погони за ценой и направить поток к следующему пулу ликвидности; для сделки ${symbol} ориентир по плану: entry ${entry}, SL ${sl}, TP ${tp}.`;
    const consequenceText = direction === "покупка"
      ? "Ожидаемое следствие — удержание зоны спроса, импульсная реакция вверх и постепенный перенос цены к buy-side liquidity."
      : direction === "продажа"
        ? "Ожидаемое следствие — удержание зоны предложения, импульсная реакция вниз и постепенный перенос цены к sell-side liquidity."
        : "Ожидаемое следствие — сначала подтверждение реакции в зоне интереса, затем выбор направления после снятия ликвидности.";
    const invalidationText = invalidation
      ? `Invalidation: ${invalidation}.`
      : `Invalidation: сценарий отменяется, если цена закрепляется за SL ${sl} или ломает структуру, на которой построена идея.`;

    return `${symbol}: ${direction}. ${sweepText} ${inducementText} ${zoneText} ${objectiveText} ${consequenceText} ${invalidationText}`;
  }

  async function generateRemoteArticle(idea) {
    const localArticle = firstMeaningfulText(idea?.idea_article_ru, idea?.article_ru);
    if (localArticle) return localArticle;
    const localUnifiedNarrative = firstMeaningfulText(idea?.institutional_narrative, idea?.unified_narrative);
    if (localUnifiedNarrative) return localUnifiedNarrative;

    const key = `${getSymbol(idea)}:${idea?.id || idea?.idea_id || idea?.entry || ""}:${idea?.prop_score || ""}`;
    if (narrativeCache.has(key)) return narrativeCache.get(key);
    const payload = {
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
      zone: idea?.market_structure?.zone || idea?.fvg_context_ru || idea?.order_block_ru || idea?.summary_structured?.zone || "",
      news: idea?.news_context_ru || idea?.fundamental_context_ru || idea?.sentiment?.summary || "",
      options: idea?.options_summary_ru || idea?.options_analysis?.summary_ru || "",
      margin: idea?.prop_signal_score?.margin_zone_confluence || "",
      delta: idea?.prop_signal_score?.delta_divergence || "",
    };
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          message: `Объясни сигнал ${payload.symbol} как Smart Money institutional narrative: причина, действия крупного участника, ликвидность, inducement/sweep/FVG/OB, цель движения и invalidation. Верни JSON с full_text.`,
          context: payload,
        }),
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const article = extractReplyText(data);
      if (article) {
        narrativeCache.set(key, article);
        return article;
      }
    } catch (error) {
      // Fallback below keeps modal useful even when AI/API is unavailable.
    }
    return institutionalFallbackArticle(idea);
  }

  function ensureArticleSection(modal) {
    const body = modal && modal.querySelector(".ideas-modal-body");
    if (!body) return null;
    let section = body.querySelector(".ai-generated-article-section");
    if (!section) {
      section = document.createElement("section");
      section.className = "modal-section ai-generated-article-section";
      section.innerHTML = `<h4>Institutional Narrative</h4><div class="modal-text ai-generated-article">Генерирую institutional narrative...</div>`;
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
      target.textContent = "Генерирую institutional narrative...";
      generateRemoteArticle(idea).then((article) => {
        target.innerHTML = escapeHtml(article).replace(/\n+/g, "<br><br>");
      });
    };
  }
})();