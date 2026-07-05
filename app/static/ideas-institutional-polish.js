(function () {
  "use strict";

  function safeEscape(value) {
    return typeof escapeHtml === "function" ? escapeHtml(value) : String(value ?? "");
  }

  function safeFormat(value) {
    return typeof formatNumber === "function" ? formatNumber(value) : String(value ?? "—");
  }

  function safeList(value) {
    return typeof formatListValue === "function" ? formatListValue(value) : (Array.isArray(value) ? value.join(", ") : String(value ?? "—"));
  }

  function dash(...values) {
    if (typeof valueOrDash === "function") return valueOrDash(...values);
    for (const value of values) {
      if (value !== undefined && value !== null && value !== "") return value;
    }
    return "—";
  }

  function asText(value) {
    return String(value ?? "").trim();
  }

  function asLower(value) {
    return asText(value).toLowerCase();
  }

  function moneyWallLabel(price, size) {
    const p = safeFormat(price);
    const s = safeFormat(size);
    return `${p} (${s})`;
  }

  function minutesLabel(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    const abs = Math.abs(n);
    if (abs >= 60) return `${(n / 60).toFixed(1)} ч`;
    return `${n.toFixed(0)} мин`;
  }

  function optionsBiasLabel(idea) {
    const bias = typeof resolveExternalOptionsBias === "function" ? resolveExternalOptionsBias(idea) : dash(idea.options_bias, idea.prop_bias, "neutral");
    const normalized = asLower(bias);
    if (normalized.includes("bull")) return { text: "BIAS BULLISH", tone: "good", icon: "🟢" };
    if (normalized.includes("bear")) return { text: "BIAS BEARISH", tone: "bad", icon: "🔴" };
    if (normalized.includes("conflict")) return { text: "OPTIONS CONFLICT", tone: "bad", icon: "⚠️" };
    return { text: "BIAS NEUTRAL", tone: "neutral", icon: "🧩" };
  }

  function pinningTone(value) {
    const v = asLower(value);
    if (v.includes("high") || v.includes("выс")) return "warn";
    if (v.includes("low") || v.includes("низ")) return "good";
    return "neutral";
  }

  function heatmapTone(idea) {
    const bias = asLower(idea.heatmap_bias || idea.heatmapBias);
    if (bias.includes("bull") || bias.includes("support")) return "good";
    if (bias.includes("bear") || bias.includes("resist") || bias.includes("rejection")) return "bad";
    return "neutral";
  }

  function renderChip(label, tone = "neutral") {
    return `<span class="mini-chip mini-chip--${safeEscape(tone)}">${safeEscape(label)}</span>`;
  }

  function renderMetric(label, value, note = "") {
    return `<div class="pro-metric"><span>${safeEscape(label)}</span><strong>${safeEscape(value)}</strong>${note ? `<em>${safeEscape(note)}</em>` : ""}</div>`;
  }

  function getOptionsDetails(idea) {
    return {
      source: typeof resolveOptionsSourceLabel === "function" ? resolveOptionsSourceLabel(idea) : dash(idea.options_source, "MT4_OptionsFX"),
      bias: typeof resolveExternalOptionsBias === "function" ? resolveExternalOptionsBias(idea) : dash(idea.options_bias, idea.prop_bias, "neutral"),
      summary: typeof resolveExternalOptionsRu === "function" ? resolveExternalOptionsRu(idea) : dash(idea.options_summary_ru, "—"),
      strikes: typeof resolveOptionsKeyStrikes === "function" ? resolveOptionsKeyStrikes(idea) : dash(idea.key_strikes, idea.keyLevels),
      maxPain: typeof resolveOptionsMaxPain === "function" ? resolveOptionsMaxPain(idea) : dash(idea.max_pain, idea.maxPain),
      callWalls: dash(idea.call_walls, idea.callWalls, idea.options_call_walls, idea.options?.call_walls),
      putWalls: dash(idea.put_walls, idea.putWalls, idea.options_put_walls, idea.options?.put_walls),
      pinning: dash(idea.pinning_risk, idea.pinningRisk, idea.options_pinning_risk, idea.options?.pinning_risk),
      range: dash(idea.range_risk, idea.rangeRisk, idea.options_range_risk, idea.options?.range_risk),
      score: dash(idea.options_score, idea.prop_options_score, idea.external_options_score, idea.options?.score, idea.prop_score_options),
    };
  }

  function renderOptionsLayer(idea, compact = false) {
    const opt = getOptionsDetails(idea);
    const bias = optionsBiasLabel(idea);
    const pinTone = pinningTone(opt.pinning);
    const headline = `${bias.icon} ${bias.text}`;
    const chips = [
      renderChip(headline, bias.tone),
      opt.pinning !== "—" ? renderChip(`PINNING ${opt.pinning}`, pinTone) : "",
      opt.range !== "—" ? renderChip(`RANGE ${opt.range}`, "neutral") : "",
      opt.source !== "—" ? renderChip(opt.source, "neutral") : "",
    ].filter(Boolean).join("");
    return `<section class="institutional-section pro-layer pro-layer--options">
      <div class="pro-layer-head"><h4>🧩 Options Layer</h4><div class="mini-chip-row">${chips}</div></div>
      <div class="pro-layer-grid ${compact ? "compact" : ""}">
        ${renderMetric("Bias", opt.bias)}
        ${renderMetric("Score", opt.score)}
        ${renderMetric("Max Pain", safeList(opt.maxPain))}
        ${renderMetric("Call / Put", `${safeList(opt.callWalls)} / ${safeList(opt.putWalls)}`)}
      </div>
      <p class="pro-layer-note">Strikes: ${safeEscape(safeList(opt.strikes))}</p>
    </section>`;
  }

  function renderHeatmapLayer(idea) {
    const available = Boolean(idea.heatmap_available || idea.heatmapAvailable);
    const bias = dash(idea.heatmap_bias, idea.heatmapBias, available ? "active" : "нет данных");
    const tone = heatmapTone(idea);
    const above = moneyWallLabel(dash(idea.heatmap_wall_above, idea.heatmapWallAbove), dash(idea.heatmap_wall_above_size, idea.heatmapWallAboveSize));
    const below = moneyWallLabel(dash(idea.heatmap_wall_below, idea.heatmapWallBelow), dash(idea.heatmap_wall_below_size, idea.heatmapWallBelowSize));
    const score = dash(idea.heatmap_score, idea.orderflow_score, idea.dom_score);
    return `<section class="institutional-section pro-layer pro-layer--heatmap ${available ? "is-live" : "is-missing"}">
      <div class="pro-layer-head"><h4>🔥 DOM / Heatmap</h4><div class="mini-chip-row">${renderChip(available ? "LIVE LMV" : "NO DATA", available ? "good" : "neutral")}${renderChip(bias, tone)}</div></div>
      <div class="pro-layer-grid compact">
        ${renderMetric("Wall above", above)}
        ${renderMetric("Wall below", below)}
        ${renderMetric("Bias", bias)}
        ${renderMetric("Score", score)}
      </div>
    </section>`;
  }

  function renderOrderflowLayer(idea) {
    const vd = typeof resolveVolumeDelta === "function" ? resolveVolumeDelta(idea) : {};
    const cvd = dash(idea.cvd_divergence, vd.divergence, "false");
    const absorption = dash(idea.absorption, idea.absorption_signal, idea.orderflow?.absorption);
    const domBias = dash(idea.dom_bias, idea.orderflow?.dom_bias);
    const reason = dash(idea.orderflow_reason_ru, idea.heatmap_reason_ru, idea.dom_reason_ru, "—");
    const src = typeof normalizeOrderflowSource === "function" ? normalizeOrderflowSource(idea) : { icon: "⚪", label: "Unknown Source", compact: "Unknown", descriptor: "Unknown Source", qualityText: "Quality —", reason: "—", ageText: "—" };
    return `<section class="institutional-section pro-layer pro-layer--orderflow">
      <div class="pro-layer-head"><h4>⚡ Orderflow</h4><div class="mini-chip-row">${renderChip(`${src.icon} ${src.compact}`, src.kind === "unavailable" ? "bad" : src.kind === "cache" ? "warn" : "good")}${renderChip(`DOM ${domBias}`, asLower(domBias).includes("bear") ? "bad" : asLower(domBias).includes("bull") ? "good" : "neutral")}${renderChip(`CVD ${cvd}`, String(cvd) === "false" ? "neutral" : "warn")}</div></div>
      <div class="pro-layer-grid compact">
        ${renderMetric("Source", `${src.label} · ${src.descriptor}`)}
        ${renderMetric("Quality", src.qualityText)}
        ${renderMetric("Reason", src.reason)}
        ${renderMetric("Age", src.ageText)}
        ${renderMetric("Delta", dash(idea.delta, vd.delta))}
        ${renderMetric("CumDelta", dash(idea.cum_delta, idea.cumulative_delta, vd.cumdelta))}
        ${renderMetric("Absorption", absorption)}
        ${renderMetric("CVD div", cvd)}
      </div>
      <p class="pro-layer-note">${safeEscape(reason)}</p>
    </section>`;
  }

  function renderNewsLayer(idea) {
    const event = dash(idea.news_event, typeof resolveNewsContext === "function" ? resolveNewsContext(idea) : "—");
    const impact = dash(idea.news_impact, idea.impact, idea.fundamental_risk, "—");
    const minutes = dash(idea.minutes_to_event, idea.news_minutes_to_event);
    const lock = Boolean(idea.news_lock_active);
    const tone = lock ? "bad" : asLower(impact).includes("high") ? "warn" : "good";
    return `<section class="institutional-section pro-layer pro-layer--news ${lock ? "is-blocked" : ""}">
      <div class="pro-layer-head"><h4>${lock ? "🚨" : "📰"} News / Fundamental</h4><div class="mini-chip-row">${renderChip(lock ? "TRADE BLOCKED" : `IMPACT ${impact}`, tone)}</div></div>
      <p class="pro-layer-title">${safeEscape(event)}</p>
      <p class="pro-layer-note">До события: ${safeEscape(minutesLabel(minutes))} · Risk: ${safeEscape(dash(idea.news_risk, idea.fundamental_risk))}</p>
    </section>`;
  }

  function renderRiskLayer(idea) {
    const finalScore = dash(idea.final_score, idea.score, idea.prop_score);
    const execScore = dash(idea.execution_score, idea.executionScore);
    const risk = dash(idea.risk_per_trade_pct, idea.recommended_risk_percent, "0.25");
    const regime = dash(idea.market_regime, idea.regime, "—");
    return `<section class="institutional-section pro-layer pro-layer--risk">
      <div class="pro-layer-head"><h4>🛡️ Risk / Execution</h4><div class="mini-chip-row">${renderChip(`FINAL ${finalScore}`, Number(finalScore) >= 88 ? "good" : Number(finalScore) >= 70 ? "warn" : "bad")}</div></div>
      <div class="pro-layer-grid compact">
        ${renderMetric("Execution", execScore)}
        ${renderMetric("Final", finalScore)}
        ${renderMetric("Risk", `${risk}%`)}
        ${renderMetric("Regime", regime)}
      </div>
    </section>`;
  }

  function renderWyckoffLayer(idea) {
    const signal = dash(idea.wyckoff_signal, idea.wyckoff?.signal, idea.absorption ? "absorption context" : "—");
    const phase = dash(idea.wyckoff_phase, idea.wyckoff?.phase, idea.market_regime, "—");
    const score = dash(idea.wyckoff_score, idea.absorption_score, "—");
    return `<section class="institutional-section pro-layer pro-layer--wyckoff">
      <div class="pro-layer-head"><h4>📦 Wyckoff / Absorption</h4><div class="mini-chip-row">${renderChip(signal, String(signal) === "—" ? "neutral" : "good")}</div></div>
      <div class="pro-layer-grid compact">
        ${renderMetric("Phase", phase)}
        ${renderMetric("Signal", signal)}
        ${renderMetric("Score", score)}
        ${renderMetric("Absorption", dash(idea.absorption, "—"))}
      </div>
    </section>`;
  }

  window.renderInstitutionalSections = function renderInstitutionalSectionsPolished(idea) {
    const market = `<section class="institutional-section pro-layer pro-layer--structure">
      <div class="pro-layer-head"><h4>🏛️ Market Structure</h4><div class="mini-chip-row">${renderChip(`HTF ${dash(idea.htf_bias, idea.market_structure?.trend_regime)}`, "neutral")}</div></div>
      <div class="pro-layer-grid compact">
        ${renderMetric("BOS", dash(idea.market_structure?.bos, idea.bos))}
        ${renderMetric("Sweep", dash(idea.liquidity?.sweep, idea.sweep))}
        ${renderMetric("FVG", dash(idea.fvg?.type, idea.selected_zone_type))}
        ${renderMetric("DPOC", dash(idea.dpoc_price, idea.market_structure?.dpoc_price))}
      </div>
    </section>`;
    return `<div class="institutional-sections pro-sections">
      ${market}
      ${renderOrderflowLayer(idea)}
      ${renderHeatmapLayer(idea)}
      ${renderOptionsLayer(idea, true)}
      ${renderNewsLayer(idea)}
      ${renderWyckoffLayer(idea)}
      ${renderRiskLayer(idea)}
    </div>`;
  };

  window.renderExecutionAnalysis = function renderExecutionAnalysisPolished(idea) {
    const rows = [
      ["Killzone", `${dash(idea.killzone_status)} (${dash(idea.killzone_bonus)})`, dash(idea.killzone_reason_ru)],
      ["ATR", `${safeFormat(idea.atr_pips)} пипс`, idea.atr_filter_passed === false ? "Ниже prop-порога" : "Фильтр пройден"],
      ["RVOL", safeFormat(idea.rvol), dash(idea.rvol_status)],
      ["VWAP", safeFormat(idea.vwap), dash(idea.vwap_alignment)],
      ["News Lock", idea.news_lock_active ? "ACTIVE" : "OFF", dash(idea.news_event, idea.news_minutes_to_event)],
      ["Correlation", idea.correlation_block ? "BLOCK" : "OK", `USD exposure: ${dash(idea.usd_exposure_count)}`],
      ["DOM Heatmap", dash(idea.heatmap_bias), `${dash(idea.heatmap_wall_above_size)} / ${dash(idea.heatmap_wall_below_size)}`],
      ["Wyckoff", dash(idea.wyckoff_signal), `Score: ${dash(idea.wyckoff_score)}`],
      ["Options", dash(typeof resolveExternalOptionsBias === "function" ? resolveExternalOptionsBias(idea) : idea.options_bias), `MaxPain: ${safeList(typeof resolveOptionsMaxPain === "function" ? resolveOptionsMaxPain(idea) : idea.max_pain)}`],
      ["Dynamic Risk", `${dash(idea.risk_per_trade_pct, idea.recommended_risk_percent)}%`, `Lot: ${dash(idea.recommended_lot)}`],
    ];
    return `<section class="modal-section execution-analysis premium-execution" style="margin-top:16px;">
      <h4>Execution Analysis</h4>
      <div class="modal-meta">
        <div><span>Base score</span><strong>${safeEscape(dash(idea.base_score, idea.base_score_before_execution_filters))}</strong></div>
        <div><span>Execution score</span><strong>${safeEscape(dash(idea.execution_score))}</strong></div>
        <div><span>Final score</span><strong>${safeEscape(dash(idea.final_score, idea.score))}</strong></div>
        <div><span>Mode</span><strong>${safeEscape(dash(idea.mode, idea.prop_mode_label))}</strong></div>
      </div>
      <div class="criteria-grid premium-criteria">${rows.map(([label, value, note]) => `<div class="criterion"><strong>${safeEscape(label)}</strong><br>${safeEscape(value)} · ${safeEscape(note)}</div>`).join("")}</div>
    </section>`;
  };

  const originalRenderStatusPills = window.renderStatusPills;
  window.renderStatusPills = function renderStatusPillsPolished(idea) {
    const base = typeof originalRenderStatusPills === "function" ? originalRenderStatusPills(idea) : "<div class='status-pill-row'></div>";
    const extras = [];
    if (idea.news_lock_active) extras.push(["🚨 TRADE BLOCKED BY NEWS", "danger"]);
    if (idea.heatmap_available) extras.push([`🔥 HEATMAP ${dash(idea.heatmap_bias)}`, heatmapTone(idea) === "bad" ? "danger" : "hot"]);
    if (idea.wyckoff_signal) extras.push([`📦 WYCKOFF ${idea.wyckoff_signal}`, "hot"]);
    if (idea.absorption) extras.push([`🧲 ABSORPTION ${idea.absorption}`, "warn"]);
    if (!extras.length) return base;
    return base.replace("</div>", extras.map(([label, cls]) => `<span class="status-pill ${cls}">${safeEscape(label)}</span>`).join("") + "</div>");
  };

  function injectPolishStyles() {
    if (document.getElementById("ideas-institutional-polish-styles")) return;
    const style = document.createElement("style");
    style.id = "ideas-institutional-polish-styles";
    style.textContent = `
      .pro-sections { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
      .pro-layer { position: relative; overflow: hidden; border-color: rgba(124,184,255,.26); background: linear-gradient(145deg, rgba(7, 22, 42, .86), rgba(3, 14, 28, .68)); box-shadow: inset 0 1px 0 rgba(255,255,255,.06); }
      .pro-layer::before { content: ""; position: absolute; inset: 0; pointer-events: none; background: radial-gradient(circle at 90% 0%, rgba(69,202,255,.12), transparent 35%); }
      .pro-layer > * { position: relative; z-index: 1; }
      .pro-layer-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 9px; }
      .pro-layer-head h4 { margin: 0; }
      .mini-chip-row { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 5px; }
      .mini-chip { display: inline-flex; align-items: center; gap: 4px; padding: 4px 7px; border-radius: 999px; border: 1px solid rgba(148,163,184,.24); background: rgba(2,6,23,.38); color: #dbeeff; font-size: 9px; font-weight: 950; letter-spacing: .04em; text-transform: uppercase; white-space: nowrap; }
      .mini-chip--good { border-color: rgba(52,211,153,.46); color: #9fffd0; background: rgba(6,78,59,.18); }
      .mini-chip--bad { border-color: rgba(248,113,113,.48); color: #fecdd3; background: rgba(127,29,29,.2); }
      .mini-chip--warn { border-color: rgba(250,204,21,.44); color: #fde68a; background: rgba(113,63,18,.2); }
      .mini-chip--neutral { border-color: rgba(96,165,250,.28); color: #bfdbfe; }
      .pro-layer-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
      .pro-layer-grid.compact { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .pro-metric { padding: 8px; border-radius: 11px; background: rgba(2, 6, 23, .42); border: 1px solid rgba(95,156,230,.18); min-width: 0; }
      .pro-metric span { display:block; color:#9bb8d8; font-size:9px; font-weight:950; text-transform:uppercase; letter-spacing:.06em; margin-bottom:2px; }
      .pro-metric strong { display:block; color:#f4f8ff; font-size:12px; overflow-wrap:anywhere; }
      .pro-metric em { display:block; color:#b9d6f8; font-size:10px; font-style:normal; margin-top:2px; }
      .pro-layer-note, .pro-layer-title { margin: 8px 0 0; color: #dbeeff; font-size: 12px; line-height: 1.45; overflow-wrap: anywhere; }
      .pro-layer-title { color:#f4f8ff; font-weight:900; }
      .pro-layer--options { border-color: rgba(167,139,250,.32); }
      .pro-layer--heatmap.is-live { border-color: rgba(251,146,60,.42); box-shadow: inset 0 1px 0 rgba(255,255,255,.06), 0 0 26px rgba(251,146,60,.08); }
      .pro-layer--news.is-blocked { border-color: rgba(248,113,113,.6); background: linear-gradient(145deg, rgba(127,29,29,.3), rgba(3,14,28,.72)); }
      .premium-execution { border-color: rgba(84,255,181,.24); }
      .premium-criteria { margin-top: 10px; }
      @media (max-width: 920px) { .pro-sections { grid-template-columns: 1fr; } }
    `;
    document.head.appendChild(style);
  }

  injectPolishStyles();
  document.addEventListener("DOMContentLoaded", injectPolishStyles);
})();
