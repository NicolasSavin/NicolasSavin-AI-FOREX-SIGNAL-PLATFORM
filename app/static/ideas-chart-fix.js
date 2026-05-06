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

  function addLine(series, price, title, color, style, width) {
    const p = number(price);
    if (p === null || !series || !series.createPriceLine) return;
    try {
      series.createPriceLine({
        price: p,
        color: color || "#8fb9ff",
        lineWidth: width || 1,
        lineStyle: style === "solid" ? 0 : style === "dashed" ? 2 : 1,
        axisLabelVisible: true,
        title: title,
      });
    } catch (e) {
      console.warn("chart_line_failed", title, e);
    }
  }

  function addRange(series, low, high, label, color) {
    const lo = number(low);
    const hi = number(high);
    if (lo === null && hi === null) return;
    if (lo !== null && hi !== null && Math.abs(lo - hi) > 0) {
      const a = Math.min(lo, hi);
      const b = Math.max(lo, hi);
      addLine(series, b, label + " HIGH", color, "dashed", 1);
      addLine(series, a, label + " LOW", color, "dashed", 1);
      return;
    }
    addLine(series, lo !== null ? lo : hi, label, color, "dashed", 1);
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

  function addIdeaOverlays(series, idea) {
    addLine(series, idea.entry ?? idea.entry_price, "ENTRY", "#ffd84d", "dashed", 2);
    addLine(series, idea.sl ?? idea.stop_loss, "SL", "#ff5f7a", "dashed", 2);
    addLine(series, idea.tp ?? idea.take_profit ?? idea.target, "TP", "#31f59d", "dashed", 2);

    addRange(series, idea.selected_zone_low, idea.selected_zone_high, "ZONE", "#38bdf8");

    const market = idea.market_context || {};
    const orderBlocks = [idea.order_blocks, idea.orderBlocks, market.order_blocks, market.orderBlocks, idea.ob_zones, idea.poi_zones];
    orderBlocks.flatMap(collectRanges).slice(0, 6).forEach((r, i) => addRange(series, r.low, r.high, i === 0 ? "OB" : "OB " + (i + 1), "#a78bfa"));

    const fvgs = [idea.fvg, idea.fvgs, idea.fair_value_gaps, idea.fairValueGaps, market.fvg, market.fvgs, market.fair_value_gaps];
    fvgs.flatMap(collectRanges).slice(0, 6).forEach((r, i) => addRange(series, r.low, r.high, i === 0 ? "FVG" : "FVG " + (i + 1), "#22d3ee"));

    const liquidity = [idea.liquidity, idea.liquidity_levels, idea.liquidity_zones, idea.liquidity_sweep, market.liquidity, market.liquidity_levels, market.liquidity_zones];
    limitUnique(liquidity.flatMap(collectFlatNumbers), 10).forEach((p, i) => addLine(series, p, i === 0 ? "LIQ" : "LIQ " + (i + 1), "#f97316", "dotted", 1));

    const opt = idea.options_analysis || {};
    const optionLevels = [opt.keyLevels, opt.keyStrikes, opt.callWalls, opt.putWalls, opt.maxPain, idea.options_key_levels, idea.options_levels];
    limitUnique(optionLevels.flatMap(collectFlatNumbers), 12).forEach((p, i) => addLine(series, p, i === 0 ? "OPT" : "OPT " + (i + 1), "#facc15", "dotted", 1));
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
    addIdeaOverlays(series, idea || {});
    chart.timeScale().fitContent();
  };

  if (typeof window.renderIdeaCard === "function") {
    const originalRenderIdeaCard = window.renderIdeaCard;
    window.renderIdeaCard = function renderIdeaCardFixed(idea, index) {
      return originalRenderIdeaCard(idea, index).replace(/ · AI-идея/g, " · " + escapeHtmlLocal((window.getIdeaDirection ? window.getIdeaDirection(idea) : "Идея")));
    };
  }
})();
