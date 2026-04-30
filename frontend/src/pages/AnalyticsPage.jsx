import React, { useMemo, useState } from "react";

const PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"];
const ANALYTICS_PROMPT =
  "Объективно оцени текущую рыночную обстановку по 4 валютным парам: EURUSD, GBPUSD, USDJPY, USDCHF. Для каждой пары дай: 1) общий контекст, 2) нейтральный/bullish/bearish bias, 3) что подтверждает сценарий, 4) что отменяет сценарий, 5) основные риски. Не выдумывай котировки и новости. Если live-данных нет, прямо скажи об этом. Не давай гарантий прибыли.";

function normalizeWarnings(value) {
  if (Array.isArray(value)) {
    return value.filter((item) => typeof item === "string" && item.trim().length > 0);
  }
  if (typeof value === "string" && value.trim().length > 0) {
    return [value.trim()];
  }
  return [];
}

function toSafeObject(value) {
  return value && typeof value === "object" ? value : {};
}

export default function AnalyticsPage() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [analysis, setAnalysis] = useState(null);

  const parsed = useMemo(() => {
    const safe = toSafeObject(analysis);
    const reply = typeof safe.reply === "string" ? safe.reply : "";
    const source = typeof safe.source === "string" ? safe.source : "Не указан";
    const dataStatusRaw = safe.dataStatus ?? safe.data_status;
    const dataStatus = typeof dataStatusRaw === "string" ? dataStatusRaw : "unknown";
    const warnings = normalizeWarnings(safe.warnings);

    return { reply, source, dataStatus, warnings };
  }, [analysis]);

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
    } catch (requestError) {
      setAnalysis(null);
      setError(requestError instanceof Error ? requestError.message : "Не удалось получить разбор.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-white p-4 md:p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6 shadow-2xl shadow-cyan-950/20">
          <p className="text-xs uppercase tracking-[0.2em] text-cyan-400">Аналитика Grok/OpenRouter</p>
          <h1 className="mt-2 text-2xl md:text-3xl font-bold">Объективный обзор 4 валютных пар</h1>
          <p className="mt-3 text-sm text-slate-300">
            Это аналитический обзор, не инвестиционная рекомендация.
          </p>
          <button
            onClick={handleRefresh}
            disabled={loading}
            className="mt-5 rounded-lg bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "Обновление..." : "Обновить разбор"}
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {PAIRS.map((pair) => (
            <div key={pair} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 animate-pulse">
              <p className="text-sm text-slate-400">Пара</p>
              <p className="mt-1 text-xl font-semibold text-cyan-300">{pair}</p>
            </div>
          ))}
        </div>

        {loading && <p className="text-slate-300">Загружаем аналитический разбор...</p>}

        {error && (
          <div className="rounded-xl border border-rose-700/40 bg-rose-900/20 p-4 text-rose-200">
            <p className="font-semibold">Ошибка</p>
            <p className="text-sm mt-1">{error}</p>
          </div>
        )}

        {!loading && !error && !analysis && (
          <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 text-slate-300">
            Нажмите «Обновить разбор», чтобы получить текущую аналитику через backend endpoint /api/chat.
          </div>
        )}

        {!error && analysis && (
          <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
              <div className="rounded-lg bg-slate-800/60 p-3">
                <p className="text-slate-400">Источник</p>
                <p className="font-medium text-slate-100 break-all">{parsed.source}</p>
              </div>
              <div className="rounded-lg bg-slate-800/60 p-3">
                <p className="text-slate-400">Статус данных</p>
                <p className="font-medium text-slate-100">{parsed.dataStatus}</p>
              </div>
              <div className="rounded-lg bg-slate-800/60 p-3">
                <p className="text-slate-400">Warnings</p>
                <p className="font-medium text-slate-100">{parsed.warnings.length ? parsed.warnings.join("; ") : "Нет"}</p>
              </div>
            </div>

            <div className="rounded-lg bg-slate-800/40 p-4">
              <p className="text-slate-400 text-sm mb-2">Reply</p>
              <pre className="whitespace-pre-wrap text-sm text-slate-100 font-sans">{parsed.reply || "Пустой ответ от backend."}</pre>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
