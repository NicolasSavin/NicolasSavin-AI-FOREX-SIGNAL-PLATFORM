import { CmeCache } from "./cmeCache";
import { topKeyStrikes } from "./cmeParser";

const cache = new CmeCache();

export async function fetchCmeOptions(url: string) {
  const cached = cache.get<any>(url);
  if (cached) return cached;
  try {
    const res = await fetch(url, { headers: { "User-Agent": "FXSignalPlatform/1.0" } });
    const html = await res.text();
    const strikes: number[] = [];
    const callOI: number[] = [];
    const putOI: number[] = [];
    const expirations: string[] = [];

    const analysis = {
      putCallRatio: callOI.length ? putOI.reduce((a, b) => a + b, 0) / callOI.reduce((a, b) => a + b, 0) : null,
      keyStrikes: topKeyStrikes(strikes, callOI, putOI).map((x) => x.strike),
      barrierZones: topKeyStrikes(strikes, callOI, putOI),
      maxPain: strikes[0] ?? null,
    };

    const payload = { strikes, callOI, putOI, expirations, analysis };
    cache.set(url, payload);
    return payload;
  } catch {
    return { available: false, reason: "CME scraping failed" };
  }
}
