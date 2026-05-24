// Hotfix: prevent the /ideas page from staying in an infinite loading state
// when the FX market is closed, data providers are unavailable, or /ideas/market is slow.

const IDEAS_MARKET_FIX_TIMEOUT_MS = 12000;

function isLikelyForexMarketClosed(date = new Date()) {
  const day = date.getUTCDay();
  const hour = date.getUTCHours();
  const minute = date.getUTCMinutes();
  const minutes = hour * 60 + minute;

  // Approximate FX weekend: Friday 22:00 UTC -> Sunday 22:00 UTC.
  if (day === 6) return true;
  if (day === 5 && minutes >= 22 * 60) return true;
  if (day === 0 && minutes < 22 * 60) return true;
  return false;
}

function buildIdeasEmptyMessage(payload, error) {
  const diagnostics = payload?.diagnostics || {};
  const failedSymbols = Array.isArray(diagnostics.failed_symbols) ? diagnostics.failed_symbols : [];
  const apiMessage = diagnostics.error || payload?.message || payload?.warning || payload?.metric_warning_ru || "";
  const marketClosed = Boolean(payload?.market_closed) || isLikelyForexMarketClosed();

  if (marketClosed) {
    return {
      title: "Рынок сейчас закрыт",
      body: "Новые AI-идеи появятся после открытия рынка. Последние данные могут быть недоступны, поэтому сайт не должен строить свежие торговые сценарии на пустых котировках.",
      details: failedSymbols.length ? `Не удалось обновить: ${failedSymbols.join(", ")}.` : apiMessage,
    };
  }

  if (error?.name === "AbortError") {
    return {
      title: "API идей отвечает слишком долго",
      body: "Я остановил ожидание, чтобы страница не висела на загрузке. Обычно это бывает из-за долгого запроса к провайдеру свечей или AI-генерации.",
      details: "Повторная попытка будет автоматически через минуту.",
    };
  }

  return {
    title: "Идеи пока недоступны",
    body: "Backend ответил без активных идей или данные провайдера временно недоступны.",
    details: failedSymbols.length ? `Пары без данных: ${failedSymbols.join(", ")}.` : apiMessage,
  };
}

function renderIdeasEmptyState(payload, error) {
  if (!ideasContainer) return;
  const message = buildIdeasEmptyMessage(payload, error);
  ideasContainer.innerHTML = `
    <div class="ideas-loading">
      <strong>${escapeHtml(message.title)}</strong><br>
      ${escapeHtml(message.body)}
      ${message.details ? `<br><br><span>${escapeHtml(message.details)}</span>` : ""}
    </div>`;
}

async function getJson(url) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), IDEAS_MARKET_FIX_TIMEOUT_MS);
  try {
    const resp = await fetch(url, { cache: "no-store", signal: controller.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

function renderIdeas(payload) {
  const rawIdeas = Array.isArray(payload?.ideas) ? payload.ideas : Array.isArray(payload?.signals) ? payload.signals : [];
  const ideas = filterIdeasByProp(rawIdeas);
  lastPayload = payload;
  if (ideasUpdatedAt) ideasUpdatedAt.textContent = `Обновление: ${formatUpdatedAt(payload?.updated_at_utc)}`;
  if (!rawIdeas.length) {
    renderIdeasEmptyState(payload);
    return;
  }
  ideasContainer.innerHTML = renderPropFilters() + (ideas.length ? `<div class="ideas-grid">${ideas.map(renderIdeaCard).join("")}</div>` : `<div class="ideas-loading">Нет идей под выбранный фильтр.</div>`);
  ideasContainer.querySelectorAll("[data-prop-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      currentPropFilter = btn.getAttribute("data-prop-filter") || "all";
      renderIdeas(lastPayload);
    });
  });
  ideasContainer.querySelectorAll("[data-idea-index]").forEach((card) => {
    card.addEventListener("click", () => openIdeaModal(ideas[Number(card.getAttribute("data-idea-index"))]));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") openIdeaModal(ideas[Number(card.getAttribute("data-idea-index"))]);
    });
  });
}

async function loadIdeas() {
  try {
    const payload = await getJson("/ideas/market");
    const ideas = Array.isArray(payload?.ideas) ? payload.ideas : Array.isArray(payload?.signals) ? payload.signals : [];
    const voiceMessages = collectVoiceNotifications(ideas);
    renderIdeas(payload);
    if (hasLoadedIdeasOnce && isVoiceEnabled()) voiceMessages.forEach(enqueueVoiceMessage);
    hasLoadedIdeasOnce = true;
  } catch (error) {
    console.error("ideas_load_failed", error);
    renderIdeasEmptyState(null, error);
    if (ideasUpdatedAt) ideasUpdatedAt.textContent = "Обновление: данные временно недоступны";
  }
}
