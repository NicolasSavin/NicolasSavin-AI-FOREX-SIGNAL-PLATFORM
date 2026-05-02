import { CmeCache } from "./cmeCache";

const cache = new CmeCache();

export async function fetchCmeFutures(url: string) {
  const cached = cache.get<any>(url);
  if (cached) return cached;
  try {
    const res = await fetch(url, { headers: { "User-Agent": "FXSignalPlatform/1.0" } });
    const html = await res.text();
    const volumeMatch = html.match(/"volume"\s*:\s*"?([0-9,]+)"?/i);
    const oiMatch = html.match(/"openInterest"\s*:\s*"?([0-9,]+)"?/i);
    const payload = {
      volume: Number((volumeMatch?.[1] || "0").replace(/,/g, "")),
      openInterest: Number((oiMatch?.[1] || "0").replace(/,/g, "")),
      lastUpdated: new Date().toISOString(),
    };
    cache.set(url, payload);
    return payload;
  } catch {
    return { available: false, reason: "CME scraping failed" };
  }
}
