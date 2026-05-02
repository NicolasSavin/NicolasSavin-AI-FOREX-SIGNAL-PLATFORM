export function parseNumber(raw: string): number | null {
  const match = raw.match(/([0-9][0-9,]*)/);
  if (!match) return null;
  const num = Number(match[1].replace(/,/g, ""));
  return Number.isFinite(num) ? num : null;
}

export function topKeyStrikes(strikes: number[], callOI: number[], putOI: number[]) {
  return strikes
    .map((strike, i) => ({ strike, oi: (callOI[i] ?? 0) + (putOI[i] ?? 0) }))
    .sort((a, b) => b.oi - a.oi)
    .slice(0, 3);
}
