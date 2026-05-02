export type SignalDirection = "BUY" | "SELL" | "NEUTRAL";
export type Bias = "bullish" | "bearish" | "neutral" | "unknown";

export interface OptionStrikeRow {
  strike: number;
  callOpenInterest: number;
  putOpenInterest: number;
  callVolume?: number;
  putVolume?: number;
}

export interface OptionExpiration {
  expiry: string;
  strikes: OptionStrikeRow[];
}

export interface OptionsChain {
  symbol: string;
  underlyingPrice: number;
  expirations: OptionExpiration[];
}

const clamp = (v: number, min: number, max: number): number => Math.min(max, Math.max(min, v));
const safe = (v: unknown): number => (Number.isFinite(v as number) ? Number(v) : 0);

const flattenStrikes = (chain?: Partial<OptionsChain> | null): OptionStrikeRow[] => {
  if (!chain || !Array.isArray(chain.expirations)) return [];
  return chain.expirations.flatMap((e) => (Array.isArray(e?.strikes) ? e.strikes : [])).filter((s) => Number.isFinite(s?.strike));
};

const distPct = (a: number, b: number): number | null => {
  if (!Number.isFinite(a) || !Number.isFinite(b) || b === 0) return null;
  return (Math.abs(a - b) / Math.abs(b)) * 100;
};

export function calculatePutCallRatio(chain?: Partial<OptionsChain> | null) {
  const strikes = flattenStrikes(chain);
  const totalCallOI = strikes.reduce((acc, s) => acc + safe(s.callOpenInterest), 0);
  const totalPutOI = strikes.reduce((acc, s) => acc + safe(s.putOpenInterest), 0);
  if (totalCallOI <= 0 || totalPutOI <= 0) {
    return { ratio: null, bias: "unknown" as Bias, totalCallOI, totalPutOI };
  }
  const ratio = totalPutOI / totalCallOI;
  const bias: Bias = ratio > 1.25 ? "bearish" : ratio < 0.75 ? "bullish" : "neutral";
  return { ratio, bias, totalCallOI, totalPutOI };
}

export function calculateKeyStrikes(chain?: Partial<OptionsChain> | null, limit = 5) {
  return flattenStrikes(chain)
    .map((s) => {
      const callOI = safe(s.callOpenInterest);
      const putOI = safe(s.putOpenInterest);
      const totalOI = callOI + putOI;
      const type = callOI > putOI * 1.3 ? "call_wall" : putOI > callOI * 1.3 ? "put_wall" : "mixed_wall";
      return { strike: safe(s.strike), totalOI, callOI, putOI, type };
    })
    .sort((a, b) => b.totalOI - a.totalOI)
    .slice(0, Math.max(1, limit));
}

export function calculateBarrierZones(chain?: Partial<OptionsChain> | null, underlyingPrice?: number) {
  const strikes = flattenStrikes(chain);
  const price = Number.isFinite(underlyingPrice) ? Number(underlyingPrice) : safe(chain?.underlyingPrice);
  if (!strikes.length || !price) return { support: [], resistance: [] };

  const putOIs = strikes.map((s) => safe(s.putOpenInterest)).sort((a, b) => a - b);
  const callOIs = strikes.map((s) => safe(s.callOpenInterest)).sort((a, b) => a - b);
  const putThreshold = putOIs[Math.floor(putOIs.length * 0.8)] ?? 0;
  const callThreshold = callOIs[Math.floor(callOIs.length * 0.8)] ?? 0;
  const maxPut = Math.max(...putOIs, 1);
  const maxCall = Math.max(...callOIs, 1);

  const toStrength = (oi: number, d: number | null, maxOI: number) => {
    const oiScore = (oi / maxOI) * 70;
    const distanceScore = d == null ? 0 : clamp((1 - d / 2) * 30, 0, 30);
    return clamp(Math.round(oiScore + distanceScore), 0, 100);
  };

  const support = strikes
    .filter((s) => s.strike < price && safe(s.putOpenInterest) >= putThreshold)
    .map((s) => {
      const distancePercent = distPct(s.strike, price) ?? 100;
      const putOI = safe(s.putOpenInterest);
      return { strike: safe(s.strike), strength: toStrength(putOI, distancePercent, maxPut), putOI, distancePercent };
    })
    .sort((a, b) => a.distancePercent - b.distancePercent);

  const resistance = strikes
    .filter((s) => s.strike > price && safe(s.callOpenInterest) >= callThreshold)
    .map((s) => {
      const distancePercent = distPct(s.strike, price) ?? 100;
      const callOI = safe(s.callOpenInterest);
      return { strike: safe(s.strike), strength: toStrength(callOI, distancePercent, maxCall), callOI, distancePercent };
    })
    .sort((a, b) => a.distancePercent - b.distancePercent);

  return { support, resistance };
}

export function calculateMaxPain(chain?: Partial<OptionsChain> | null) {
  const strikes = flattenStrikes(chain);
  const price = safe(chain?.underlyingPrice);
  if (!strikes.length) return { strike: null, totalPain: null, distancePercent: null };

  let bestStrike: number | null = null;
  let minPain = Number.POSITIVE_INFINITY;

  for (const candidate of strikes) {
    const settlement = safe(candidate.strike);
    let totalPain = 0;
    for (const s of strikes) {
      totalPain += Math.max(0, settlement - safe(s.strike)) * safe(s.callOpenInterest);
      totalPain += Math.max(0, safe(s.strike) - settlement) * safe(s.putOpenInterest);
    }
    if (totalPain < minPain) {
      minPain = totalPain;
      bestStrike = settlement;
    }
  }

  return { strike: bestStrike, totalPain: Number.isFinite(minPain) ? minPain : null, distancePercent: bestStrike != null ? distPct(bestStrike, price) : null };
}

export function calculatePinningRisk(chain?: Partial<OptionsChain> | null, underlyingPrice?: number) {
  const price = Number.isFinite(underlyingPrice) ? Number(underlyingPrice) : safe(chain?.underlyingPrice);
  if (!price) return { risk: "unknown", nearestPinStrike: null, distancePercent: null, reason: "Нет текущей цены для расчёта pinning." };
  const maxPain = calculateMaxPain(chain);
  const key = calculateKeyStrikes(chain, 3).map((k) => k.strike);
  const candidates = [maxPain.strike, ...key].filter((x): x is number => Number.isFinite(x as number));
  if (!candidates.length) return { risk: "unknown", nearestPinStrike: null, distancePercent: null, reason: "Недостаточно опционных уровней." };
  let nearest = candidates[0];
  let nearestDist = distPct(nearest, price) ?? 100;
  for (const c of candidates.slice(1)) {
    const d = distPct(c, price) ?? 100;
    if (d < nearestDist) {
      nearestDist = d;
      nearest = c;
    }
  }
  const risk = nearestDist <= 0.25 ? "high" : nearestDist <= 0.6 ? "medium" : nearestDist <= 1.0 ? "low" : "none";
  return { risk, nearestPinStrike: nearest, distancePercent: nearestDist, reason: `Ближайший страйк притяжения ${nearest.toFixed(4)}, расстояние ${nearestDist.toFixed(2)}%.` };
}

export function calculateOptionsBias(chain?: Partial<OptionsChain> | null, underlyingPrice?: number) {
  const price = Number.isFinite(underlyingPrice) ? Number(underlyingPrice) : safe(chain?.underlyingPrice);
  if (!price || !flattenStrikes(chain).length) return { bias: "unknown" as Bias, confidence: 0, reasons: ["Нет достаточных данных CME options."] };
  const putCall = calculatePutCallRatio(chain);
  const zones = calculateBarrierZones(chain, price);
  const maxPain = calculateMaxPain(chain);

  let bull = 0;
  let bear = 0;
  const reasons: string[] = [];

  if (putCall.bias === "bullish") { bull += 2; reasons.push("Put/Call ratio указывает на bullish перекос."); }
  if (putCall.bias === "bearish") { bear += 2; reasons.push("Put/Call ratio указывает на bearish перекос."); }

  const nearSupport = zones.support[0];
  const nearResistance = zones.resistance[0];
  if (nearSupport && nearSupport.strength >= 60) { bull += 1.5; reasons.push("Рядом сильная put-support зона."); }
  if (nearResistance && nearResistance.strength >= 60) { bear += 1.5; reasons.push("Рядом сильная call-resistance зона."); }

  if (maxPain.strike && maxPain.distancePercent != null && maxPain.distancePercent >= 1) {
    if (price > maxPain.strike) { bear += 1; reasons.push("Цена заметно выше max pain: риск mean-reversion вниз."); }
    if (price < maxPain.strike) { bull += 1; reasons.push("Цена заметно ниже max pain: риск mean-reversion вверх."); }
  }

  const delta = bull - bear;
  const bias: Bias = Math.abs(delta) < 1 ? "neutral" : delta > 0 ? "bullish" : "bearish";
  const confidence = clamp(Math.round((Math.max(bull, bear) / Math.max(1, bull + bear)) * 100), 0, 100);
  if (!reasons.length) reasons.push("Факторы опционного рынка смешанные.");
  return { bias, confidence, reasons };
}

export function createOptionsAnalysis(chain?: Partial<OptionsChain> | null, signalDirection: SignalDirection) {
  const hasData = flattenStrikes(chain).length > 0 && safe(chain?.underlyingPrice) > 0;
  if (!hasData) {
    return {
      available: false,
      source: "cme_options" as const,
      putCall: calculatePutCallRatio(null),
      keyStrikes: [],
      barrierZones: { support: [], resistance: [] },
      maxPain: { strike: null, totalPain: null, distancePercent: null },
      pinningRisk: { risk: "unknown", nearestPinStrike: null, distancePercent: null, reason: "Опционные данные недоступны." },
      bias: { bias: "unknown", confidence: 0, reasons: ["Опционные данные недоступны."] },
      signalImpact: { scoreImpact: 0, confirmation: "unknown" as const, summary: "Опционные данные отсутствуют, использован fallback." },
    };
  }

  const putCall = calculatePutCallRatio(chain);
  const keyStrikes = calculateKeyStrikes(chain, 5);
  const barrierZones = calculateBarrierZones(chain, safe(chain?.underlyingPrice));
  const maxPain = calculateMaxPain(chain);
  const pinningRisk = calculatePinningRisk(chain, safe(chain?.underlyingPrice));
  const bias = calculateOptionsBias(chain, safe(chain?.underlyingPrice));

  let scoreImpact = 0;
  if (signalDirection === "BUY" && bias.bias === "bullish") scoreImpact += 5;
  if (signalDirection === "SELL" && bias.bias === "bearish") scoreImpact += 5;
  if (signalDirection === "BUY" && bias.bias === "bearish") scoreImpact -= 5;
  if (signalDirection === "SELL" && bias.bias === "bullish") scoreImpact -= 5;
  if (pinningRisk.risk === "high") scoreImpact -= 3;

  const nearSupport = barrierZones.support[0];
  const nearResistance = barrierZones.resistance[0];
  const strongSupport = !!nearSupport && nearSupport.strength >= 60;
  const strongResistance = !!nearResistance && nearResistance.strength >= 60;

  if (signalDirection === "BUY" && strongSupport) scoreImpact += 3;
  if (signalDirection === "BUY" && strongResistance) scoreImpact -= 2;
  if (signalDirection === "SELL" && strongResistance) scoreImpact += 3;
  if (signalDirection === "SELL" && strongSupport) scoreImpact -= 2;

  scoreImpact = clamp(scoreImpact, -10, 10);
  const confirmation = signalDirection === "NEUTRAL"
    ? "neutral"
    : scoreImpact >= 4
      ? "confirmed"
      : scoreImpact <= -3
        ? "conflict"
        : pinningRisk.risk === "high"
          ? "warning"
          : "neutral";

  const summary = confirmation === "confirmed"
    ? `Опционный рынок подтверждает ${signalDirection}: общий bias ${bias.bias}.`
    : confirmation === "conflict"
      ? `Опционные данные конфликтуют с ${signalDirection}: общий bias ${bias.bias}.`
      : pinningRisk.risk === "high"
        ? `Есть риск pinning около страйка ${pinningRisk.nearestPinStrike?.toFixed(4)}, движение может быть ограничено до экспирации.`
        : "Опционный контекст нейтрален и не даёт сильного подтверждения.";

  return { available: true, source: "cme_options" as const, putCall, keyStrikes, barrierZones, maxPain, pinningRisk, bias, signalImpact: { scoreImpact, confirmation, summary } };
}
