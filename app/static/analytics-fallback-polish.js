(function () {
  function n(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function format(value, pair) {
    const parsed = n(value);
    if (parsed === null) return "—";
    if (String(pair || "").includes("JPY")) return parsed.toFixed(3);
    if (String(pair || "").includes("XAU")) return parsed.toFixed(2);
    return parsed.toFixed(5);
  }

  function atr(candles) {
    const rows = Array.isArray(candles) ? candles.slice(-20) : [];
    if (rows.length < 2) return 0;
    let sum = 0;
    let count = 0;
    for (let i = 1; i < rows.length; i += 1) {
      const high = n(rows[i].high);
      const low = n(rows[i].low);
      const prevClose = n(rows[i - 1].close);
      if (high === null || low === null || prevClose === null) continue;
      sum += Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
      count += 1;
    }
    return count ? sum / count : 0;
  }

  function professionalMt4Article(pair, data) {
    const candles = Array.isArray(data && data.candles) ? data.candles : [];
    if (!candles.length) return "Нет доступных свечей для построения анализа.";

    const first = n(candles[0].close);
    const last = n(candles[candles.length - 1].close);
    const highs = candles.map((c) => n(c.high)).filter((v) => v !== null);
    const lows = candles.map((c) => n(c.low)).filter((v) => v !== null);
    const high = Math.max.apply(null, highs);
    const low = Math.min.apply(null, lows);
    const recent = candles.slice(-30);
    const recentHigh = Math.max.apply(null, recent.map((c) => n(c.high)).filter((v) => v !== null));
    const recentLow = Math.min.apply(null, recent.map((c) => n(c.low)).filter((v) => v !== null));
    const averageRange = atr(candles);
    const bullish = last !== null && first !== null && last > first;
    const bearish = last !== null && first !== null && last < first;
    const bias = bullish ? "бычий уклон" : bearish ? "медвежий уклон" : "баланс / флэт";
    const scenario = bullish ? "покупки от отката после подтверждения" : bearish ? "продажи от отката после подтверждения" : "работа только после выхода из диапазона";
    const invalidation = bullish ? recentLow : recentHigh;
    const target = bullish ? recentHigh : recentLow;

    return [
      "ТЕКУЩАЯ СИТУАЦИЯ",
      `${pair} M15: получено ${candles.length} реальных свечей. Последняя цена ${format(last, pair)}. Диапазон выборки ${format(low, pair)} – ${format(high, pair)}. Текущее смещение: ${bias}.`,
      "",
      "ПРИЧИНА ДВИЖЕНИЯ",
      `Цена работает внутри последнего локального диапазона ${format(recentLow, pair)} – ${format(recentHigh, pair)}. Движение оценивается через структуру M15, реакцию на ликвидность и удержание ключевых уровней. Средний диапазон свечи около ${format(averageRange, pair)}.`,
      "",
      "КЛЮЧЕВЫЕ УРОВНИ",
      `Поддержка: ${format(recentLow, pair)}. Сопротивление: ${format(recentHigh, pair)}. Текущая цена: ${format(last, pair)}. Инвалидация сценария: ${format(invalidation, pair)}. Ближайшая цель: ${format(target, pair)}.`,
      "",
      "ТОРГОВЫЙ СЦЕНАРИЙ",
      `Основной сценарий: ${scenario}. Вход без подтверждения от зоны не нужен. Если цена закрепляется за уровнем инвалидации ${format(invalidation, pair)}, сценарий отменяется.`,
      "",
      "РИСК",
      "Объёмы ограничены MT4 tick/свечными данными. Опционный слой в этом ответе не подтверждён отдельным источником. План требует подтверждения следующими свечами и контроля риска.",
    ].join("\n");
  }

  window.buildProfessionalMt4Article = professionalMt4Article;
})();
