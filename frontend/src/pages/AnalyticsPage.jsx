import React, { useMemo, useState } from "react";

const PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"];
const ANALYTICS_PROMPT =
  "Ты — Grok AI аналитик. Сделай глубокий и структурный разбор 4 валютных пар: EURUSD, GBPUSD, USDJPY, USDCHF. По каждой паре обязательно используй: Smart Money + ICT (ликвидность, BOS/CHoCH, order blocks/FVG), объемный анализ, дивергенции, опционы (если нет данных — пометь как unavailable), волновой контекст, паттерны (графические и гармонические), фундаментальный фон (ключевые мировые события и макро-драйверы). Для каждой пары верни поля: Bias (Bullish/Bearish/Neutral), Situation, Confirmation, Invalidation, Risks. Не выдумывай котировки, новости или опционные метрики. Если live-данных нет, прямо укажи fallback/unavailable. Ответ строго структурируй по каждой паре.";

const DEFAULT_SECTION = "Нет данных";

function toSafeObject(value) {
  return value && typeof value === "object" ? value : {};
}

function normalizeWarnings(value) {
  if (Array.isArray(value)) {
    return value.filter((item) => typeof item === "string" && item.trim());
  }
  if (typeof value === "string" && value.trim()) {
    return [value.trim()];
  }
  return [];
}

function splitLines(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function detectBias(text) {
  const source = String(text || "").toLowerCase();
  if (/bullish|быч|лонг|рост/.test(source)) return "Bullish";
  if (/bearish|медв|шорт|сниж|паден/.test(source)) return "Bearish";
  return "Neutral";
}

function parseSectionBlock(blockText) {
  const lines = splitLines(blockText);
  const data = {
    bias: detectBias(blockText),
    situation: DEFAULT_SECTION,
    confirmation: DEFAULT_SECTION,
    invalidation: DEFAULT_SECTION,
    risks: DEFAULT_SECTION,
  };

  const joined = lines.join("\n");
  const patterns = [
    ["situation", /(situation|ситуац(?:ия|ии)|контекст)\s*[:\-]\s*([\s\S]*?)(?=\n(?:confirmation|подтвержд|invalidation|отмен|risks|риски|bias|уклон)\b|$)/i],
    ["confirmation", /(confirmation|подтвержд\w*)\s*[:\-]\s*([\s\S]*?)(?=\n(?:situation|ситуац|invalidation|отмен|risks|риски|bias|уклон)\b|$)/i],
    ["invalidation", /(invalidation|отмен\w*)\s*[:\-]\s*([\s\S]*?)(?=\n(?:situation|ситуац|confirmation|подтвержд|risks|риски|bias|уклон)\b|$)/i],
    ["risks", /(risks?|риски?)\s*[:\-]\s*([\s\S]*?)(?=\n(?:situation|ситуац|confirmation|подтвержд|invalidation|отмен|bias|уклон)\b|$)/i],
  ];

  patterns.forEach(([key, pattern]) => {
    const match = joined.match(pattern);
    if (match?.[2]) {
      data[key] = match[2].trim();
    }
  });

  const biasLine = lines.find((line) => /(bias|уклон|направление)\s*[:\-]/i.test(line));
  if (biasLine) {
    data.bias = detectBias(biasLine);
  }

  if (Object.values(data).every((v) => v === DEFAULT_SECTION) && lines.length) {
    data.situation = lines.slice(0, 3).join(" ");
    data.risks = lines.slice(3).join(" ") || DEFAULT_SECTION;
  }

  return data;
}

function parsePairsFromReply(reply) {
  const safeReply = String(reply || "");
  const parsed = {};

  PAIRS.forEach((pair, idx) => {
    const nextPair = PAIRS[idx + 1];
    const pairRegex = new RegExp(
      `${pair}\\s*[:\\-]?([\\s\\S]*?)(?=${nextPair ? nextPair : "$"})`,
      "i",
    );
    const match = safeReply.match(pairRegex);
    parsed[pair] = parseSectionBlock(match?.[1] || "");
  });

  return parsed;
}

function getStatusMeta(statusValue) {
  const source = String(statusValue || "").toLowerCase();
  if (source.includes("live")) {
    return { label: "LIVE", className: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10" };
  }
  if (source.includes("fallback")) {
    return { label: "FALLBACK", className: "text-amber-300 border-amber-500/40 bg-amber-500/10" };
  }
  return { label: "UNAVAILABLE", className: "text-rose-300 border-rose-500/40 bg-rose-500/10" };
}

function getBiasMeta(bias) {
  if (bias === "Bullish") {
    return {
      badge: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10",
      card: "border-emerald-500/30 shadow-emerald-950/30",
    };
  }
  if (bias === "Bearish") {
    return {
      badge: "text-rose-300 border-rose-500/40 bg-rose-500/10",
      card: "border-rose-500/30 shadow-rose-950/30",
    };
  }
  return {
    badge: "text-amber-200 border-amber-400/40 bg-amber-400/10",
    card: "border-slate-600/60 shadow-slate-950/40",
  };
}

function formatUpdatedAt(date) {
  if (!date) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

export default function AnalyticsPage() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);

  const parsed = useMemo(() => {
    const safe = toSafeObject(analysis);
    const reply = typeof safe.reply === "string" ? safe.reply : "";
    const source = typeof safe.source === "string" ? safe.source : "Не указан";
    const dataStatusRaw = safe.dataStatus ?? safe.data_status;
    const dataStatus = typeof dataStatusRaw === "string" ? dataStatusRaw : "unknown";
    const warnings = normalizeWarnings(safe.warnings);
    const pairs = parsePairsFromReply(reply);
    return { source, dataStatus, warnings, pairs };
  }, [analysis]);

  const statusMeta = getStatusMeta(parsed.dataStatus);

  const handleRefresh = async () => {
    setLoading(true);
    setError("");

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: ANALYTICS_PROMPT }),
      });

      if (!response.ok) {
        throw new Error(`Ошибка API: ${response.status}`);
      }

      const payload = await response.json();
      setAnalysis(toSafeObject(payload));
      setUpdatedAt(new Date());
    } catch (requestError) {
      setAnalysis(null);
      setError(requestError instanceof Error ? requestError.message : "Не удалось получить разбор.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-950 via-[#050a12] to-black text-slate-100 px-4 py-6 md:px-6 md:py-8">
      <div className="max-w-7xl mx-auto space-y-5">
        <section className="rounded-2xl border border-slate-800/80 bg-slate-950/85 p-5 md:p-6 shadow-2xl shadow-cyan-950/20">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[11px] uppercase tracking-[0.24em] text-cyan-300/90">AI Desk Brief</p>
              <h1 className="mt-2 text-2xl md:text-3xl font-semibold tracking-tight">Аналитика</h1>
              <p className="mt-2 text-sm text-slate-300">Grok-разбор FX: SMC/ICT, объемы, дивергенции, опционы, волны, паттерны и фундаментал</p>
            </div>
            <span className={`inline-flex items-center rounded-md border px-3 py-1 text-xs font-semibold tracking-wide ${statusMeta.className}`}>
              {statusMeta.label}
            </span>
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="rounded-md border border-cyan-400/40 bg-cyan-400/15 px-4 py-2 text-sm font-semibold text-cyan-200 transition hover:bg-cyan-400/25 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading ? "Grok анализирует рынок…" : "Обновить разбор"}
            </button>
            <p className="text-xs text-slate-400">Обновлено: {formatUpdatedAt(updatedAt)}</p>
          </div>

          {error && (
            <div className="mt-4 rounded-lg border border-rose-600/40 bg-rose-950/20 p-3 text-sm text-rose-200">{error}</div>
          )}
        </section>

        <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {PAIRS.map((pair) => {
            const pairData = parsed.pairs[pair] || parseSectionBlock("");
            const biasMeta = getBiasMeta(pairData.bias);
            return (
              <article
                key={pair}
                className={`rounded-xl border bg-gradient-to-b from-slate-900/95 to-slate-950/95 p-4 shadow-xl ${biasMeta.card}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-[10px] uppercase tracking-[0.22em] text-slate-400">FX Major</p>
                    <h2 className="mt-1 text-xl font-semibold tracking-wide text-slate-100">{pair}</h2>
                  </div>
                  <span className={`rounded-md border px-2.5 py-1 text-xs font-semibold ${biasMeta.badge}`}>{pairData.bias}</span>
                </div>

                <div className="mt-4 space-y-3 text-sm leading-relaxed">
                  <div>
                    <p className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Situation</p>
                    <p className="mt-1 text-slate-200">{pairData.situation}</p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Confirmation</p>
                    <p className="mt-1 text-slate-200">{pairData.confirmation}</p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Invalidation</p>
                    <p className="mt-1 text-slate-200">{pairData.invalidation}</p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Risks</p>
                    <p className="mt-1 text-slate-200">{pairData.risks}</p>
                  </div>
                </div>

                <footer className="mt-4 border-t border-slate-800/80 pt-3 text-xs text-slate-400">
                  <p>Data status: {parsed.dataStatus || "unknown"}</p>
                  <p className="mt-1">{parsed.warnings.length ? parsed.warnings.join(" • ") : "Warnings: нет"}</p>
                  <p className="mt-1">Источник: {parsed.source}</p>
                </footer>
              </article>
            );
          })}
        </section>

        <p className="text-center text-xs text-slate-500">Это аналитический обзор, не инвестиционная рекомендация.</p>
      </div>
    </div>
  );
}
