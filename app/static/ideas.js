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
  if (label === "BUY IDEA") return "idea-label-buy";
  if (label === "SELL IDEA") return "idea-label-sell";
  return "idea-label-watch";
}

function renderIdeaCard(idea) {
  const chartImageUrl = idea.chartImageUrl || idea.chart_image || null;
  const analysis = idea.analysis || {};
  const tradePlan = idea.trade_plan || {};
  const updates = Array.isArray(idea.updates) ? idea.updates.slice(-5).reverse() : [];
  const reasoning = idea.current_reasoning || idea.full_text || "";

  return `
    <article class="idea-card">
      <div class="idea-card-top">
        <div class="idea-card-meta">
          <div class="idea-instrument">${escapeHtml(idea.instrument || "MARKET")}</div>
          <h3 class="idea-title">${escapeHtml(idea.title || "AI-идея")}</h3>
          <div class="idea-news-line">Основание: ${escapeHtml(idea.news_title || "Рыночная новость")}</div>
          <div class="idea-news-line">Статус: <strong>${escapeHtml((idea.status || "waiting").toUpperCase())}</strong></div>
        </div>
        <div class="idea-label ${labelClass(idea.label)}">${escapeHtml(idea.label || "WATCH")}</div>
      </div>

      <div class="idea-summary">${escapeHtml(idea.summary_ru || "")}</div>

      ${
        chartImageUrl
          ? `<div class="idea-chart-wrap">
               <img class="idea-chart-image" src="${escapeHtml(chartImageUrl)}?t=${Date.now()}" alt="${escapeHtml(idea.title || "chart")}" />
             </div>`
          : `<div class="idea-chart-missing">Снапшот графика недоступен (${escapeHtml(idea.chartSnapshotStatus || idea.chart_snapshot_status || "no_data")}).</div>`
      }

      <div class="idea-analysis-grid">
        <section class="idea-section"><h4>Фундаментал</h4><p>${escapeHtml(analysis.fundamental_ru || "")}</p></section>
        <section class="idea-section"><h4>SMC / ICT</h4><p>${escapeHtml(analysis.smc_ict_ru || "")}</p></section>
        <section class="idea-section"><h4>Паттерн</h4><p>${escapeHtml(analysis.pattern_ru || "")}</p></section>
        <section class="idea-section"><h4>Волны</h4><p>${escapeHtml(analysis.waves_ru || "")}</p></section>
        <section class="idea-section"><h4>Объёмы</h4><p>${escapeHtml(analysis.volume_ru || "")}</p></section>
        <section class="idea-section"><h4>Ликвидность</h4><p>${escapeHtml(analysis.liquidity_ru || "")}</p></section>
      </div>

      <section class="idea-section idea-section-plan">
        <h4>Торговый сценарий</h4>
        <ul class="trade-plan-list">
          <li><strong>Bias:</strong> ${escapeHtml(tradePlan.bias || "neutral")}</li>
          <li><strong>Зона работы:</strong> ${escapeHtml(tradePlan.entry_zone || "")}</li>
          <li><strong>Инвалидация:</strong> ${escapeHtml(tradePlan.invalidation || "")}</li>
          <li><strong>Цель 1:</strong> ${escapeHtml(tradePlan.target_1 || "")}</li>
          <li><strong>Цель 2:</strong> ${escapeHtml(tradePlan.target_2 || "")}</li>
          <li><strong>Альтернатива:</strong> ${escapeHtml(tradePlan.alternative_scenario_ru || "")}</li>
        </ul>
      </section>

      <section class="idea-section idea-section-plan">
        <h4>Текущее обоснование</h4>
        <p>${escapeHtml(reasoning)}</p>
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
