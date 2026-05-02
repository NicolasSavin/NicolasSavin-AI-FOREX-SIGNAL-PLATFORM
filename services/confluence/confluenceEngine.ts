import { createOptionsAnalysis, type SignalDirection } from "../optionsAnalysis/optionsMetrics.ts";

export interface Candle {
  time?: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

type SmbBias = "bullish" | "bearish" | "neutral" | "unknown";

interface SmartMoneyInput {
  bullishBOS?: boolean;
  bearishBOS?: boolean;
  inBullishOrderBlock?: boolean;
  inBearishOrderBlock?: boolean;
  inDiscount?: boolean;
  inPremium?: boolean;
  fvgBelowPrice?: boolean;
  fvgAbovePrice?: boolean;
  bias?: SmbBias;
}

interface LiquidityInput {
  sweepBelow?: boolean;
  sweepAbove?: boolean;
  stopHuntBelow?: boolean;
  stopHuntAbove?: boolean;
  bullishInducement?: boolean;
  bearishInducement?: boolean;
  bias?: SmbBias;
}

export interface ConfluenceInput {
  symbol: string;
  price: number;
  candles: Candle[];
  signalDirection: SignalDirection;
  smartMoney?: SmartMoneyInput | null;
  liquidity?: LiquidityInput | null;
  options?: unknown;
  volume?: { scoreImpact?: number; trend?: string; confirmation?: string } | null;
}

export interface ConfluenceResult {
  signal: SignalDirection;
  confidence: number;
  score: number;
  breakdown: { smartMoney: number; liquidity: number; options: number; volume: number };
  warnings: string[];
  summary: string;
}

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

function smartMoneyScore(input: ConfluenceInput) {
  const sm = input.smartMoney;
  if (!sm) return { score: 0, bullish: false, bearish: false, reasons: ["SMC данные недоступны (fallback)."] };
  let score = 0;
  const buy = input.signalDirection === "BUY";
  const sell = input.signalDirection === "SELL";

  if (buy) {
    if (sm.bullishBOS) score += 10;
    if (sm.inBullishOrderBlock) score += 8;
    if (sm.inDiscount) score += 6;
    if (sm.fvgBelowPrice) score += 6;
  }
  if (sell) {
    if (sm.bearishBOS) score += 10;
    if (sm.inBearishOrderBlock) score += 8;
    if (sm.inPremium) score += 6;
    if (sm.fvgAbovePrice) score += 6;
  }
  return { score: clamp(score, 0, 30), bullish: !!(sm.bullishBOS || sm.bias === "bullish"), bearish: !!(sm.bearishBOS || sm.bias === "bearish"), reasons: [] };
}

function liquidityScore(input: ConfluenceInput) {
  const lq = input.liquidity;
  if (!lq) return { score: 0, confirms: false, reasons: ["Liquidity данные недоступны (fallback)."] };
  let score = 0;
  if (input.signalDirection === "BUY") {
    if (lq.sweepBelow) score += 10;
    if (lq.stopHuntBelow) score += 8;
    if (lq.bullishInducement) score += 7;
  }
  if (input.signalDirection === "SELL") {
    if (lq.sweepAbove) score += 10;
    if (lq.stopHuntAbove) score += 8;
    if (lq.bearishInducement) score += 7;
  }
  return { score: clamp(score, 0, 25), confirms: score >= 10, reasons: [] };
}

function confidenceFromScore(score: number): number {
  return clamp(score, 0, 100);
}

function confidenceBucket(score: number): string {
  if (score < 20) return "weak";
  if (score < 40) return "low";
  if (score < 60) return "medium";
  if (score < 80) return "strong";
  return "very strong";
}

export function createConfluenceAnalysis(input: ConfluenceInput): ConfluenceResult {
  const warnings: string[] = [];
  const sm = smartMoneyScore(input);
  const lq = liquidityScore(input);
  const opt = createOptionsAnalysis((input.options ?? null) as any, input.signalDirection);
  const optionsScore = clamp(opt.signalImpact.scoreImpact, -15, 15);
  const volumeScore = clamp(Number(input.volume?.scoreImpact ?? 0), -10, 10);

  if (!opt.available) warnings.push("Опционные данные недоступны: использован fallback без влияния.");

  let totalScore = sm.score + lq.score + optionsScore + volumeScore;

  const optionsConflict =
    (input.signalDirection === "BUY" && opt.bias.bias === "bearish") ||
    (input.signalDirection === "SELL" && opt.bias.bias === "bullish");

  if (optionsConflict) {
    totalScore -= 12;
    warnings.push("Конфликт: SMC/Liquidity и options bias расходятся — confidence снижен.");
  }

  if (opt.pinningRisk.risk === "high") {
    totalScore -= 8;
    warnings.push("Высокий pinning risk: target следует ограничить до ближайшей зоны ликвидности.");
  }

  totalScore = clamp(totalScore, 0, 100);

  let signal: SignalDirection = "NEUTRAL";
  const smBullish = sm.bullish || input.smartMoney?.bias === "bullish";
  const smBearish = sm.bearish || input.smartMoney?.bias === "bearish";
  const optionsNotAgainst = !optionsConflict;

  if (input.signalDirection === "BUY" && smBullish && lq.confirms && optionsNotAgainst) signal = "BUY";
  if (input.signalDirection === "SELL" && smBearish && lq.confirms && optionsNotAgainst) signal = "SELL";

  if (!input.smartMoney) warnings.push("SMC слой отсутствует.");
  if (!input.liquidity) warnings.push("Liquidity слой отсутствует.");
  if (!input.volume) warnings.push("Volume слой отсутствует.");

  const confidence = confidenceFromScore(totalScore);
  const summary = [
    `${signal === "NEUTRAL" ? "Смешанный" : signal === "BUY" ? "Bullish" : "Bearish"} confluence (${confidenceBucket(confidence)}):`,
    `- SMC score: ${sm.score}/30`,
    `- Liquidity score: ${lq.score}/25`,
    `- Options impact: ${optionsScore}`,
    `- Volume impact: ${volumeScore}`,
    opt.signalImpact.summary,
  ].join("\n");

  return {
    signal,
    confidence,
    score: totalScore,
    breakdown: { smartMoney: sm.score, liquidity: lq.score, options: optionsScore, volume: volumeScore },
    warnings,
    summary,
  };
}
