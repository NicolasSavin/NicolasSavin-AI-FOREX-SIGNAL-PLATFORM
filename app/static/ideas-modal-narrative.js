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
    ];
    return banned.some((marker) => text.includes(marker));
  }

  function useOnlyIfInstitutional(value) {
    const text = String(value ?? "").trim();
    if (!text || isTemplateNarrative(text)) return "";
    const lowered = text.toLowerCase();
    const hasInstitutionalTerms = [
      "крупн", "smart money", "ликвид", "inducement", "sweep", "fvg", "order block", "ордерблок", "дисбаланс", "распредел", "накоплен",
    ].some((token) => lowered.includes(token));
    return hasInstitutionalTerms ? text : "";
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
    return useOnlyIfInstitutional(firstMeaningfulText(
      data?.article_ru,
      data?.idea_article_ru,
      data?.institutional_narrative,
      data?.unified_narrative,
      data?.full_text,
      data?.text,
      parsed?.institutional_narrative,
      parsed?.unified_narrative,
      parsed?.full_text,
      parsed?.summary,
      rawReply,
    ));
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
      idea?.summary_structured?.zone,
      idea?.selected_zone_type
    );
    const invalidation = firstMeaningfulText(idea?.invalidation, idea?.risk_note, idea?.risk_logic);

    const liquiditySide = direction === "покупка" ? "sell-side liquidity под локальными минимумами" : direction === "продажа" ? "buy-side liquidity над локальными максимумами" : "оба внешних пула ликвидности";
    const stopPool = direction === "покупка" ? "стопы ранних покупателей и sell-stop приказы продавцов" : direction === "продажа" ? "стопы продавцов и buy-stop входы поздних покупателей" : "стопы участников по обе стороны диапазона";
    const actionText = direction === "покупка"
      ? "Крупный участник мог сначала продавить цену ниже очевидной поддержки, получить встречный поток заявок и затем использовать его для накопления long-позиции."
      : direction === "продажа"
        ? "Крупный участник мог сначала вытянуть цену выше очевидного сопротивления, активировать стопы продавцов и заявки покупателей на пробой, а затем использовать эту ликвидность для распределения short-позиции."
        : "Крупный участник, вероятно, держит рынок в фазе inducement: толпа видит очевидный пробой, но подтверждённого displacement ещё нет.";
    const zoneText = zone
      ? `Рабочая зона ${zone} важна не сама по себе, а как место, где после sweep должна появиться защита позиции через FVG/OB или резкий отказ цены идти дальше.`
      : "Рабочая зона важна как потенциальный FVG/OB: там должен появиться отказ цены продолжать ложное движение и первая попытка доставки к противоположной ликвидности.";
    const objectiveText = `Цель такого поведения — не красивый вход по уровню, а получение ликвидности из ${liquidity || liquiditySide} и доставка цены к следующему пулу заявок; текущий план использует entry ${entry}, SL ${sl}, TP ${tp}.`;
    const consequenceText = direction === "покупка"
      ? "Если гипотеза верна, рынок должен удержать discount-зону, показать displacement вверх и начать доставку к buy-side liquidity."
      : direction === "продажа"
        ? "Если гипотеза верна, рынок должен удержать premium-зону, показать displacement вниз и начать доставку к sell-side liquidity."
        : "Если гипотеза верна, следующим подтверждением будет sweep одной стороны диапазона и импульсное принятие цены в обратном направлении.";
    const invalidationText = invalidation
      ? `Отмена сценария: ${invalidation}.`
      : `Отмена сценария наступит, если цена закрепится за SL ${sl} и вместо возврата в диапазон примет цену за зоной манипуляции.`;

    return `${symbol} ${timeframe}: ${direction}. ${actionText} Рабочее топливо движения — ${stopPool}; именно поэтому важно смотреть не на сам факт касания уровня, а на то, появилась ли после него агрессивная реакция крупного участника. ${zoneText} ${objectiveText} ${consequenceText} ${invalidationText}`;
  }

  async function generateRemoteArticle(idea) {
    const localArticle = useOnlyIfInstitutional(firstMeaningfulText(idea?.idea_article_ru, idea?.article_ru));
    if (localArticle) return localArticle;
    const localUnifiedNarrative = useOnlyIfInstitutional(firstMeaningfulText(idea?.institutional_narrative, idea?.unified_narrative));
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
      zone: idea?.market_structure?.zone || idea?.fvg_context_ru || idea?.order_block_ru || idea?.summary_structured?.zone || idea?.selected_zone_type || "",
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
          message: `Объясни сигнал ${payload.symbol} как Smart Money institutional narrative. Обязательно раскрой: что делал крупный участник, где была ликвидность, какой inducement/sweep/FVG/OB мог использоваться, зачем это делалось, к какой ликвидности доставляется цена и что отменяет гипотезу. Запрещены фразы: торгуется в сценарии, подтверждены, частично подтверждены, вход допустим. Верни JSON с full_text на русском в 3-5 абзацах.`,
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