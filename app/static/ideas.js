const ideasContainer = document.getElementById("ideasContainer");
const ideasUpdatedAt = document.getElementById("ideasUpdatedAt");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatUpdatedAt(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date) + " UTC";
}

async function getJson(url) {
  const resp = await fetch(url, { cache: "no-store" });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

function labelClass(label) {
  if (label === "BUY IDEA" || label === "ИДЕЯ ПОКУПКИ") return "idea-label-buy";
  if (label === "SELL IDEA" || label === "ИДЕЯ ПРОДАЖИ") return "idea-label-sell";
  return "idea-label-watch";
}

function renderIdeaCard(idea) {
  const chartImageUrl = idea.chartImageUrl || idea.chart_image || null;
  const tradePlan = idea.trade_plan || {};
  const updates = Array.isArray(idea.updates) ? idea.updates.slice(-5).reverse() : [];
  const reasoning = resolveVisibleNarrative(idea);
  const compactSummary = String(idea?.compact_summary || "").trim();

  return `
    <article class="idea-card">
      <div class="idea-card-top">
        <div class="idea-card-meta">
          <div class="idea-instrument">${escapeHtml(idea.instrument || "РЫНОК")}</div>
          <h3 class="idea-title">${escapeHtml(idea.title || "AI-идея")}</h3>
          <div class="idea-news-line">Основание: ${escapeHtml(idea.news_title || "Рыночная новость")}</div>
          <div class="idea-news-line">Статус: <strong>${escapeHtml(idea.status || "ожидание")}</strong></div>
        </div>
        <div class="idea-label ${labelClass(idea.label)}">${escapeHtml(idea.label || "НАБЛЮДЕНИЕ")}</div>
      </div>

      <div class="idea-summary">${escapeHtml(reasoning)}</div>
      ${compactSummary ? `<div class="idea-news-line">МТФ: ${escapeHtml(compactSummary)}</div>` : ""}
      <div class="idea-news-line">Источник описания: <strong>${escapeHtml(idea.narrative_source || "резервный_шаблон")}</strong></div>

      ${
        chartImageUrl
          ? `<div class="idea-chart-wrap">
               <img class="idea-chart-image" src="${escapeHtml(chartImageUrl)}?t=${Date.now()}" alt="${escapeHtml(idea.title || "chart")}" />
             </div>`
          : `<div class="idea-chart-missing">Снапшот графика недоступен (${escapeHtml(idea.chartSnapshotStatus || idea.chart_snapshot_status || "no_data")}).</div>`
      }

      <section class="idea-section idea-section-plan">
        <h4>Единый нарратив</h4>
        <p>${escapeHtml(reasoning)}</p>
      </section>

      <section class="idea-section idea-section-plan">
        <h4>Торговый сценарий</h4>
        <ul class="trade-plan-list">
          <li><strong>Уклон:</strong> ${escapeHtml(tradePlan.уклон || tradePlan.bias || "нейтральный")}</li>
          <li><strong>Зона работы:</strong> ${escapeHtml(tradePlan.entry_zone || "")}</li>
          <li><strong>Инвалидация:</strong> ${escapeHtml(tradePlan.invalidation || "")}</li>
          <li><strong>Цель 1:</strong> ${escapeHtml(tradePlan.target_1 || "")}</li>
          <li><strong>Цель 2:</strong> ${escapeHtml(tradePlan.target_2 || "")}</li>
          <li><strong>Альтернатива:</strong> ${escapeHtml(tradePlan.alternative_scenario_ru || "")}</li>
        </ul>
      </section>

      <section class="idea-section idea-section-plan">
        <h4>Лента обновлений</h4>
        ${
          updates.length
            ? `<ul class="trade-plan-list">${updates
                .map(
                  (item) =>
                    `<li><strong>${escapeHtml(item.event_type || "updated")}:</strong> ${escapeHtml(item.explanation || "")} <em>(${escapeHtml(
                      formatUpdatedAt(item.timestamp),
                    )})</em></li>`,
                )
                .join("")}</ul>`
            : "<p>Пока нет событий в lifecycle.</p>"
        }
      </section>
    </article>
  `;
}

function resolveVisibleNarrative(idea) {
  const thesis = String(idea?.idea_thesis || "").trim();
  if (thesis) return thesis;
  const unified = String(idea?.unified_narrative || "").trim();
  if (unified) return unified;
  const fullText = String(idea?.full_text || "").trim();
  if (fullText) return fullText;
  const fallbackNarrative = String(idea?.fallback_narrative || "").trim();
  if (fallbackNarrative) return fallbackNarrative;
  return "Сценарий в режиме fallback: модельный нарратив временно недоступен.";
}

function renderIdeas(payload) {
  const ideas = payload?.ideas || [];

  if (ideasUpdatedAt) {
    ideasUpdatedAt.textContent = `Обновление: ${formatUpdatedAt(payload?.updated_at_utc)}`;
  }

  if (!ideas.length) {
    ideasContainer.innerHTML = `<div class="ideas-loading">Идеи пока недоступны.</div>`;
    return;
  }

  ideasContainer.innerHTML = ideas.map(renderIdeaCard).join("");
}

async function loadIdeas() {
  try {
    const payload = await getJson("/ideas/market");
    renderIdeas(payload);
  } catch {
    ideasContainer.innerHTML = `<div class="ideas-loading">Не удалось загрузить идеи.</div>`;
    if (ideasUpdatedAt) ideasUpdatedAt.textContent = "Обновление: ошибка загрузки";
  }
}

loadIdeas();
setInterval(loadIdeas, 60000);
