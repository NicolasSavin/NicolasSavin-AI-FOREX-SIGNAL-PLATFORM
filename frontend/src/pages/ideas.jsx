import React, { useCallback, useEffect, useMemo, useState } from "react";
import IdeaCard, { IdeaModal } from "../components/IdeaCard";

const IDEAS_ENDPOINT = "/api/ideas/market";
const REFRESH_INTERVAL_MS = 25_000;

function toNumber(value, fallback = 0) {
  const num = Number(value);
  return Number.isFinite(num) ? num : fallback;
}

function normalizeIdea(rawIdea, index) {
  const symbol = rawIdea?.symbol || rawIdea?.pair || rawIdea?.instrument || "UNKNOWN";
  const direction = String(rawIdea?.direction || rawIdea?.action || rawIdea?.signal || "WAIT").toUpperCase();
  const grade = String(rawIdea?.prop_grade || rawIdea?.prop_signal_score?.grade || "C").toUpperCase();

  return {
    ...rawIdea,
    id: rawIdea?.id || rawIdea?.idea_id || `${symbol}-${rawIdea?.timeframe || "M15"}-${index}`,
    symbol,
    direction,
    action: direction,
    timeframe: rawIdea?.timeframe || rawIdea?.tf || "M15",
    confidence: toNumber(rawIdea?.confidence ?? rawIdea?.prop_score ?? rawIdea?.prop_signal_score?.score),
    grade,
    entry: rawIdea?.entry ?? rawIdea?.entry_price ?? null,
    sl: rawIdea?.sl ?? rawIdea?.stop_loss ?? null,
    tp: rawIdea?.tp ?? rawIdea?.take_profit ?? rawIdea?.target ?? null,
    image: rawIdea?.image || rawIdea?.chartImageUrl || rawIdea?.chart_image || "/images/default-chart.png",
    tags: rawIdea?.tags || [rawIdea?.prop_mode, rawIdea?.selected_zone_type].filter(Boolean),
  };
}

function parseIdeas(payload) {
  const source = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.ideas)
      ? payload.ideas
      : Array.isArray(payload?.signals)
        ? payload.signals
        : [];
  return source.map(normalizeIdea);
}

async function fetchIdeas() {
  const response = await fetch(IDEAS_ENDPOINT, {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: не удалось получить идеи`);
  }

  const payload = await response.json();
  return {
    ideas: parseIdeas(payload),
    updatedAt: payload?.updated_at_utc || payload?.updated_at || new Date().toISOString(),
  };
}

export default function IdeasPage() {
  const [ideas, setIdeas] = useState([]);
  const [selectedIdea, setSelectedIdea] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState(null);

  const [pairFilter, setPairFilter] = useState("ALL");
  const [timeframeFilter, setTimeframeFilter] = useState("ALL");
  const [gradeFilter, setGradeFilter] = useState("ALL");

  const loadIdeas = useCallback(async ({ silent = false } = {}) => {
    if (silent) setIsRefreshing(true);
    else setIsLoading(true);

    try {
      const { ideas: nextIdeas, updatedAt: nextUpdatedAt } = await fetchIdeas();
      setIdeas(nextIdeas);
      setUpdatedAt(nextUpdatedAt);
      setError("");
    } catch (loadError) {
      setError(loadError?.message || "Ошибка загрузки идей");
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadIdeas();
    const timer = window.setInterval(() => loadIdeas({ silent: true }), REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [loadIdeas]);

  const pairOptions = useMemo(() => ["ALL", ...new Set(ideas.map((item) => item.symbol).filter(Boolean))], [ideas]);
  const timeframeOptions = useMemo(() => ["ALL", ...new Set(ideas.map((item) => item.timeframe).filter(Boolean))], [ideas]);
  const gradeOptions = useMemo(() => ["ALL", ...new Set(ideas.map((item) => item.grade).filter(Boolean))], [ideas]);

  const filteredIdeas = useMemo(
    () =>
      ideas.filter((item) => {
        const pairMatch = pairFilter === "ALL" || item.symbol === pairFilter;
        const timeframeMatch = timeframeFilter === "ALL" || item.timeframe === timeframeFilter;
        const gradeMatch = gradeFilter === "ALL" || item.grade === gradeFilter;
        return pairMatch && timeframeMatch && gradeMatch;
      }),
    [ideas, pairFilter, timeframeFilter, gradeFilter],
  );

  return (
    <div className="min-h-screen bg-slate-950 p-4 text-white md:p-6">
      <div className="mx-auto max-w-7xl space-y-5">
        <header className="rounded-3xl border border-slate-800 bg-slate-900/70 p-5 backdrop-blur">
          <h1 className="text-2xl font-bold md:text-4xl">Торговые идеи</h1>
          <p className="mt-2 text-sm text-slate-300">Живые сигналы с API, автообновление каждые 25 секунд.</p>
          <p className="mt-2 text-xs text-slate-400">Последнее обновление: {updatedAt ? new Date(updatedAt).toLocaleString("ru-RU") : "—"}</p>
        </header>

        <section className="grid grid-cols-1 gap-3 rounded-2xl border border-slate-800 bg-slate-900/70 p-4 md:grid-cols-4">
          <FilterSelect label="Пара" value={pairFilter} onChange={setPairFilter} options={pairOptions} />
          <FilterSelect label="Таймфрейм" value={timeframeFilter} onChange={setTimeframeFilter} options={timeframeOptions} />
          <FilterSelect label="Grade" value={gradeFilter} onChange={setGradeFilter} options={gradeOptions} />
          <button
            type="button"
            onClick={() => loadIdeas({ silent: true })}
            disabled={isLoading || isRefreshing}
            className="h-11 self-end rounded-xl border border-cyan-500/40 bg-cyan-500/10 text-sm font-semibold text-cyan-200 transition hover:bg-cyan-500/20 disabled:opacity-50"
          >
            {isRefreshing ? "Обновление..." : "Обновить"}
          </button>
        </section>

        {error ? (
          <div className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-100">
            Ошибка: {error}
          </div>
        ) : null}

        {isLoading ? (
          <IdeasLoader />
        ) : filteredIdeas.length ? (
          <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {filteredIdeas.map((idea) => (
              <IdeaCard key={idea.id} idea={idea} onOpen={setSelectedIdea} />
            ))}
          </section>
        ) : (
          <EmptyState onReload={() => loadIdeas()} />
        )}
      </div>

      <IdeaModal idea={selectedIdea} onClose={() => setSelectedIdea(null)} />
    </div>
  );
}

function FilterSelect({ label, value, onChange, options }) {
  return (
    <label className="space-y-1 text-sm">
      <span className="text-slate-400">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-11 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 text-white outline-none focus:border-cyan-500"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option === "ALL" ? "Все" : option}
          </option>
        ))}
      </select>
    </label>
  );
}

function IdeasLoader() {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-80 animate-pulse rounded-2xl border border-slate-800 bg-slate-900/70 p-4" />
      ))}
    </div>
  );
}

function EmptyState({ onReload }) {
  return (
    <div className="rounded-3xl border border-slate-800 bg-slate-900/70 p-10 text-center">
      <p className="text-xl font-semibold">Идеи не найдены</p>
      <p className="mt-2 text-sm text-slate-400">Попробуйте изменить фильтры или запросить обновление данных.</p>
      <button type="button" onClick={onReload} className="mt-5 rounded-xl bg-cyan-400 px-5 py-2 font-semibold text-slate-950">
        Загрузить снова
      </button>
    </div>
  );
}
