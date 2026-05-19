import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import IdeaCard, { IdeaModal } from "../components/IdeaCard";

const VOICE_ENABLED_KEY = "ideas_voice_enabled";
const VOICE_SPEAK_INITIAL_KEY = "ideas_voice_speak_initial";
const VOICE_THROTTLE_MS = 20_000;
const IDEAS_REFRESH_MS = 20_000;

const IDEAS_ENDPOINTS = ["/api/ideas/market", "/ideas/market", "/api/ideas"];

function normalizeIdea(rawIdea, index) {
  const propScore = rawIdea?.prop_signal_score || {};
  const advisor = rawIdea?.advisor_signal || {};
  const symbol = rawIdea?.symbol || rawIdea?.pair || rawIdea?.instrument || "UNKNOWN";
  const action = rawIdea?.action || rawIdea?.signal || advisor?.action || rawIdea?.direction || "WAIT";
  const confidence = Number(rawIdea?.confidence ?? rawIdea?.prop_score ?? propScore?.score ?? 0);

  return {
    ...rawIdea,
    id: rawIdea?.id || rawIdea?.idea_id || `${symbol}-${rawIdea?.timeframe || rawIdea?.tf || "TF"}-${index}`,
    symbol,
    action,
    direction: rawIdea?.direction || action,
    status: rawIdea?.status || (rawIdea?.trade_permission || advisor?.allowed ? "ACTIVE" : "WATCHLIST"),
    entry: rawIdea?.entry ?? rawIdea?.entry_price ?? advisor?.entry ?? "",
    sl: rawIdea?.sl ?? rawIdea?.stop_loss ?? advisor?.sl ?? "",
    tp: rawIdea?.tp ?? rawIdea?.take_profit ?? rawIdea?.target ?? advisor?.tp ?? "",
    confidence: Number.isFinite(confidence) ? confidence : 0,
    timeframe: rawIdea?.timeframe || rawIdea?.tf || "M15",
    image: rawIdea?.image || rawIdea?.chartImageUrl || rawIdea?.chart_image || "/images/default-chart.png",
    tags: rawIdea?.tags || [rawIdea?.prop_grade, rawIdea?.prop_mode, rawIdea?.selected_zone_type].filter(Boolean),
  };
}

function extractIdeas(payload) {
  const list = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.ideas)
      ? payload.ideas
      : Array.isArray(payload?.signals)
        ? payload.signals
        : [];

  return list.map(normalizeIdea);
}

async function requestJson(url, signal) {
  const response = await fetch(url, {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal,
  });

  if (!response.ok) {
    throw new Error(`${url}: HTTP ${response.status}`);
  }

  return response.json();
}

async function fetchIdeasPayload(signal) {
  const errors = [];

  for (const endpoint of IDEAS_ENDPOINTS) {
    try {
      const payload = await requestJson(endpoint, signal);
      return { payload, endpoint };
    } catch (error) {
      if (error?.name === "AbortError") throw error;
      errors.push(error?.message || String(error));
    }
  }

  throw new Error(errors.join("; ") || "Не удалось загрузить идеи");
}

function speakIdeaChange(idea) {
  if (typeof window === "undefined" || !window.speechSynthesis) return;
  if (localStorage.getItem(VOICE_ENABLED_KEY) !== "true") return;

  const symbol = idea.symbol || "инструмент";
  const action = idea.action || idea.signal || "WAIT";
  const status = idea.status || "";
  const entry = idea.entry ? `Вход ${idea.entry}.` : "";
  const sl = idea.sl ? `Стоп ${idea.sl}.` : "";
  const tp = idea.tp ? `Тейк ${idea.tp}.` : "";

  const text = `Новая идея по ${symbol}. Сигнал ${action}. ${status}. ${entry} ${sl} ${tp}`;

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "ru-RU";
  utterance.rate = 1;
  utterance.pitch = 1;

  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
}

function ideaChangeKey(idea) {
  return [
    idea.symbol || "",
    idea.action || idea.signal || "",
    idea.status || "",
    idea.entry || "",
    idea.sl || "",
    idea.tp || "",
    idea.confidence || "",
  ].join("|");
}

function ideaPriority(idea) {
  const status = (idea.status || "").toUpperCase();
  const action = (idea.action || idea.signal || "WAIT").toUpperCase();
  const grade = String(idea.prop_grade || idea?.prop_signal_score?.grade || "").toUpperCase();

  if (status === "ACTIVE" || grade === "A") return 3;
  if (action === "BUY" || action === "SELL") return 2;
  return 1;
}

function formatUpdatedAt(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("ru-RU");
}

export default function IdeasPage() {
  const [selectedIdea, setSelectedIdea] = useState(null);
  const [ideas, setIdeas] = useState([]);
  const [voiceEnabled, setVoiceEnabled] = useState(() => localStorage.getItem(VOICE_ENABLED_KEY) === "true");
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState(null);
  const [activeEndpoint, setActiveEndpoint] = useState(null);
  const prevIdeasRef = useRef(new Map());
  const lastSpeechAtRef = useRef(0);

  const loadIdeas = useCallback(async ({ silent = false } = {}) => {
    const controller = new AbortController();

    if (silent) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }

    try {
      const { payload, endpoint } = await fetchIdeasPayload(controller.signal);
      const normalizedIdeas = extractIdeas(payload);

      setIdeas(normalizedIdeas);
      setActiveEndpoint(endpoint);
      setLastUpdatedAt(payload?.updated_at_utc || payload?.updated_at || new Date().toISOString());
      setError(null);
    } catch (loadError) {
      if (loadError?.name !== "AbortError") {
        setError(loadError?.message || "Не удалось загрузить идеи");
      }
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }

    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    async function initialLoad() {
      setIsLoading(true);
      try {
        const { payload, endpoint } = await fetchIdeasPayload(controller.signal);
        setIdeas(extractIdeas(payload));
        setActiveEndpoint(endpoint);
        setLastUpdatedAt(payload?.updated_at_utc || payload?.updated_at || new Date().toISOString());
        setError(null);
      } catch (loadError) {
        if (loadError?.name !== "AbortError") {
          setError(loadError?.message || "Не удалось загрузить идеи");
        }
      } finally {
        setIsLoading(false);
      }
    }

    initialLoad();

    const intervalId = window.setInterval(() => {
      loadIdeas({ silent: true });
    }, IDEAS_REFRESH_MS);

    return () => {
      controller.abort();
      window.clearInterval(intervalId);
    };
  }, [loadIdeas]);

  useEffect(() => {
    localStorage.setItem(VOICE_ENABLED_KEY, voiceEnabled ? "true" : "false");
  }, [voiceEnabled]);

  useEffect(() => {
    const currentMap = new Map(ideas.map((idea) => [idea.id || idea.symbol, ideaChangeKey(idea)]));
    const hadPreviousIdeas = prevIdeasRef.current.size > 0;
    const allowInitialSpeak = localStorage.getItem(VOICE_SPEAK_INITIAL_KEY) === "true";

    if (!hadPreviousIdeas) {
      prevIdeasRef.current = currentMap;
      if (!allowInitialSpeak) return;
    }

    const changedIdeas = ideas.filter((idea) => {
      const key = idea.id || idea.symbol;
      return prevIdeasRef.current.get(key) !== ideaChangeKey(idea);
    });

    prevIdeasRef.current = currentMap;

    if (!changedIdeas.length) return;

    const now = Date.now();
    if (now - lastSpeechAtRef.current < VOICE_THROTTLE_MS) return;

    const mostImportant = [...changedIdeas].sort((a, b) => ideaPriority(b) - ideaPriority(a))[0];
    speakIdeaChange(mostImportant);
    lastSpeechAtRef.current = now;
  }, [ideas]);

  const voiceStatusText = useMemo(() => (voiceEnabled ? "включён" : "выключен"), [voiceEnabled]);

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,rgba(14,165,233,0.16),transparent_34%),linear-gradient(135deg,#020617,#0f172a_55%,#020617)] p-4 text-white md:p-6">
      <div className="mx-auto max-w-7xl">
        <div className="mb-6 rounded-3xl border border-white/10 bg-white/[0.04] p-5 shadow-2xl shadow-black/30 backdrop-blur md:p-6">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-[0.28em] text-cyan-300">Prop ideas desk</p>
              <h1 className="text-2xl font-bold tracking-tight text-white md:text-4xl">AI-разбор рыночных сценариев</h1>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300">
                Реальные идеи из API, автообновление каждые 20 секунд, голосовые уведомления и сохранение текущей логики карточек.
              </p>
            </div>

            <div className="flex flex-col gap-3 rounded-2xl border border-slate-700/80 bg-slate-950/55 p-4 text-sm text-slate-300">
              <label className="inline-flex w-fit cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  checked={voiceEnabled}
                  onChange={(event) => setVoiceEnabled(event.target.checked)}
                  className="h-4 w-4 accent-cyan-400"
                />
                <span>🔊 Озвучивать изменения</span>
              </label>
              <div className="flex flex-wrap gap-2 text-xs">
                <span className="rounded-full border border-slate-700 px-3 py-1">Голос: {voiceStatusText}</span>
                <span className="rounded-full border border-slate-700 px-3 py-1">Источник: {activeEndpoint || "—"}</span>
                <span className="rounded-full border border-slate-700 px-3 py-1">Обновлено: {formatUpdatedAt(lastUpdatedAt)}</span>
              </div>
            </div>
          </div>
        </div>

        {error ? (
          <div className="mb-4 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-100">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <span>Ошибка загрузки идей: {error}</span>
              <button
                type="button"
                onClick={() => loadIdeas()}
                className="rounded-xl bg-rose-400 px-4 py-2 text-xs font-bold text-rose-950 transition hover:bg-rose-300"
              >
                Повторить
              </button>
            </div>
          </div>
        ) : null}

        <div className="mb-4 flex items-center justify-between gap-3 text-sm text-slate-400">
          <span>{isRefreshing ? "Обновляю идеи…" : `Идей загружено: ${ideas.length}`}</span>
          <button
            type="button"
            onClick={() => loadIdeas({ silent: true })}
            disabled={isRefreshing || isLoading}
            className="rounded-xl border border-cyan-400/30 bg-cyan-400/10 px-4 py-2 text-xs font-bold text-cyan-100 transition hover:bg-cyan-400/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Обновить сейчас
          </button>
        </div>

        {isLoading ? <IdeasLoader /> : null}

        {!isLoading && !ideas.length ? <EmptyState onRetry={() => loadIdeas()} /> : null}

        {!isLoading && ideas.length ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {ideas.map((idea) => (
              <IdeaCard key={idea.id} idea={idea} onOpen={setSelectedIdea} />
            ))}
          </div>
        ) : null}

        <IdeaModal idea={selectedIdea} onClose={() => setSelectedIdea(null)} />
      </div>
    </div>
  );
}

function IdeasLoader() {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, index) => (
        <div key={index} className="h-80 animate-pulse rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="mb-4 h-4 w-24 rounded bg-slate-800" />
          <div className="mb-3 h-6 w-40 rounded bg-slate-800" />
          <div className="mb-2 h-3 w-full rounded bg-slate-800" />
          <div className="mb-6 h-3 w-3/4 rounded bg-slate-800" />
          <div className="h-36 rounded-xl bg-slate-800" />
        </div>
      ))}
    </div>
  );
}

function EmptyState({ onRetry }) {
  return (
    <div className="rounded-3xl border border-slate-800 bg-slate-900/70 p-8 text-center shadow-xl">
      <p className="text-lg font-semibold text-white">Идей пока нет</p>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-slate-400">
        API ответил успешно, но не вернул активные идеи. Проверь генератор сигналов или обнови страницу через несколько секунд.
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-5 rounded-xl bg-cyan-400 px-5 py-2 text-sm font-bold text-slate-950 transition hover:bg-cyan-300"
      >
        Проверить снова
      </button>
    </div>
  );
}
