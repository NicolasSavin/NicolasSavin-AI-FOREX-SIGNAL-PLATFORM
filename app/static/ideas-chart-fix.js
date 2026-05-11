(function () {
  "use strict";

  const state = {
    chart: null,
    visibility: { ob: true, fvg: true, liq: true, signals: true },
  };

  const n = (v) => {
    const x = Number(v);
    return Number.isFinite(x) ? x : null;
  };
  const list = (v) => (Array.isArray(v) ? v : v && typeof v === "object" ? [v] : []);
  const firstNum = (...values) => {
    for (const value of values) {
      const x = n(value);
      if (x !== null) return x;
    }
    return null;
  };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  function candlesOf(idea) {
    if (typeof window.collectCandles === "function") return window.collectCandles(idea);
    const sources = [idea?.candles, idea?.chartData?.candles, idea?.chart_data?.candles, idea?.chart?.candles, idea?.market_data?.candles, idea?.market_context?.candles, idea?.history, idea?.ohlc];
    return sources.find((x) => Array.isArray(x) && x.length >= 2) || [];
  }

  function normalizeCandle(c, index) {
    const rawTime = c?.time || c?.timestamp || c?.t || Math.floor(Date.now() / 1000) - (200 - index) * 900;
    return {
      time: typeof rawTime === "number" ? rawTime : Math.floor(new Date(rawTime).getTime() / 1000),
      open: Number(c?.open ?? c?.o ?? c?.close ?? c?.c),
      high: Number(c?.high ?? c?.h ?? c?.close ?? c?.c),
      low: Number(c?.low ?? c?.l ?? c?.close ?? c?.c),
      close: Number(c?.close ?? c?.c),
    };
  }

  function directionOf(idea) {
    const raw = String(idea?.action || idea?.signal || idea?.label || idea?.direction || "WAIT").toUpperCase();
    if (raw.includes("BUY") || raw.includes("ПОКУП")) return "BUY";
    if (raw.includes("SELL") || raw.includes("ПРОДА")) return "SELL";
    return "WAIT";
  }

  function rangeOf(item) {
    if (typeof item === "number" || typeof item === "string") {
      const price = n(item);
      return price === null ? null : { low: price, high: price };
    }
    const low = firstNum(item?.low, item?.bottom, item?.min, item?.price_low, item?.zone_low, item?.from_price, item?.from, item?.y1);
    const high = firstNum(item?.high, item?.top, item?.max, item?.price_high, item?.zone_high, item?.to_price, item?.to, item?.y2);
    const price = firstNum(item?.price, item?.level, item?.value, item?.strike);
    if (low === null && high === null && price !== null) return { low: price, high: price };
    if (low !== null && high === null) return { low, high: low };
    if (high !== null && low === null) return { low: high, high };
    if (low !== null && high !== null) return { low: Math.min(low, high), high: Math.max(low, high) };
    return null;
  }

  function collectRanges(...values) {
    const out = [];
    values.forEach((value) => {
      list(value).forEach((item) => {
        const direct = rangeOf(item);
        if (direct) out.push(direct);
        list(item?.zones).forEach((zone) => { const r = rangeOf(zone); if (r) out.push(r); });
        list(item?.levels).forEach((level) => { const r = rangeOf(level); if (r) out.push(r); });
      });
    });
    return out;
  }

  function avgRange(data) {
    const recent = data.slice(-50);
    return recent.length ? recent.reduce((sum, c) => sum + Math.max(0, c.high - c.low), 0) / recent.length : 0;
  }

  function dedupeAndRank(ranges, data, limit) {
    const close = data.at(-1)?.close || 0;
    const minGap = Math.max(avgRange(data) * 0.25, 0.00001);
    const sorted = ranges
      .filter(Boolean)
      .map((r) => ({ low: Math.min(r.low, r.high), high: Math.max(r.low, r.high) }))
      .sort((a, b) => Math.abs(((a.low + a.high) / 2) - close) - Math.abs(((b.low + b.high) / 2) - close));
    const out = [];
    for (const r of sorted) {
      const mid = (r.low + r.high) / 2;
      if (out.some((x) => Math.abs(((x.low + x.high) / 2) - mid) <= minGap)) continue;
      out.push(r);
      if (out.length >= limit) break;
    }
    return out;
  }

  function fallbackFvgs(data) {
    const ar = avgRange(data);
    const zones = [];
    for (let i = Math.max(2, data.length - 70); i < data.length; i += 1) {
      const a = data[i - 2];
      const c = data[i];
      if (c.low > a.high && c.low - a.high > ar * 0.2) zones.push({ low: a.high, high: c.low });
      if (c.high < a.low && a.low - c.high > ar * 0.2) zones.push({ low: c.high, high: a.low });
    }
    return zones;
  }

  function fallbackObs(data) {
    const ar = avgRange(data);
    const zones = [];
    for (let i = Math.max(1, data.length - 65); i < data.length - 1; i += 1) {
      const c = data[i];
      const next = data[i + 1];
      if (Math.abs(next.close - next.open) < ar * 1.15) continue;
      if ((c.close < c.open && next.close > next.open) || (c.close > c.open && next.close < next.open)) zones.push({ low: c.low, high: c.high });
    }
    return zones;
  }

  function fallbackLiquidity(data) {
    const levels = [];
    for (let i = Math.max(2, data.length - 85); i < data.length - 2; i += 1) {
      const c = data[i];
      if (c.high >= data[i - 1].high && c.high >= data[i - 2].high && c.high >= data[i + 1].high && c.high >= data[i + 2].high) levels.push({ low: c.high, high: c.high });
      if (c.low <= data[i - 1].low && c.low <= data[i - 2].low && c.low <= data[i + 1].low && c.low <= data[i + 2].low) levels.push({ low: c.low, high: c.low });
    }
    return levels;
  }

  function styleOnce() {
    if (document.getElementById("institutional-chart-polish")) return;
    const style = document.createElement("style");
    style.id = "institutional-chart-polish";
    style.textContent = `
      .chart-area{position:relative}.institutional-overlay-layer{position:absolute;inset:0;z-index:11;pointer-events:none;overflow:hidden}.institutional-zone{position:absolute;border-radius:7px;box-shadow:inset 0 0 0 1px rgba(255,255,255,.08)}.institutional-zone span{position:absolute;left:6px;top:3px;font-size:10px;font-weight:950;color:#f8fbff;text-shadow:0 1px 3px #000}.institutional-arrow{position:absolute;left:18px;top:18px;z-index:25;padding:10px 14px;border-radius:16px;font-weight:950;box-shadow:0 12px 32px rgba(0,0,0,.38);pointer-events:none}.institutional-arrow.buy{background:linear-gradient(180deg,#7dffc4,#31f59d);color:#00150c}.institutional-arrow.sell{background:linear-gradient(180deg,#ff7f99,#be123c);color:#fff}.institutional-arrow.wait{background:rgba(71,85,105,.9);color:#fff}.institutional-toggle-row{position:absolute;left:18px;bottom:14px;z-index:35;display:flex;gap:7px;flex-wrap:wrap}.institutional-toggle-row button{border:1px solid rgba(148,163,184,.45);background:rgba(3,14,28,.78);color:#dbeeff;border-radius:999px;padding:6px 9px;font-size:11px;font-weight:900;cursor:pointer}.institutional-toggle-row button.active{background:rgba(56,189,248,.22);border-color:rgba(56,189,248,.8);color:#fff}.chart-fs-btn{position:absolute;right:12px;top:12px;z-index:40;border:1px solid rgba(255,255,255,.2);background:rgba(3,14,28,.82);color:#e8f3ff;border-radius:10px;padding:8px 11px;font-size:12px;font-weight:900;cursor:pointer}.chart-area.chart-fullscreen{position:fixed!important;inset:10px!important;z-index:2147483000!important;width:auto!important;height:auto!important;border-radius:18px!important;background:#06111f!important;box-shadow:0 30px 120px rgba(0,0,0,.86)!important}.chart-area.chart-fullscreen #ideaModalChart{height:calc(100vh - 20px)!important;min-height:calc(100vh - 20px)!important}`;
    document.head.appendChild(style);
  }

  function addPriceLine(series, price, title, color, width = 2) {
    const p = n(price);
    if (p === null) return;
    try { series.createPriceLine({ price: p, color, lineWidth: width, lineStyle: 2, axisLabelVisible: true, title }); } catch (_) {}
  }

  function drawHtmlOverlays(chart, series, idea, data) {
    styleOnce();
    const host = document.getElementById("ideaModalChart")?.closest(".chart-area") || document.getElementById("ideaModalChart");
    if (!host) return;
    host.querySelectorAll(".institutional-overlay-layer,.institutional-toggle-row,.institutional-arrow,.chart-fs-btn").forEach((el) => el.remove());
    host.style.position = "relative";

    const layer = document.createElement("div");
    layer.className = "institutional-overlay-layer";
    host.appendChild(layer);

    const market = idea?.market_context || {};
    const zones = {
      ob: dedupeAndRank(collectRanges(idea?.order_blocks, idea?.orderBlocks, idea?.ob_zones, idea?.poi_zones, market?.order_blocks, market?.orderBlocks).concat(fallbackObs(data)), data, 3),
      fvg: dedupeAndRank(collectRanges(idea?.fvg, idea?.fvgs, idea?.fair_value_gaps, idea?.fairValueGaps, idea?.imbalances, market?.fvg, market?.fvgs, market?.fair_value_gaps, market?.imbalances).concat(fallbackFvgs(data)), data, 3),
      liq: dedupeAndRank(collectRanges(idea?.liquidity, idea?.liquidity_levels, idea?.liquidity_zones, market?.liquidity, market?.liquidity_levels, market?.liquidity_zones, market?.equal_highs, market?.equal_lows).concat(fallbackLiquidity(data)), data, 4),
    };

    const meta = {
      ob: { label: "OB", color: "rgba(167,139,250,.18)", border: "rgba(167,139,250,.95)" },
      fvg: { label: "FVG", color: "rgba(34,211,238,.14)", border: "rgba(34,211,238,.9)" },
      liq: { label: "LIQ", color: "rgba(249,115,22,.10)", border: "rgba(249,115,22,.86)" },
    };

    function repaint() {
      layer.innerHTML = "";
      const start = data[Math.max(0, data.length - 45)]?.time || data[0]?.time;
      const end = data.at(-1)?.time;
      const x1 = chart.timeScale().timeToCoordinate(start);
      const x2 = chart.timeScale().timeToCoordinate(end);
      Object.keys(zones).forEach((kind) => {
        if (!state.visibility[kind]) return;
        zones[kind].forEach((z, index) => {
          const yHigh = series.priceToCoordinate(z.high);
          const yLow = series.priceToCoordinate(z.low);
          if (![x1, x2, yHigh, yLow].every(Number.isFinite)) return;
          const box = document.createElement("div");
          box.className = `institutional-zone institutional-zone-${kind}`;
          box.style.left = `${Math.min(x1, x2)}px`;
          box.style.top = `${Math.min(yHigh, yLow)}px`;
          box.style.width = `${Math.max(10, Math.abs(x2 - x1))}px`;
          box.style.height = `${Math.max(4, Math.abs(yLow - yHigh))}px`;
          box.style.background = meta[kind].color;
          box.style.border = `1px solid ${meta[kind].border}`;
          box.innerHTML = `<span>${meta[kind].label} ${index + 1}</span>`;
          layer.appendChild(box);
        });
      });
    }

    repaint();
    chart.timeScale().subscribeVisibleTimeRangeChange(repaint);
    window.addEventListener("resize", repaint, { passive: true });

    const arrow = document.createElement("div");
    const direction = directionOf(idea);
    arrow.className = `institutional-arrow ${direction === "BUY" ? "buy" : direction === "SELL" ? "sell" : "wait"}`;
    arrow.textContent = direction === "BUY" ? "↗ BUY / Покупка" : direction === "SELL" ? "↘ SELL / Продажа" : "→ WAIT / Наблюдение";
    if (state.visibility.signals) host.appendChild(arrow);

    const toggles = document.createElement("div");
    toggles.className = "institutional-toggle-row";
    [["ob", "OB"], ["fvg", "FVG"], ["liq", "Liquidity"], ["signals", "Signals"]].forEach(([key, text]) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = state.visibility[key] ? "active" : "";
      btn.textContent = text;
      btn.addEventListener("click", (event) => {
        event.stopPropagation();
        state.visibility[key] = !state.visibility[key];
        btn.className = state.visibility[key] ? "active" : "";
        arrow.style.display = state.visibility.signals ? "" : "none";
        repaint();
      });
      toggles.appendChild(btn);
    });
    host.appendChild(toggles);

    const fs = document.createElement("button");
    fs.type = "button";
    fs.className = "chart-fs-btn";
    fs.textContent = "⛶ На весь экран";
    fs.addEventListener("click", (event) => {
      event.stopPropagation();
      host.classList.toggle("chart-fullscreen");
      fs.textContent = host.classList.contains("chart-fullscreen") ? "× Закрыть экран" : "⛶ На весь экран";
      setTimeout(() => {
        try {
          chart.applyOptions({ width: host.clientWidth, height: Math.max(390, host.clientHeight) });
          chart.timeScale().fitContent();
          repaint();
        } catch (_) {}
      }, 40);
    });
    host.appendChild(fs);
  }

  window.renderChartContainer = function renderChartContainerPatched(idea) {
    const rows = candlesOf(idea);
    if (Array.isArray(rows) && rows.length >= 2) return '<div id="ideaModalChart"></div>';
    const rawUrl = idea?.chartImageUrl || idea?.chart_image || idea?.chart_url || "";
    const url = typeof window.normalizeChartImageUrl === "function" ? window.normalizeChartImageUrl(rawUrl) : String(rawUrl || "");
    const symbol = typeof window.getIdeaSymbol === "function" ? window.getIdeaSymbol(idea) : String(idea?.symbol || idea?.instrument || "chart");
    return url ? `<img class="chart-image" src="${esc(url)}?t=${Date.now()}" alt="График ${esc(symbol)}">` : '<div id="ideaModalChart"></div>';
  };
  try { renderChartContainer = window.renderChartContainer; } catch (_) {}

  window.renderModalChart = function renderModalChartPolished(idea) {
    const container = document.getElementById("ideaModalChart");
    if (!container || !window.LightweightCharts) return;
    const data = candlesOf(idea).map(normalizeCandle).filter((c) => [c.time, c.open, c.high, c.low, c.close].every(Number.isFinite));
    if (data.length < 2) {
      container.innerHTML = '<div class="ideas-loading">График недоступен: API не передал свечи.</div>';
      return;
    }
    if (state.chart) { try { state.chart.remove(); } catch (_) {} }
    container.innerHTML = "";
    const chart = LightweightCharts.createChart(container, {
      layout: { background: { color: "#06111f" }, textColor: "#dbeeff" },
      grid: { vertLines: { color: "rgba(255,255,255,.055)" }, horzLines: { color: "rgba(255,255,255,.055)" } },
      rightPriceScale: { borderColor: "rgba(95,156,230,.24)", scaleMargins: { top: 0.12, bottom: 0.12 } },
      timeScale: { borderColor: "rgba(95,156,230,.24)", rightOffset: 8, barSpacing: 8 },
      crosshair: { mode: 1 },
    });
    state.chart = chart;
    window.modalChart = chart;
    const series = chart.addCandlestickSeries({ upColor: "#31f59d", downColor: "#ff5f7a", borderUpColor: "#31f59d", borderDownColor: "#ff5f7a", wickUpColor: "#31f59d", wickDownColor: "#ff5f7a" });
    series.setData(data);
    addPriceLine(series, idea?.entry ?? idea?.entry_price, "ENTRY", "#ffd84d");
    addPriceLine(series, idea?.sl ?? idea?.stop_loss, "SL", "#ff5f7a");
    addPriceLine(series, idea?.tp ?? idea?.take_profit ?? idea?.target, "TP", "#31f59d");
    chart.timeScale().fitContent();
    drawHtmlOverlays(chart, series, idea || {}, data);
    setTimeout(() => {
      try { chart.applyOptions({ width: container.clientWidth, height: Math.max(390, container.clientHeight) }); chart.timeScale().fitContent(); } catch (_) {}
    }, 80);
  };
})();
