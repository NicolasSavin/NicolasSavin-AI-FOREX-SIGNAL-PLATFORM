import test from 'node:test';
import assert from 'node:assert/strict';
import { createConfluenceAnalysis } from '../confluenceEngine.ts';

const optionsBullish = {
  symbol: 'EURUSD', underlyingPrice: 1.1,
  expirations: [{ expiry: '2026-06-01', strikes: [
    { strike: 1.09, callOpenInterest: 150, putOpenInterest: 50 },
    { strike: 1.1, callOpenInterest: 180, putOpenInterest: 60 },
    { strike: 1.11, callOpenInterest: 170, putOpenInterest: 55 },
  ] }],
};

test('all four layers confirm => strong signal', () => {
  const r = createConfluenceAnalysis({
    symbol: 'EURUSD', price: 1.1, candles: [], signalDirection: 'BUY',
    smartMoney: { bullishBOS: true, inBullishOrderBlock: true, inDiscount: true, fvgBelowPrice: true, bias: 'bullish' },
    liquidity: { sweepBelow: true, stopHuntBelow: true, bullishInducement: true },
    options: optionsBullish,
    volume: { scoreImpact: 8 },
  });
  assert.equal(r.signal, 'BUY');
  assert.ok(r.score >= 40);
});

test('one layer against => score reduction', () => {
  const supportive = createConfluenceAnalysis({
    symbol: 'EURUSD', price: 1.1, candles: [], signalDirection: 'BUY',
    smartMoney: { bullishBOS: true, inBullishOrderBlock: true, inDiscount: true, fvgBelowPrice: true, bias: 'bullish' },
    liquidity: { sweepBelow: true, stopHuntBelow: true, bullishInducement: true },
    options: optionsBullish, volume: { scoreImpact: 6 },
  });
  const conflicted = createConfluenceAnalysis({
    symbol: 'EURUSD', price: 1.1, candles: [], signalDirection: 'BUY',
    smartMoney: { bullishBOS: true, inBullishOrderBlock: true, inDiscount: true, fvgBelowPrice: true, bias: 'bullish' },
    liquidity: { sweepBelow: true, stopHuntBelow: true, bullishInducement: true },
    options: optionsBullish, volume: { scoreImpact: -8 },
  });
  assert.ok(conflicted.score < supportive.score);
});

test('options against => warning', () => {
  const optionsBearish = { ...optionsBullish, expirations: [{ expiry: '2026', strikes: [{ strike: 1.1, callOpenInterest: 10, putOpenInterest: 100 }] }] };
  const r = createConfluenceAnalysis({
    symbol: 'EURUSD', price: 1.1, candles: [], signalDirection: 'BUY',
    smartMoney: { bullishBOS: true, bias: 'bullish' },
    liquidity: { sweepBelow: true },
    options: optionsBearish, volume: { scoreImpact: 2 },
  });
  assert.ok(r.warnings.some((w) => w.toLowerCase().includes('конфликт')));
});

test('no data => graceful fallback', () => {
  const r = createConfluenceAnalysis({ symbol: 'EURUSD', price: 1.1, candles: [], signalDirection: 'BUY' });
  assert.equal(r.signal, 'NEUTRAL');
  assert.ok(r.warnings.length >= 3);
});

test('score always 0..100', () => {
  const r = createConfluenceAnalysis({
    symbol: 'EURUSD', price: 1.1, candles: [], signalDirection: 'BUY',
    smartMoney: { bullishBOS: true, inBullishOrderBlock: true, inDiscount: true, fvgBelowPrice: true, bias: 'bullish' },
    liquidity: { sweepBelow: true, stopHuntBelow: true, bullishInducement: true },
    options: optionsBullish, volume: { scoreImpact: 50 },
  });
  assert.ok(r.score >= 0 && r.score <= 100);
});
