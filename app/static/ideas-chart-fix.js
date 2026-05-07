(function () {
  function number(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function firstNumber() {
    for (let i = 0; i < arguments.length; i++) {
      const n = number(arguments[i]);
      if (n !== null) return n;
    }
    return null;
  }

  function arr(value) {
    if (Array.isArray(value)) return value;
    if (value && typeof value === "object") return [value];
    return [];
  }

  function candlesOf(idea) {
    const sources = [
      idea && idea.candles,
      idea && idea.chartData && idea.chartData.candles,
      idea && idea.chart_data && idea.chart_data.candles,
      idea && idea.chart && idea.chart.candles,
      idea && idea.market_data && idea.market_data.candles,
      idea && idea.market_context && idea.market_context.candles,
      idea && idea.history,
      idea && idea.ohlc,
    ];
    for (const src of sources) {
      if (Array.isArray(src) && src.length >= 2) return src;
    }
    return [];
  }

  function candle(c, i) {
    const t = c.time || c.timestamp || c.t || Math.floor(Date.now() / 1000) - (200 - i) * 900;
    return {
      time: typeof t === "number" ? t : Math.floor(new Date(t).getTime() / 1000),
      open: Number(c.open ?? c.o ?? c.close ?? c.c),
      high: Number(c.high ?? c.h ?? c.close ?? c.c),
      low: Number(c.low ?? c.l ?? c.close ?? c.c),
      close: Number(c.close ?? c.c),
    };
  }

  function getDirection(idea) {
    const raw = String((idea && (idea.signal || idea.action || idea.label || idea.direction)) || "WAIT").toUpperCase();
    if (raw.includes("BUY") || raw.includes("ПОКУП")) return "BUY";
    if (raw.includes("SELL") || raw.includes("ПРОДА")) return "SELL";
    return "WAIT";
  }

  function addLine(series, price, title, color, style, width) {
    const p = number(price);
    if (p === null || !series || !series.createPriceLine) return false;
    try {
      series.createPriceLine({
        price: p,
        color: color || "#8fb9ff",
        lineWidth: width || 1,
        lineStyle: style === "solid" ? 0 : style === "dashed" ? 2 : 1,
        axisLabelVisible: true,
        title: title,
      });
      return true;
    } catch (e) {
      console.warn("chart_line_failed", title, e);
      return false;
    }
  }

  function addRange(series, low, high, label, color) {
    const lo = number(low);
    const hi = number(high);
    if (lo === null && hi === null) return 0;
    if (lo !== null && hi !== null && Math.abs(lo - hi) > 0) {
      const a = Math.min(lo, hi);
      const b = Math.max(lo, hi);
      let count = 0;
      if (addLine(series, b, label + " HIGH", color, "dashed", 1)) count++;
      if (addLine(series, a, label + " LOW", color, "dashed", 1)) count++;
      return count;
    }
    return addLine(series, lo !== null ? lo : hi, label, color, "dashed", 1) ? 1 : 0;
  }

  function collectRanges(value) {
    return arr(value).map((item) => {
      if (typeof item === "number" || typeof item === "string") {
        const p = number(item);
        return p === null ? null : { low: p, high: p };
      }
      const low = firstNumber(item.low, item.bottom, item.min, item.from_price, item.from, item.price_low, item.zone_low, item.low_price, item.y1);
      const high = firstNumber(item.high, item.top, item.max, item.to_price, item.to, item.price_high, item.zone_high, item.high_price, item.y2);
      const price = firstNumber(item.price, item.level, item.value, item.strike);
      if (low === null && high === null && price !== null) return { low: price, high: price };
      if (low !== null && high === null) return { low, high: low };
      if (high !== null && low === null) return { low: high, high };
      if (low !== null && high !== null) return { low, high };
      return null;
    }).filter(Boolean);
  }

  function collectFlatNumbers(value) {
    if (!value) return [];
    if (Array.isArray(value)) return value.flatMap(collectFlatNumbers);
    if (typeof value === "number" || typeof value === "string") {
      const n = number(value);
      return n === null ? [] : [n];
    }
    if (typeof value === "object") {
      const direct = firstNumber(value.price, value.level, value.value, value.strike);
      if (direct !== null) return [direct];
      return collectRanges(value).flatMap((r) => r.low === r.high ? [r.low] : [r.low, r.high]);
    }
    return [];
  }

  function limitUnique(values, max) {
    const out = [];
    for (const v of values) {
      const n = number(v);
      if (n === null) continue;
      const key = n.toFixed(5);
      if (!out.some((x) => x.toFixed(5) === key)) out.push(n);
      if (out.length >= max) break;
    }
    return out;
  }

  function avgRange(data) {
    const recent = data.slice(-40);
    if (!recent.length) return 0;
    return recent.reduce((sum, c) => sum + Math.max(0, c.high - c.low), 0) / recent.length;
  }

  function detectFallbackLiquidity(data) {
    const recent = data.slice(-80);
    if (recent.length < 12) return [];
    const levels = [];
    for (let i = 2; i < recent.length - 2; i++) {
      const c = recent[i];
      const isHigh = c.high >= recent[i - 1].high && c.high >= recent[i - 2].high && c.high >= recent[i + 1].high && c.high >= recent[i + 2].high;
      const isLow = c.low <= recent[i - 1].low && c.low <= recent[i - 2].low && c.low <= recent[i + 1].low && c.low <= recent[i + 2].low;
      if (isHigh) levels.push(c.high);
      if (isLow) levels.push(c.low);
    }
    const current = recent[recent.length - 1].close;
    return limitUnique(levels.sort((a, b) => Math.abs(a - current) - Math.abs(b - current)), 6);
  }

  function detectFallbackFvg(data) {
    const recent = data.slice(-70);
    if (recent.length < 6) return [];
    const gapMin = avgRange(recent) * 0.22;
    const zones = [];
    for (let i = 2; i < recent.length; i++) {
      const a = recent[i - 2];
      const c = recent[i];
      if (c.low > a.high && c.low - a.high >= gapMin) zones.push({ low: a.high, high: c.low });
      if (c.high < a.low && a.low - c.high >= gapMin) zones.push({ low: c.high, high: a.low });
    }
    return zones.slice(-5);
  }

  function detectFallbackOb(data, direction) {
    const recent = data.slice(-70);
    if (recent.length < 8) return [];
    const ar = avgRange(recent);
    const zones = [];
    for (let i = 1; i < recent.length - 2; i++) {
      const c = recent[i];
      const next = recent[i + 1];
      const body = Math.abs(next.close - next.open);
      const impulse = body > ar * 1.15;
      if (!impulse) continue;
      const bullishImpulse = next.close > next.open;
      const bearishImpulse = next.close < next.open;
      if ((direction === "BUY" || direction === "WAIT") && c.close < c.open && bullishImpulse) zones.push({ low: c.low, high: c.high });
      if ((direction === "SELL" || direction === "WAIT") && c.close > c.open && bearishImpulse) zones.push({ low: c.low, high: c.high });
    }
    return zones.slice(-4);
  }

  function dedupeRanges(ranges, minDistance) {
    const out = [];
    for (const range of ranges) {
      if (!range) continue;
      const lo = number(range.low);
      const hi = number(range.high);
      if (lo === null && hi === null) continue;
      const low = Math.min(lo ?? hi, hi ?? lo);
      const high = Math.max(lo ?? hi, hi ?? lo);
      const mid = (low + high) / 2;
      if (out.some((r) => Math.abs(((r.low + r.high) / 2) - mid) <= minDistance)) continue;
      out.push({ low, high });
    }
    return out;
  }

  function resolveTimeRange(item, data) {
    const fallbackStart = data[0] && data[0].time;
    const fallbackEnd = data[data.length - 1] && data[data.length - 1].time;
    const parse = (v) => (typeof v === "number" ? v : v ? Math.floor(new Date(v).getTime() / 1000) : null);
    const from = parse(item && (item.from_time ?? item.start_time ?? item.time_from ?? item.t1 ?? item.start ?? item.from)) ?? fallbackStart;
    const to = parse(item && (item.to_time ?? item.end_time ?? item.time_to ?? item.t2 ?? item.end ?? item.to)) ?? fallbackEnd;
    return { from: Math.min(from, to), to: Math.max(from, to) };
  }

  function buildZoneRects(rawList, data, color, labelPrefix, minDistance) {
    const zones = [];
    arr(rawList).forEach((item) => {
      collectRanges(item).forEach((r) => zones.push({ ...r, ...resolveTimeRange(item, data) }));
    });
    return dedupeRanges(zones, minDistance).map((zone, i) => ({ ...zone, color, label: i === 0 ? labelPrefix : `${labelPrefix} ${i + 1}` }));
  }

  function renderHtmlZones(chart, host, zones, cls) {
    if (!chart || !host || !zones.length) return null;
    let layer = host.querySelector(".smc-zones-layer");
    if (!layer) {
      layer = document.createElement("div");
      layer.className = "smc-zones-layer";
      host.appendChild(layer);
    }
    const repaint = () => {
      layer.innerHTML = "";
      zones.forEach((zone) => {
        const x1 = chart.timeScale().timeToCoordinate(zone.from);
        const x2 = chart.timeScale().timeToCoordinate(zone.to);
        const y1 = chart.priceScale("right").priceToCoordinate(zone.high);
        const y2 = chart.priceScale("right").priceToCoordinate(zone.low);
        if (![x1, x2, y1, y2].every((v) => Number.isFinite(v))) return;
        const rect = document.createElement("div");
        rect.className = `smc-zone ${cls}`;
        rect.style.left = `${Math.min(x1, x2)}px`;
        rect.style.top = `${Math.min(y1, y2)}px`;
        rect.style.width = `${Math.max(2, Math.abs(x2 - x1))}px`;
        rect.style.height = `${Math.max(2, Math.abs(y2 - y1))}px`;
        rect.style.borderColor = zone.color;
        rect.style.background = zone.color;
        rect.innerHTML = `<span>${zone.label}</span>`;
        layer.appendChild(rect);
      });
    };
    repaint();
    chart.timeScale().subscribeVisibleTimeRangeChange(repaint);
    return repaint;
  }

  function addFallbackSmcOverlays(series, idea, data, explicitCounts) {
    const direction = getDirection(idea);
    if (!explicitCounts.liquidity) {
      detectFallbackLiquidity(data).forEach((p, i) => addLine(series, p, i === 0 ? "LIQ*" : "LIQ* " + (i + 1), "#f97316", "dotted", 1));
    }
    if (!explicitCounts.fvg) {
      detectFallbackFvg(data).forEach((r, i) => addRange(series, r.low, r.high, i === 0 ? "FVG*" : "FVG* " + (i + 1), "#22d3ee"));
    }
    if (!explicitCounts.ob) {
      detectFallbackOb(data, direction).forEach((r, i) => addRange(series, r.low, r.high, i === 0 ? "OB*" : "OB* " + (i + 1), "#a78bfa"));
    }
  }

  function addIdeaOverlays(series, idea, data) {
    addLine(series, idea.entry ?? idea.entry_price, "ENTRY", "#ffd84d", "dashed", 2);
    addLine(series, idea.sl ?? idea.stop_loss, "SL", "#ff5f7a", "dashed", 2);
    addLine(series, idea.tp ?? idea.take_profit ?? idea.target, "TP", "#31f59d", "dashed", 2);

    addRange(series, idea.selected_zone_low, idea.selected_zone_high, "ZONE", "#38bdf8");

    const explicitCounts = { ob: 0, fvg: 0, liquidity: 0 };
    const market = idea.market_context || {};
    const orderBlocks = [idea.order_blocks, idea.orderBlocks, market.order_blocks, market.orderBlocks, idea.ob_zones, idea.poi_zones];
    orderBlocks.flatMap(collectRanges).slice(0, 6).forEach((r, i) => { explicitCounts.ob += addRange(series, r.low, r.high, i === 0 ? "OB" : "OB " + (i + 1), "#a78bfa"); });

    const fvgs = [idea.fvg, idea.fvgs, idea.fair_value_gaps, idea.fairValueGaps, market.fvg, market.fvgs, market.fair_value_gaps, market.imbalances, idea.imbalances];
    fvgs.flatMap(collectRanges).slice(0, 6).forEach((r, i) => { explicitCounts.fvg += addRange(series, r.low, r.high, i === 0 ? "FVG" : "FVG " + (i + 1), "#22d3ee"); });

    const liquidity = [idea.liquidity, idea.liquidity_levels, idea.liquidity_zones, idea.liquidity_sweep, market.liquidity, market.liquidity_levels, market.liquidity_zones, market.equal_highs, market.equal_lows];
    limitUnique(liquidity.flatMap(collectFlatNumbers), 10).forEach((p, i) => { if (addLine(series, p, i === 0 ? "LIQ" : "LIQ " + (i + 1), "#f97316", "dotted", 1)) explicitCounts.liquidity++; });

    const opt = idea.options_analysis || {};
    const optionLevels = [opt.keyLevels, opt.keyStrikes, opt.callWalls, opt.putWalls, opt.maxPain, idea.options_key_levels, idea.options_levels];
    limitUnique(optionLevels.flatMap(collectFlatNumbers), 12).forEach((p, i) => addLine(series, p, i === 0 ? "OPT" : "OPT " + (i + 1), "#facc15", "dotted", 1));

    addFallbackSmcOverlays(series, idea, data || [], explicitCounts);
  }

  function ensureChartUiStyles() {
    if (document.getElementById("ideas-chart-hotfix-ui")) return;
    const style = document.createElement("style");
    style.id = "ideas-chart-hotfix-ui";
    style.textContent = `
      .chart-area { position: relative; }
      .smc-zones-layer { position:absolute; inset:0; pointer-events:none; z-index:12; }
      .smc-zone { position:absolute; border:1px solid; border-radius:6px; box-shadow:inset 0 0 0 1px rgba(255,255,255,.1); }
      .smc-zone span { position:absolute; left:6px; top:2px; font-size:10px; font-weight:850; color:#e6f3ff; text-shadow:0 1px 2px rgba(0,0,0,.85); }
      .smc-zone.ob-bull { opacity:.34; }
      .smc-zone.ob-bear { opacity:.34; }
      .smc-zone.fvg { opacity:.28; }
      .smc-zone.liq { opacity:.22; }
      .chart-area.chart-fullscreen { position: fixed !important; inset: 10px !important; z-index: 2147483000 !important; width: auto !important; height: auto !important; min-height: 0 !important; border-radius: 18px !important; background: #06111f !important; box-shadow: 0 30px 120px rgba(0,0,0,.86) !important; }
      .chart-area.chart-fullscreen #ideaModalChart { height: calc(100vh - 20px) !important; min-height: calc(100vh - 20px) !important; }
      .chart-fs-btn { position:absolute; top:12px; right:12px; z-index:30; border:1px solid rgba(255,255,255,.18); background:rgba(3,14,28,.82); color:#e8f3ff; border-radius:10px; padding:8px 11px; font-size:12px; font-weight:900; cursor:pointer; backdrop-filter: blur(8px); }
      .chart-fs-btn:hover { border-color:rgba(56,189,248,.8); color:#fff; }
      .trade-arrow-overlay { position:absolute; left:18px; top:18px; z-index:25; display:flex; align-items:center; gap:10px; padding:10px 12px; border-radius:14px; font-weight:950; letter-spacing:.02em; box-shadow:0 10px 28px rgba(0,0,0,.35); pointer-events:none; }
      .trade-arrow-overlay.buy { color:#00150c; background:linear-gradient(180deg,#7dffc4,#31f59d); }
      .trade-arrow-overlay.sell { color:#fff; background:linear-gradient(180deg,#ff7f99,#be123c); }
      .trade-arrow-overlay.wait { color:#e8f3ff; background:rgba(71,85,105,.85); }
      .trade-arrow-icon { font-size:30px; line-height:1; }
      .trade-arrow-text { display:flex; flex-direction:column; font-size:12px; line-height:1.15; }
      .trade-arrow-text strong { font-size:16px; }
      .chart-fullscreen .trade-arrow-overlay { left:24px; top:24px; transform:scale(1.05); transform-origin:left top; }
      .chart-fullscreen .chart-fs-btn { top:18px; right:18px; }
    `;
    document.head.appendChild(style);
  }

  function syncChartSizeLater(chart, container) {
    const resize = () => {
      if (!chart || !container) return;
      const rect = container.getBoundingClientRect();
      try {
        chart.applyOptions({ width: Math.max(320, Math.floor(rect.width)), height: Math.max(320, Math.floor(rect.height)) });
        chart.timeScale().fitContent();
      } catch (e) {}
    };
    setTimeout(resize, 30);
    setTimeout(resize, 180);
    window.addEventListener("resize", resize, { passive: true });
    return resize;
  }

  function addChartControls(chart, idea) {
    ensureChartUiStyles();
    const container = document.getElementById("ideaModalChart");
    const area = container && container.closest(".chart-area");
    if (!container || !area) return;

    area.querySelectorAll(".chart-fs-btn,.trade-arrow-overlay").forEach((el) => el.remove());

    const direction = getDirection(idea);
    const arrow = document.createElement("div");
    arrow.className = "trade-arrow-overlay " + (direction === "BUY" ? "buy" : direction === "SELL" ? "sell" : "wait");
    const icon = direction === "BUY" ? "↗" : direction === "SELL" ? "↘" : "→";
    const text = direction === "BUY" ? "BUY / Покупка" : direction === "SELL" ? "SELL / Продажа" : "WAIT / Наблюдение";
    arrow.innerHTML = `<span class="trade-arrow-icon">${icon}</span><span class="trade-arrow-text"><span>Направление</span><strong>${text}</strong></span>`;
    area.appendChild(arrow);

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chart-fs-btn";
    btn.textContent = "⛶ Полный экран";
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      area.classList.toggle("chart-fullscreen");
      btn.textContent = area.classList.contains("chart-fullscreen") ? "× Закрыть экран" : "⛶ Полный экран";
      syncChartSizeLater(chart, container);
    });
    area.appendChild(btn);

    document.addEventListener("keydown", function onEsc(event) {
      if (event.key !== "Escape") return;
      if (!area.classList.contains("chart-fullscreen")) return;
      area.classList.remove("chart-fullscreen");
      btn.textContent = "⛶ Полный экран";
      syncChartSizeLater(chart, container);
    });
  }

  function escapeHtmlLocal(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  window.renderModalChart = function renderModalChartFixed(idea) {
    const container = document.getElementById("ideaModalChart");
    if (!container || !("LightweightCharts" in window)) return;

    const data = candlesOf(idea)
      .map(candle)
      .filter((c) => Number.isFinite(c.open) && Number.isFinite(c.high) && Number.isFinite(c.low) && Number.isFinite(c.close));

    if (data.length < 2) {
      container.innerHTML = '<div class="ideas-loading">График недоступен: API не передал свечи. Уровни идеи показаны в карточке.</div>';
      return;
    }

    if (window.modalChart) {
      try { window.modalChart.remove(); } catch (e) {}
      window.modalChart = null;
    }

    const area = container.closest(".chart-area");
    if (area) area.querySelectorAll(".chart-fs-btn,.trade-arrow-overlay").forEach((el) => el.remove());
    container.innerHTML = "";

    const chart = LightweightCharts.createChart(container, {
      layout: { background: { color: "#06111f" }, textColor: "#dbeeff" },
      grid: { vertLines: { color: "rgba(255,255,255,.055)" }, horzLines: { color: "rgba(255,255,255,.055)" } },
      rightPriceScale: { borderColor: "rgba(95,156,230,.24)", scaleMargins: { top: 0.12, bottom: 0.12 } },
      timeScale: { borderColor: "rgba(95,156,230,.24)", rightOffset: 8, barSpacing: 8 },
      crosshair: { mode: 1 },
    });
    window.modalChart = chart;

    const series = chart.addCandlestickSeries({
      upColor: "#31f59d",
      downColor: "#ff5f7a",
      borderUpColor: "#31f59d",
      borderDownColor: "#ff5f7a",
      wickUpColor: "#31f59d",
      wickDownColor: "#ff5f7a",
    });

    series.setData(data);
    addIdeaOverlays(series, idea || {}, data);
    const market = (idea && idea.market_context) || {};
    const minDistance = Math.max(avgRange(data) * 0.2, 0.00001);
    const zoneHost = container.closest(".chart-area") || container;
    zoneHost.querySelectorAll(".smc-zones-layer").forEach((el) => el.remove());

    const bullishOb = buildZoneRects([idea.order_blocks_bullish, market.order_blocks_bullish, idea.order_blocks], data, "rgba(20,184,166,.55)", "BULLISH OB", minDistance);
    const bearishOb = buildZoneRects([idea.order_blocks_bearish, market.order_blocks_bearish], data, "rgba(239,68,68,.55)", "BEARISH OB", minDistance);
    const fvgZones = buildZoneRects([idea.fvg, idea.fvgs, idea.fair_value_gaps, idea.imbalances, market.fvg, market.imbalances], data, "rgba(34,211,238,.52)", "FVG", minDistance);
    const buyLiq = buildZoneRects([idea.liquidity_zones, market.liquidity_zones], data, "rgba(245,158,11,.45)", "BUY SIDE LIQUIDITY", minDistance);
    const sellLiq = buildZoneRects([idea.sell_side_liquidity, market.sell_side_liquidity], data, "rgba(251,146,60,.45)", "SELL SIDE LIQUIDITY", minDistance);
    const repaintFns = [
      renderHtmlZones(chart, zoneHost, bullishOb, "ob-bull"),
      renderHtmlZones(chart, zoneHost, bearishOb, "ob-bear"),
      renderHtmlZones(chart, zoneHost, fvgZones, "fvg"),
      renderHtmlZones(chart, zoneHost, [...buyLiq, ...sellLiq], "liq"),
    ].filter(Boolean);

    const sweepMarkers = arr(idea.liquidity_sweeps || idea.liquidity_sweep || market.liquidity_sweeps).map((event) => {
      const time = resolveTimeRange(event, data).to;
      const side = String(event.side || event.direction || "").toLowerCase();
      const down = side.includes("sell") || side.includes("bear") || side.includes("high");
      return { time, position: down ? "aboveBar" : "belowBar", color: "#f97316", shape: down ? "arrowDown" : "arrowUp", text: "SWEEP" };
    });
    const structureMarkers = arr(idea.structure || idea.bos_choch || market.structure).map((node) => {
      const time = resolveTimeRange(node, data).to;
      const type = String(node.type || node.label || "").toUpperCase();
      const isBos = type.includes("BOS");
      const down = type.includes("DOWN") || type.includes("BEAR") || type.includes("↓");
      return { time, position: down ? "aboveBar" : "belowBar", color: isBos ? "#60a5fa" : "#f472b6", shape: down ? "arrowDown" : "arrowUp", text: isBos ? `BOS ${down ? "↓" : "↑"}` : `CHOCH ${down ? "↓" : "↑"}` };
    });
    if (typeof series.setMarkers === "function") series.setMarkers([...sweepMarkers, ...structureMarkers].sort((a, b) => a.time - b.time));

    chart.timeScale().fitContent();
    addChartControls(chart, idea || {});
    syncChartSizeLater(chart, container);
    window.addEventListener("resize", () => repaintFns.forEach((fn) => fn()), { passive: true });
  };

  if (typeof window.renderIdeaCard === "function") {
    const originalRenderIdeaCard = window.renderIdeaCard;
    window.renderIdeaCard = function renderIdeaCardFixed(idea, index) {
      return originalRenderIdeaCard(idea, index).replace(/ · AI-идея/g, " · " + escapeHtmlLocal((window.getIdeaDirection ? window.getIdeaDirection(idea) : "Идея")));
    };
  }
})();
