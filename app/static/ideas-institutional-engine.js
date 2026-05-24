// Frontend institutional ideas engine for /ideas.
// It uses real candles from the existing backend chart/debug endpoints and builds practical
// SMC-style scenarios when the backend returns no A/B ideas or only WAIT/NO TRADE.

const INSTITUTIONAL_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"];
const INSTITUTIONAL_TIMEFRAMES = ["M15", "H1", "H4"];
const INSTITUTIONAL_REFRESH_MS = 180000;

function instNum(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function instRound(symbol, value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return null;
  if (String(symbol).includes("JPY")) return Number(num.toFixed(3));
  if (String(symbol).includes("XAU")) return Number(num.toFixed(2));
  return Number(num.toFixed(5));
}

function instNormalizeCandles(raw) {
  const rows = Array.isArray(raw) ? raw : [];
  return rows
    .map((c, index) => ({
      index,
      time: c.time || c.timestamp || c.datetime || c.date,
      open: instNum(c.open ?? c.o),
      high: instNum(c.high ?? c.h),
      low: instNum(c.low ?? c.l),
      close: instNum(c.close ?? c.c),
      volume: instNum(c.volume ?? c.v ?? c.tick_volume) || 1,
    }))
    .filter((c) => [c.open, c.high, c.low, c.close].every(Number.isFinite));
}

function instAtr(candles, period = 14) {
  if (candles.length < period + 1) return null;
  const trs = [];
  for (let i = candles.length - period; i < candles.length; i += 1) {
    const c = candles[i];
    const prev = candles[i - 1];
    trs.push(Math.max(c.high - c.low, Math.abs(c.high - prev.close), Math.abs(c.low - prev.close)));
  }
  return trs.reduce((a, b) => a + b, 0) / trs.length;
}

function instSwings(candles, field) {
  const result = [];
  for (let i = 2; i < candles.length - 2; i += 1) {
    const v = candles[i][field];
    if (field === "high") {
      if (v >= candles[i - 1].high && v >= candles[i - 2].high && v >= candles[i + 1].high && v >= candles[i + 2].high) {
        result.push({ index: i, price: v });
      }
    } else if (v <= candles[i - 1].low && v <= candles[i - 2].low && v <= candles[i + 1].low && v <= candles[i + 2].low) {
      result.push({ index: i, price: v });
    }
  }
  return result;
}

function instSma(values, period) {
  if (values.length < period) return null;
  const slice = values.slice(-period);
  return slice.reduce((a, b) => a + b, 0) / slice.length;
}

function instDetectFvg(candles, action) {
  const zones = [];
  for (let i = Math.max(2, candles.length - 45); i < candles.length; i += 1) {
    const a = candles[i - 2];
    const b = candles[i - 1];
    const c = candles[i];
    const body = Math.abs(b.close - b.open);
    const range = Math.max(b.high - b.low, 1e-12);
    const displacement = body / range > 0.55;
    if (!displacement) continue;
    if (action === "BUY" && c.low > a.high) zones.push({ low: a.high, high: c.low, index: i, type: "bullish_fvg" });
    if (action === "SELL" && c.high < a.low) zones.push({ low: c.high, high: a.low, index: i, type: "bearish_fvg" });
  }
  return zones.slice(-3);
}

function instBuildScenario(symbol, tf, candles) {
  if (candles.length < 50) return null;
  const last = candles[candles.length - 1];
  const prev = candles[candles.length - 2];
  const atr = instAtr(candles, 14) || Math.max(last.high - last.low, 1e-6);
  const highs = instSwings(candles, "high");
  const lows = instSwings(candles, "low");
  const recentHighs = highs.filter((s) => s.index < candles.length - 1).slice(-5);
  const recentLows = lows.filter((s) => s.index < candles.length - 1).slice(-5);
  if (!recentHighs.length || !recentLows.length) return null;

  const lastSwingHigh = recentHighs[recentHighs.length - 1];
  const lastSwingLow = recentLows[recentLows.length - 1];
  const closes = candles.map((c) => c.close);
  const sma20 = instSma(closes, 20);
  const sma50 = instSma(closes, 50);
  const trend = sma20 && sma50 ? (sma20 > sma50 ? "bullish" : sma20 < sma50 ? "bearish" : "neutral") : "neutral";

  const sweptLow = last.low < lastSwingLow.price && last.close > lastSwingLow.price;
  const sweptHigh = last.high > lastSwingHigh.price && last.close < lastSwingHigh.price;
  const bosUp = last.close > lastSwingHigh.price || prev.close > lastSwingHigh.price;
  const bosDown = last.close < lastSwingLow.price || prev.close < lastSwingLow.price;

  let action = "WAIT";
  let setup = "developing";
  let reason = "Структура пока без чистого триггера.";

  if ((sweptLow && trend !== "bearish") || (bosUp && trend === "bullish")) {
    action = "BUY";
    setup = sweptLow ? "liquidity_sweep_reversal" : "bos_continuation";
    reason = sweptLow
      ? "Цена сняла sell-side liquidity ниже локального swing low и закрылась обратно выше уровня — возможен smart-money разворот."
      : "Цена закрепляется выше swing high на фоне восходящего SMA-контекста — возможен continuation после BOS.";
  } else if ((sweptHigh && trend !== "bullish") || (bosDown && trend === "bearish")) {
    action = "SELL";
    setup = sweptHigh ? "liquidity_sweep_reversal" : "bos_continuation";
    reason = sweptHigh
      ? "Цена сняла buy-side liquidity выше локального swing high и закрылась обратно ниже уровня — возможен smart-money разворот."
      : "Цена закрепляется ниже swing low на фоне нисходящего SMA-контекста — возможен continuation после BOS.";
  } else if (trend === "bullish" && last.close > sma20) {
    action = "BUY";
    setup = "trend_pullback_watchlist";
    reason = "Восходящий контекст сохраняется, цена удерживается выше SMA20; идея требует подтверждения ретеста POI/FVG.";
  } else if (trend === "bearish" && last.close < sma20) {
    action = "SELL";
    setup = "trend_pullback_watchlist";
    reason = "Нисходящий контекст сохраняется, цена удерживается ниже SMA20; идея требует подтверждения ретеста POI/FVG.";
  }

  if (action === "WAIT") return null;

  const fvgs = instDetectFvg(candles, action);
  const fvg = fvgs[fvgs.length - 1];
  let entry = last.close;
  if (fvg) entry = (fvg.low + fvg.high) / 2;
  else if (action === "BUY") entry = Math.min(last.close, last.low + atr * 0.35);
  else entry = Math.max(last.close, last.high - atr * 0.35);

  let sl;
  let tp;
  if (action === "BUY") {
    sl = Math.min(lastSwingLow.price, last.low) - atr * 0.25;
    const liquidityTarget = Math.max(lastSwingHigh.price, ...recentHighs.map((s) => s.price));
    tp = Math.max(entry + Math.abs(entry - sl) * 1.6, liquidityTarget);
  } else {
    sl = Math.max(lastSwingHigh.price, last.high) + atr * 0.25;
    const liquidityTarget = Math.min(lastSwingLow.price, ...recentLows.map((s) => s.price));
    tp = Math.min(entry - Math.abs(sl - entry) * 1.6, liquidityTarget);
  }

  const risk = Math.abs(entry - sl);
  const reward = Math.abs(tp - entry);
  const rr = risk > 0 ? reward / risk : 0;
  if (!Number.isFinite(rr) || rr < 1.05) return null;

  let confidence = 58;
  if (setup.includes("sweep")) confidence += 10;
  if (setup.includes("bos")) confidence += 8;
  if (fvg) confidence += 7;
  if ((action === "BUY" && trend === "bullish") || (action === "SELL" && trend === "bearish")) confidence += 8;
  if (rr >= 1.6) confidence += 6;
  confidence = Math.max(50, Math.min(88, Math.round(confidence)));

  const grade = confidence >= 74 ? "A" : confidence >= 62 ? "B" : "C";
  const mode = grade === "A" ? "prop_entry" : grade === "B" ? "watchlist" : "research_only";
  const fvgText = fvg ? ` Дополнительный POI: ${fvg.type} ${instRound(symbol, fvg.low)}–${instRound(symbol, fvg.high)}.` : "";

  return {
    id: `institutional-${symbol}-${tf}-${setup}`,
    idea_id: `institutional-${symbol}-${tf}-${setup}`,
    symbol,
    pair: symbol,
    timeframe: tf,
    tf,
    action,
    signal: action,
    direction: action === "BUY" ? "bullish" : "bearish",
    bias: action === "BUY" ? "bullish" : "bearish",
    entry: instRound(symbol, entry),
    entry_price: instRound(symbol, entry),
    sl: instRound(symbol, sl),
    stop_loss: instRound(symbol, sl),
    tp: instRound(symbol, tp),
    take_profit: instRound(symbol, tp),
    rr: Number(rr.toFixed(2)),
    risk_reward: Number(rr.toFixed(2)),
    confidence,
    final_confidence: confidence,
    current_price: instRound(symbol, last.close),
    price: instRound(symbol, last.close),
    status: mode === "prop_entry" ? "ACTIVE" : "WAIT",
    trade_permission: mode !== "research_only",
    advisor_allowed: mode !== "research_only",
    provider: "frontend_institutional_engine",
    data_provider: "backend candles + SMC frontend engine",
    data_status: "real_or_delayed_candles",
    setup_type: setup,
    entry_source: fvg ? "fvg_retest" : setup,
    selected_zone_type: fvg ? fvg.type : setup,
    selected_zone_low: fvg ? instRound(symbol, fvg.low) : null,
    selected_zone_high: fvg ? instRound(symbol, fvg.high) : null,
    candles: candles.slice(-160),
    chart_data: { candles: candles.slice(-160) },
    chartData: { candles: candles.slice(-160) },
    reason_ru: reason,
    summary_ru: `${symbol} ${tf}: ${action}. ${reason}${fvgText} Entry ${instRound(symbol, entry)}, SL ${instRound(symbol, sl)}, TP ${instRound(symbol, tp)}, R/R ${rr.toFixed(2)}.`,
    unified_narrative: `${symbol} ${tf}: ${action}. ${reason}${fvgText} План: ждать реакцию цены в зоне entry ${instRound(symbol, entry)}; инвалидация за ${instRound(symbol, sl)}; цель — ликвидность/расширение к ${instRound(symbol, tp)}. Риск-профиль ${rr.toFixed(2)}R.`,
    news_context_ru: "Фундаментальный слой не используется как обязательный фильтр; идея построена по свечам, ликвидности и структуре.",
    prop_signal_score: {
      score: confidence,
      grade,
      mode,
      decision_ru: mode === "prop_entry" ? "Prop entry: структура и риск/прибыль достаточны для торгового сценария." : mode === "watchlist" ? "Watchlist: идея рабочая, но нужен триггер в зоне входа." : "Research only: сценарий требует подтверждения.",
      direction: action,
      blockers: [],
      missing_inputs: [],
      criteria: [
        { key: "liquidity", label_ru: "Liquidity sweep / BOS", weight: 25, score: setup.includes("sweep") || setup.includes("bos") ? 25 : 15, status: "confirmed", text_ru: reason },
        { key: "poi", label_ru: "OB/FVG/POI", weight: 20, score: fvg ? 20 : 12, status: fvg ? "confirmed" : "partial", text_ru: fvg ? fvg.type : "entry по импульсной зоне" },
        { key: "risk_reward", label_ru: "Risk/Reward", weight: 20, score: rr >= 1.6 ? 20 : 14, status: "confirmed", text_ru: `R/R ${rr.toFixed(2)}` },
        { key: "trend", label_ru: "Trend context", weight: 15, score: trend === "neutral" ? 8 : 15, status: trend === "neutral" ? "partial" : "confirmed", text_ru: trend },
        { key: "candles", label_ru: "Candles", weight: 20, score: 20, status: "confirmed", text_ru: `${candles.length} candles` },
      ],
    },
    prop_score: confidence,
    prop_grade: grade,
    prop_mode: mode,
    prop_decision_ru: mode === "prop_entry" ? "Prop entry" : mode === "watchlist" ? "Watchlist" : "Research only",
    diagnostics: { source: "ideas-institutional-engine.js", setup, trend, sweptLow, sweptHigh, bosUp, bosDown, atr },
    updated_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
  };
}

async function instFetchCandles(symbol, tf) {
  const urls = [`/api/debug/candles/${symbol}/${tf}?limit=220`, `/api/chart/${symbol}?tf=${tf}&limit=220`];
  for (const url of urls) {
    try {
      const data = await getJson(url);
      const candles = instNormalizeCandles(data?.candles || data?.chartData?.candles || data?.chart_data?.candles || []);
      if (candles.length >= 50) return candles;
    } catch (error) {
      console.warn("institutional_candles_fetch_failed", symbol, tf, error);
    }
  }
  return [];
}

function instNeedsFallback(payload) {
  const ideas = Array.isArray(payload?.ideas) ? payload.ideas : Array.isArray(payload?.signals) ? payload.signals : [];
  if (!ideas.length) return true;
  const good = ideas.filter((idea) => ["A", "B"].includes(String(idea?.prop_grade || idea?.prop_signal_score?.grade || "").toUpperCase()));
  return good.length < 3;
}

async function buildInstitutionalIdeas() {
  const candidates = [];
  for (const symbol of INSTITUTIONAL_SYMBOLS) {
    for (const tf of INSTITUTIONAL_TIMEFRAMES) {
      const candles = await instFetchCandles(symbol, tf);
      const idea = instBuildScenario(symbol, tf, candles);
      if (idea) candidates.push(idea);
    }
  }
  candidates.sort((a, b) => Number(b.confidence || 0) - Number(a.confidence || 0));
  return candidates.slice(0, 12);
}

async function loadIdeasInstitutional() {
  let backendPayload = null;
  try {
    backendPayload = await getJson("/ideas/market");
  } catch (error) {
    console.warn("backend_ideas_failed_using_institutional_engine", error);
  }

  const backendIdeas = Array.isArray(backendPayload?.ideas) ? backendPayload.ideas : Array.isArray(backendPayload?.signals) ? backendPayload.signals : [];
  if (backendPayload && !instNeedsFallback(backendPayload)) {
    renderIdeas(backendPayload);
    return;
  }

  const institutionalIdeas = await buildInstitutionalIdeas();
  const merged = [...institutionalIdeas, ...backendIdeas]
    .filter((idea, index, arr) => arr.findIndex((x) => String(x.idea_id || x.id) === String(idea.idea_id || idea.id)) === index)
    .slice(0, 16);

  const payload = {
    ...(backendPayload || {}),
    ideas: merged,
    signals: merged,
    updated_at_utc: new Date().toISOString(),
    engine: "frontend_institutional_smc_fallback",
    warning: institutionalIdeas.length
      ? "Показаны institutional SMC идеи, рассчитанные по свечам через frontend fallback engine."
      : "Не удалось построить institutional идеи: нет достаточных свечей.",
  };
  renderIdeas(payload);
}

window.loadIdeas = loadIdeasInstitutional;
window.addEventListener("DOMContentLoaded", () => {
  setTimeout(loadIdeasInstitutional, 1200);
  setInterval(loadIdeasInstitutional, INSTITUTIONAL_REFRESH_MS);
});
