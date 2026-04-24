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

function normalizeChartImageUrl(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw) || raw.startsWith("/")) return raw;
  if (raw.startsWith("static/")) return `/${raw}`;
  if (raw.startsWith("./")) return `/${raw.slice(2)}`;
  return `/static/${raw.replace(/^\/+/, "")}`;
}

function renderIdeaCard(idea) {
  const chartImageUrl = normalizeChartImageUrl(idea.chartImageUrl || idea.chart_image || "");
  console.log("chart_image:", chartImageUrl || null);
  console.log("snapshot_status:", idea.chartSnapshotStatus || idea.chart_snapshot_status || "");
  const tradePlan = idea.trade_plan || {};
  const updates = Array.isArray(idea.updates) ? idea.updates.slice(-5).reverse() : [];
  const reasoning = resolveVisibleNarrative(idea);
  const compactSummary = String(idea?.compact_summary || "").trim();
  const analysisMode = String(idea.analysis_mode || "").toLowerCase() === "smc_pro" ? "SMC PRO" : "Упрощённый";
  const providerLabel = (function(){ const p=String(idea.data_provider || "").toLowerCase(); if (p === "finnhub") return "Finnhub"; if (p === "twelvedata") return "TwelveData"; return "Yahoo"; })();
  const warningText = String(idea.warning || "").trim();

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
      <div class="idea-news-line">Режим: <strong>${escapeHtml(analysisMode)}</strong></div>
      <div class="idea-news-line">Источник: <strong>${escapeHtml(providerLabel)}</strong></div>
      ${warningText ? `<div class="idea-warning">${escapeHtml(warningText)}</div>` : ""}
      <div class="idea-news-line">Источник описания: <strong>${escapeHtml(idea.narrative_source || "резервный_шаблон")}</strong></div>

      ${renderChartBlock(idea, chartImageUrl)}

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
  const sanitize = (value) => String(value || "").replace(/\(\s*none\s*\)/gi, "").replace(/\bnone\b/gi, "").trim();
  const thesis = sanitize(idea?.idea_thesis);
  if (thesis) return thesis;
  const unified = sanitize(idea?.unified_narrative);
  if (unified) return unified;
  const fullText = sanitize(idea?.full_text);
  if (fullText) return fullText;
  const fallbackNarrative = sanitize(idea?.fallback_narrative);
  if (fallbackNarrative) return fallbackNarrative;
  const summary = sanitize(idea?.summary || idea?.short_text);
  if (summary) return summary;
  return "Сценарий в режиме fallback: модельный нарратив временно недоступен.";
}

function renderChartBlock(idea, chartImageUrl) {
  if (chartImageUrl) {
    return `<div class="idea-chart-wrap">
      <img class="idea-chart-image" src="${escapeHtml(chartImageUrl)}?t=${Date.now()}" alt="${escapeHtml(idea.title || "chart")}" />
    </div>`;
  }
  const fallbackCandles = idea?.chartData?.candles || idea?.chart_data?.candles || [];
  const fallbackSvg = buildFallbackSvg(fallbackCandles);
  if (fallbackSvg) {
    return `<div class="idea-chart-wrap">${fallbackSvg}</div>`;
  }
  return `<div class="idea-chart-missing">Снапшот графика недоступен (${escapeHtml(idea.chartSnapshotStatus || idea.chart_snapshot_status || "no_data")}).</div>`;
}

function buildFallbackSvg(candles) {
  if (!Array.isArray(candles) || candles.length < 2) return "";
  const closes = candles.map((c) => Number(c?.close)).filter((v) => Number.isFinite(v));
  if (closes.length < 2) return "";
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const width = 400;
  const height = 180;
  const step = width / Math.max(closes.length - 1, 1);
  const points = closes
    .map((value, index) => {
      const x = index * step;
      const ratio = max === min ? 0.5 : (value - min) / (max - min);
      const y = height - ratio * (height - 20) - 10;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  return `<svg class="idea-chart-image" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="fallback candles chart">
    <rect x="0" y="0" width="${width}" height="${height}" fill="#0b1220"></rect>
    <polyline fill="none" stroke="#22d3ee" stroke-width="2" points="${points}"></polyline>
  </svg>`;
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
