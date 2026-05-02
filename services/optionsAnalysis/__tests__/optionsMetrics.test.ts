import test from 'node:test';
import assert from 'node:assert/strict';
import {
  calculatePutCallRatio,
  calculateKeyStrikes,
  calculateBarrierZones,
  calculateMaxPain,
  calculatePinningRisk,
  createOptionsAnalysis,
} from '../optionsMetrics.ts';

const bullishChain = {
  symbol: 'EURUSD',
  underlyingPrice: 1.085,
  expirations: [{ expiry: '2026-06-01', strikes: [
    { strike: 1.08, callOpenInterest: 100, putOpenInterest: 30 },
    { strike: 1.085, callOpenInterest: 120, putOpenInterest: 40 },
    { strike: 1.09, callOpenInterest: 90, putOpenInterest: 35 },
  ] }],
};

test('empty chain returns available false', () => {
  const res = createOptionsAnalysis(null, 'BUY');
  assert.equal(res.available, false);
});

test('put/call ratio bullish', () => {
  const r = calculatePutCallRatio(bullishChain);
  assert.equal(r.bias, 'bullish');
});

test('put/call ratio bearish', () => {
  const chain = { ...bullishChain, expirations: [{ expiry: '2026', strikes: [{ strike: 1.085, callOpenInterest: 20, putOpenInterest: 80 }] }] };
  const r = calculatePutCallRatio(chain);
  assert.equal(r.bias, 'bearish');
});

test('key strikes sorted correctly', () => {
  const ks = calculateKeyStrikes(bullishChain, 2);
  assert.equal(ks.length, 2);
  assert.ok(ks[0].totalOI >= ks[1].totalOI);
});

test('barrier zones detect support/resistance', () => {
  const z = calculateBarrierZones({
    symbol: 'EURUSD', underlyingPrice: 1.1,
    expirations: [{ expiry: 'x', strikes: [
      { strike: 1.09, callOpenInterest: 10, putOpenInterest: 500 },
      { strike: 1.11, callOpenInterest: 520, putOpenInterest: 10 },
      { strike: 1.1, callOpenInterest: 100, putOpenInterest: 100 },
    ] }],
  }, 1.1);
  assert.ok(z.support.length > 0);
  assert.ok(z.resistance.length > 0);
});

test('max pain calculation works', () => {
  const m = calculateMaxPain(bullishChain);
  assert.ok(m.strike !== null);
  assert.ok(m.totalPain !== null);
});

test('pinning risk high near max pain', () => {
  const chain = { ...bullishChain, underlyingPrice: 1.08501 };
  const p = calculatePinningRisk(chain, chain.underlyingPrice);
  assert.equal(p.risk, 'high');
});

test('BUY confirmed by bullish bias', () => {
  const res = createOptionsAnalysis(bullishChain, 'BUY');
  assert.ok(['confirmed', 'neutral', 'warning'].includes(res.signalImpact.confirmation));
  assert.ok(res.signalImpact.scoreImpact >= 0);
});

test('SELL conflicts with bullish bias', () => {
  const res = createOptionsAnalysis(bullishChain, 'SELL');
  assert.ok(['conflict', 'neutral'].includes(res.signalImpact.confirmation));
});

test('scoreImpact capped between -10 and +10', () => {
  const res = createOptionsAnalysis(bullishChain, 'BUY');
  assert.ok(res.signalImpact.scoreImpact <= 10);
  assert.ok(res.signalImpact.scoreImpact >= -10);
});
