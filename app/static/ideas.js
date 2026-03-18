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
  return (
    new Intl.DateTimeFormat("ru-RU", {
      dateStyle: "short",
      timeStyle: "short",
      timeZone: "UTC",
    }).format(date) + " UTC"
  );
}

async function getJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} for ${url}`);
  }
  return resp.json();
}

function zoneClass(type) {
  const map = {
    order_block: "zone-ob",
    fvg: "zone-fvg",
    imbalance: "zone-fvg",
    liquidity: "zone-liquidity",
    reaction_zone: "zone-reaction",
    range: "zone-range",
  };
  return map[type] || "zone-generic";
}

function labelClass(label) {
  if (label === "BUY IDEA") return "idea-label-buy";
  if (label === "SELL IDEA") return "idea-label-sell";
  return "idea-label-watch";
}

function biasClass(bias) {
  if (bias === "bullish") return "chart-bullish";
  if (bias === "bearish") return "chart-bearish";
  return "chart-neutral";
}

function createPolyline(path, width, height) {
  const points = (path || []).map((point) => {
    const x = (Number(point.x) / 100) * width;
    const y = (Number(point.y) / 100) * height;
    return `${x},${y}`;
  });
  return points.join(" ");
}

function renderChart(chart, title) {
  const width = 760;
  const height = 460;
  const zones = chart?.zones || [];
  const levels = chart?.levels || [];
  const path = chart?.path || [];
  const bias = chart?.bias || "neutral";

  const zonesHtml = zones
    .map((zone) => {
      const x = (Number(zone.x1) / 100) * width;
      const y = (Number(zone.y1) / 100) * height;
      const rectWidth = ((Number(zone.x2) - Number(zone.x1)) / 100) * width;
      const rectHeight = ((Number(zone.y2) - Number(zone.y1)) / 100) * height;

      return `
        <g class="chart-zone ${zoneClass(zone.type)}">
          <rect
            x="${x}"
            y="${y}"
            width="${Math.max(rectWidth, 12)}"
            height="${Math.max(rectHeight, 12)}"
            rx="12"
            ry="12"
          ></rect>
          <text
            x="${x + 10}"
            y="${y + 22}"
            class="chart-zone-label"
          >${escapeHtml(zone.label || "Zone")}</text>
        </g>
      `;
    })
    .join("");

  const levelsHtml = levels
    .map((level) => {
      const x = (Number(level.x) / 100) * width;
      const y = (Number(level.y) / 100) * height;
      return `
        <g class="chart-level-group">
          <circle class="chart-level-dot" cx="${x}" cy="${y}" r="6"></circle>
          <text x="${x + 12}" y="${y - 10}" class="chart-level-label">${escapeHtml(level.label || "Level")}</text>
        </g>
      `;
    })
    .join("");

  const polyline = createPolyline(path, width, height);
  const start = path[0] || { x: 18, y: 60 };
  const end = path[path.length - 1] || { x: 80, y: 30 };
  const endX = (Number(end.x) / 100) * width;
  const endY = (Number(end.y) / 100) * height;
  const startX = (Number(start.x) / 100) * width;
  const startY = (Number(start.y) / 100) * height;

  return `
    <div class="idea-chart-card ${biasClass(bias)}">
      <div class="idea-chart-header">
        <div class="idea-chart-title">${escapeHtml(title)}</div>
        <div class="idea-chart-bias">${escapeHtml(chart?.pattern_type || "scenario")}</div>
      </div>

      <svg
        class="idea-chart-svg"
        viewBox="0 0 ${width} ${height}"
        role="img"
        aria-label="${escapeHtml(title)}"
      >
        <defs>
          <linearGradient id="chartPathGradient" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stop-color="rgba(99,102,241,0.85)"></stop>
            <stop offset="100%" stop-color="rgba(34,197,94,0.95)"></stop>
          </linearGradient>

          <filter id="chartGlow">
            <feGaussianBlur stdDeviation="4.5" result="coloredBlur"></feGaussianBlur>
            <feMerge>
              <feMergeNode in="coloredBlur"></feMergeNode>
              <feMergeNode in="SourceGraphic"></feMergeNode>
            </feMerge>
          </filter>

          <marker
            id="arrowHead"
            markerWidth="16"
            markerHeight="16"
            refX="8"
            refY="8"
            orient="auto"
          >
            <path d="M2,2 L14,8 L2,14 Z" fill="currentColor"></path>
          </marker>
        </defs>

        <g class="chart-grid">
          ${Array.from({ length: 10 })
            .map((_, i) => {
              const x = (i / 10) * width;
              return `<line x1="${x}" y1="0" x2="${x}" y2="${height}" />`;
            })
            .join("")}
          ${Array.from({ length: 8 })
            .map((_, i) => {
              const y = (i / 8) * height;
              return `<line x1="0" y1="${y}" x2="${width}" y2="${y}" />`;
            })
            .join("")}
        </g>

        ${zonesHtml}

        <g class="chart-path-group">
          <polyline
            class="chart-path chart-path-shadow"
            points="${polyline}"
            fill="none"
            stroke-width="10"
            filter="url(#chartGlow)"
          ></polyline>

          <polyline
            class="chart-path chart-path-main"
            points="${polyline}"
            fill="none"
            stroke-width="4"
          ></polyline>

          <circle class="chart-path-start" cx="${startX}" cy="${startY}" r="7"></circle>
          <circle class="chart-path-end" cx="${endX}" cy="${endY}" r="8"></circle>
        </g>

        ${levelsHtml}
      </svg>

      <div class="idea-chart-footer">
        На графике показан основной сценарий движения, ключевые зоны и логика
        идеи: структура, реакция от зоны и путь цены к ликвидности.
      </div>
    </div>
  `;
}

function renderIdeaCard(idea) {
  const analysis = idea.analysis || {};
  const tradePlan = idea.trade_plan || {};
  const chart = idea.chart || {};

  return `
    <article class="idea-card">
      <div class="idea-card-top">
        <div class="idea-card-meta">
          <div class="idea-instrument">${escapeHtml(idea.instrument || "MARKET")}</div>
          <h3 class="idea-title">${escapeHtml(idea.title || "AI-идея")}</h3>
          <div class="idea-news-line">
            Основание: ${escapeHtml(idea.news_title || "Рыночная новость")}
          </div>
        </div>
        <div class="idea-label ${labelClass(idea.label)}">${escapeHtml(idea.label || "WATCH")}</div>
      </div>

      <div class="idea-summary">
        ${escapeHtml(idea.summary_ru || "")}
      </div>

      <div class="idea-main-grid">
        <div class="idea-analysis-grid">
          <section class="idea-section">
            <h4>Фундаментал</h4>
            <p>${escapeHtml(analysis.fundamental_ru || "")}</p>
          </section>

          <section class="idea-section">
            <h4>SMC / ICT</h4>
            <p>${escapeHtml(analysis.smc_ict_ru || "")}</p>
          </section>

          <section class="idea-section">
            <h4>Паттерн</h4>
            <p>${escapeHtml(analysis.pattern_ru || "")}</p>
          </section>

          <section class="idea-section">
            <h4>Волны</h4>
            <p>${escapeHtml(analysis.waves_ru || "")}</p>
          </section>

          <section class="idea-section">
            <h4>Объёмы</h4>
            <p>${escapeHtml(analysis.volume_ru || "")}</p>
          </section>

          <section class="idea-section">
            <h4>Ликвидность</h4>
            <p>${escapeHtml(analysis.liquidity_ru || "")}</p>
          </section>

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
        </div>

        <div class="idea-chart-column">
          ${renderChart(chart, idea.instrument || "MARKET")}
        </div>
      </div>
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
  if (!ideasContainer) return;

  try {
    const payload = await getJson("/ideas/market");
    renderIdeas(payload);
  } catch (error) {
    ideasContainer.innerHTML = `<div class="ideas-loading">Не удалось загрузить идеи.</div>`;
    if (ideasUpdatedAt) {
      ideasUpdatedAt.textContent = "Обновление: ошибка загрузки";
    }
  }
}

loadIdeas();
setInterval(loadIdeas, 60000);
