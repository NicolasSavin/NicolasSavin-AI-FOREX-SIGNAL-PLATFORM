import React, { useMemo, useState } from "react";

const PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"];

const ANALYTICS_PROMPT = `
Ты — Grok AI Chief Macro & Quant FX Analyst.

ЗАДАЧА:
Сформируй ПОЛНОЦЕННЫЙ профессиональный FX-разбор по 4 парам: EURUSD, GBPUSD, USDJPY, USDCHF.
Ответ должен быть уникальным на каждом запуске: меняй формулировки, порядок аргументов, тип акцентов и структуру обоснования без потери качества.

КРИТИЧЕСКИЕ ПРАВИЛА ДОСТОВЕРНОСТИ:
1) Ничего не выдумывай: не изобретай котировки, новости, объемы, опционные стены, COT-данные, экономические релизы.
2) Если данных нет или они не live — явно помечай это как "fallback" или "unavailable" внутри соответствующего блока.
3) Четко разделяй:
   - Реальные рыночные наблюдения (если есть)
   - Прокси-оценки / сценарные допущения (если live-данных нет)
4) Не используй расплывчатые общие фразы без причинно-следственной связи.

ОБЯЗАТЕЛЬНЫЕ АНАЛИТИЧЕСКИЕ СЛОИ ДЛЯ КАЖДОЙ ПАРЫ:
- Smart Money + ICT: ликвидность, BOS/CHoCH, order blocks, FVG, premium/discount, sweep logic.
- Объемный анализ: где подтверждение импульса/абсорбции, где объем не подтверждает движение.
- Дивергенции: RSI/MACD/OBV или пояснение, почему дивергенция невалидна.
- Опционы: strikes, gamma/pain zones ИЛИ честная пометка unavailable.
- Волновой контекст: импульс/коррекция, альтернативный сценарий.
- Паттерны: графические + гармонические (если отсутствуют, так и напиши).
- Фундаментал: макро-драйверы (ставки, инфляция, рынок труда, risk-on/risk-off, геополитика, DXY/UST yields при наличии).
- Риск-контур: что ломает сценарий, где ловушки ликвидности.

ФОРМАТ ОТВЕТА (СТРОГО):
PAIR: EURUSD
Bias: Bullish | Bearish | Neutral
Situation: ...
Confirmation: ...
Invalidation: ...
Risks: ...

PAIR: GBPUSD
Bias: ...
Situation: ...
Confirmation: ...
Invalidation: ...
Risks: ...

PAIR: USDJPY
Bias: ...
Situation: ...
Confirmation: ...
Invalidation: ...
Risks: ...

PAIR: USDCHF
Bias: ...
Situation: ...
Confirmation: ...
Invalidation: ...
Risks: ...

В конце добавь:
Meta:
- Data Quality: live | fallback | unavailable (по факту)
- Confidence: 0-100
- Uncertainty Drivers: 3-6 пунктов

Текст: русский язык, профессиональный desk-style, компактно, но глубоко и предметно.
`;

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
      `(PAIR\\s*:\\s*)?${pair}\\s*[:\\-]?([\\s\\S]*?)(?=${nextPair ? `(PAIR\\s*:\\s*)?${nextPair}` : "$"})`,
      "i",
    );
    const match = safeReply.match(pairRegex);
    parsed[pair] = parseSectionBlock(match?.[2] || match?.[1] || "");
  });

  return parsed;
}

function getStatusMeta(statusValue) {
  const source = String(statusValue || "").toLowerCase();
  if (source.includes("live")) {
    return { label: "LIVE", className: "text-emerald-300 border-emerald-400/40 bg-emerald-500/10" };
  }
  if (source.includes("fallback")) {
    return { label: "FALLBACK", className: "text-amber-300 border-amber-400/40 bg-amber-500/10" };
  }
  return { label: "UNAVAILABLE", className: "text-rose-300 border-rose-400/40 bg-rose-500/10" };
}

function getBiasMeta(bias) {
  if (bias === "Bullish") {
    return {
      badge: "text-emerald-200 border-emerald-400/45 bg-emerald-500/10",
      card: "border-emerald-500/25 shadow-emerald-950/30",
    };
  }
  if (bias === "Bearish") {
    return {
      badge: "text-rose-200 border-rose-400/45 bg-rose-500/10",
      card: "border-rose-500/25 shadow-rose-950/30",
    };
  }
  return {
    badge: "text-amber-200 border-amber-400/40 bg-amber-500/10",
    card: "border-slate-600/65 shadow-slate-950/40",
  };
}

function normalizeConfluence(value) {
  const safe = toSafeObject(value);
  return {
    grade: typeof safe.grade === "string" ? safe.grade : "D",
    score: Number.isFinite(safe.total_score)
      ? safe.total_score
      : Number.isFinite(safe.score)
        ? safe.score
        : 0,
    confidenceDelta: Number.isFinite(safe.confidence_delta) ? safe.confidence_delta : 0,
    summary:
      typeof safe.summary_ru === "string"
        ? safe.summary_ru
        : typeof safe.summary === "string"
          ? safe.summary
          : "Confluence summary недоступен.",
    warnings: normalizeWarnings(safe.warnings),
    confirmations: normalizeWarnings(safe.confirmations),
    breakdown: {
      smc: Number.isFinite(safe?.breakdown?.smc)
        ? safe.breakdown.smc
        : Number.isFinite(safe?.breakdown?.smartMoney)
          ? safe.breakdown.smartMoney
          : 0,
      liquidity: Number.isFinite(safe?.breakdown?.liquidity) ? safe.breakdown.liquidity : 0,
      options: Number.isFinite(safe?.breakdown?.options) ? safe.breakdown.options : 0,
      volume: Number.isFinite(safe?.breakdown?.volume) ? safe.breakdown.volume : 0,
      sentiment: Number.isFinite(safe?.breakdown?.sentiment) ? safe.breakdown.sentiment : 0,
      risk: Number.isFinite(safe?.breakdown?.risk) ? safe.breakdown.risk : 0,
    },
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
  const [activeTab, setActiveTab] = useState(PAIRS[0]);
  const [copyState, setCopyState] = useState("idle");

  const parsed = useMemo(() => {
    const safe = toSafeObject(analysis);
    const reply = typeof safe.reply === "string" ? safe.reply : "";
    const source = typeof safe.source === "string" ? safe.source : "Не указан";
    const dataStatusRaw = safe.dataStatus ?? safe.data_status;
    const dataStatus = typeof dataStatusRaw === "string" ? dataStatusRaw : "unknown";
    const warnings = normalizeWarnings(safe.warnings);
    const pairs = parsePairsFromReply(reply);
    const confluence = normalizeConfluence(safe.confluenceAnalysis ?? safe.confluence ?? null);
    return { source, dataStatus, warnings, pairs, confluence, rawReply: reply };
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
      setCopyState("idle");
    } catch (requestError) {
      setAnalysis(null);
      setError(requestError instanceof Error ? requestError.message : "Не удалось получить разбор.");
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = async () => {
    try {
      const text = parsed.rawReply || "Разбор пока не получен.";
      await navigator.clipboard.writeText(text);
      setCopyState("copied");
      setTimeout(() => setCopyState("idle"), 1400);
    } catch {
      setCopyState("error");
      setTimeout(() => setCopyState("idle"), 1800);
    }
  };

  const activePairData = parsed.pairs[activeTab] || parseSectionBlock("");
  const activeBiasMeta = getBiasMeta(activePairData.bias);

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_#1e293b_0%,_#020617_55%,_#000_100%)] px-4 py-6 text-slate-100 md:px-8 md:py-8">
      <div className="mx-auto max-w-7xl space-y-5">
        <section className="rounded-3xl border border-white/10 bg-white/5 p-5 shadow-2xl shadow-cyan-950/20 backdrop-blur-xl md:p-7">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-[11px] uppercase tracking-[0.24em] text-cyan-300/90">AI Desk • Grok Analytics Engine</p>
              <h1 className="mt-2 text-2xl font-semibold tracking-tight md:text-4xl">Профессиональная FX-аналитика</h1>
              <p className="mt-2 max-w-3xl text-sm text-slate-300 md:text-base">
                SMC/ICT, объемы, дивергенции, опционы, волновой контекст, паттерны и фундаментальные драйверы. Каждый запрос
                принудительно требует уникальный и глубоко аргументированный разбор.
              </p>
            </div>
            <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${statusMeta.className}`}>
              {statusMeta.label}
            </span>
          </div>

          <div className="mt-5 flex flex-wrap gap-3">
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-xl border border-cyan-300/40 bg-cyan-400/15 px-4 py-2 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-400/25 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading ? "Grok анализирует рынок..." : "Обновить разбор"}
            </button>
            <button
              onClick={handleCopy}
              className="inline-flex items-center gap-2 rounded-xl border border-violet-300/40 bg-violet-400/15 px-4 py-2 text-sm font-semibold text-violet-100 transition hover:bg-violet-400/25"
            >
              {copyState === "copied" ? "Скопировано" : copyState === "error" ? "Ошибка копирования" : "Скопировать"}
            </button>
            <p className="self-center text-xs text-slate-400">Обновлено: {formatUpdatedAt(updatedAt)}</p>
          </div>

          {loading && (
            <div className="mt-4 overflow-hidden rounded-xl border border-cyan-300/20 bg-cyan-400/5 p-3">
              <div className="h-1.5 w-full animate-pulse rounded-full bg-gradient-to-r from-cyan-500/50 via-violet-400/50 to-cyan-500/50" />
              <p className="mt-2 text-xs text-cyan-100/80">Запрос отправлен. Формируется глубокий multi-layer отчёт...</p>
            </div>
          )}

          {error && <div className="mt-4 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-100">{error}</div>}
        </section>

        <section className="rounded-3xl border border-white/10 bg-white/5 p-5 backdrop-blur-xl md:p-6">
          <h2 className="text-lg font-semibold text-violet-100">Confluence Matrix</h2>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="rounded-xl border border-white/10 bg-black/20 p-3">
              <p className="text-xs text-slate-400">Score</p>
              <p className="text-2xl font-bold text-violet-200">{parsed.confluence.score}</p>
            </div>
            <div className="rounded-xl border border-white/10 bg-black/20 p-3">
              <p className="text-xs text-slate-400">Δ Confidence</p>
              <p className="text-2xl font-bold text-cyan-200">{parsed.confluence.confidenceDelta}</p>
            </div>
            <div className="rounded-xl border border-white/10 bg-black/20 p-3">
              <p className="text-xs text-slate-400">Grade</p>
              <p className="text-2xl font-bold text-emerald-200">{parsed.confluence.grade}</p>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-300 md:grid-cols-6">
            <p>SMC: {parsed.confluence.breakdown.smc}</p>
            <p>Liquidity: {parsed.confluence.breakdown.liquidity}</p>
            <p>Options: {parsed.confluence.breakdown.options}</p>
            <p>Volume: {parsed.confluence.breakdown.volume}</p>
            <p>Sentiment: {parsed.confluence.breakdown.sentiment}</p>
            <p>Risk: {parsed.confluence.breakdown.risk}</p>
          </div>
          <p className="mt-3 whitespace-pre-line text-sm text-slate-200">{parsed.confluence.summary}</p>
        </section>

        <section className="rounded-3xl border border-white/10 bg-white/5 p-5 backdrop-blur-xl md:p-6">
          <div className="flex flex-wrap gap-2">
            {PAIRS.map((pair) => {
              const pairData = parsed.pairs[pair] || parseSectionBlock("");
              const meta = getBiasMeta(pairData.bias);
              return (
                <button
                  key={pair}
                  onClick={() => setActiveTab(pair)}
                  className={`rounded-xl border px-3 py-2 text-sm font-semibold transition ${
                    activeTab === pair ? `${meta.badge} shadow-lg` : "border-white/15 bg-black/20 text-slate-300 hover:bg-black/35"
                  }`}
                >
                  {pair}
                </button>
              );
            })}
          </div>

          <article className={`mt-4 rounded-2xl border bg-black/25 p-4 shadow-xl md:p-5 ${activeBiasMeta.card}`}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[10px] uppercase tracking-[0.2em] text-slate-400">Активная пара</p>
                <h3 className="mt-1 text-2xl font-semibold">{activeTab}</h3>
              </div>
              <span className={`rounded-lg border px-3 py-1 text-xs font-semibold ${activeBiasMeta.badge}`}>{activePairData.bias}</span>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                <p className="text-[10px] uppercase tracking-[0.18em] text-slate-400">Situation</p>
                <p className="mt-1 text-sm leading-relaxed text-slate-200 whitespace-pre-line">{activePairData.situation}</p>
              </div>
              <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                <p className="text-[10px] uppercase tracking-[0.18em] text-slate-400">Confirmation</p>
                <p className="mt-1 text-sm leading-relaxed text-slate-200 whitespace-pre-line">{activePairData.confirmation}</p>
              </div>
              <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                <p className="text-[10px] uppercase tracking-[0.18em] text-slate-400">Invalidation</p>
                <p className="mt-1 text-sm leading-relaxed text-slate-200 whitespace-pre-line">{activePairData.invalidation}</p>
              </div>
              <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                <p className="text-[10px] uppercase tracking-[0.18em] text-slate-400">Risks</p>
                <p className="mt-1 text-sm leading-relaxed text-slate-200 whitespace-pre-line">{activePairData.risks}</p>
              </div>
            </div>

            <footer className="mt-4 rounded-xl border border-white/10 bg-black/25 p-3 text-xs text-slate-300">
              <p>Data status: {parsed.dataStatus || "unknown"}</p>
              <p className="mt-1">Warnings: {parsed.warnings.length ? parsed.warnings.join(" • ") : "нет"}</p>
              <p className="mt-1">Источник: {parsed.source}</p>
            </footer>
          </article>
        </section>

        <p className="text-center text-xs text-slate-500">Материал носит аналитический характер и не является инвестиционной рекомендацией.</p>
      </div>
    </div>
  );
}
