import React, { useEffect, useMemo, useRef, useState } from "react";
import IdeaCard, { IdeaModal } from "../components/IdeaCard";

const VOICE_ENABLED_KEY = "ideas_voice_enabled";
const VOICE_SPEAK_INITIAL_KEY = "ideas_voice_speak_initial";
const VOICE_THROTTLE_MS = 20_000;

const mockIdeas = [
  {
    id: 1,
    symbol: "DXY",
    action: "SELL",
    status: "ACTIVE",
    entry: "104.70",
    sl: "105.00",
    tp: "104.20",
    direction: "SHORT",
    confidence: 68,
    timeframe: "Intraday",
    summary: "После новостного импульса цена подходит к зоне ликвидности.",
    technical: "Bearish order block, liquidity sweep.",
    options: "",
    scenario: "При удержании под зоной предложения ожидается продолжение вниз.",
    targets: "104.20 / 103.80",
    invalidation: "Закрепление выше 105.00 ломает сценарий.",
    image: "/images/default-chart.png",
    tags: ["News", "Liquidity", "SMC"],
  },
];

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

  if (status === "ACTIVE") return 3;
  if (action === "BUY" || action === "SELL") return 2;
  return 1;
}

export default function IdeasPage() {
  const [selectedIdea, setSelectedIdea] = useState(null);
  const [ideas, setIdeas] = useState(mockIdeas);
  const [voiceEnabled, setVoiceEnabled] = useState(() => localStorage.getItem(VOICE_ENABLED_KEY) === "true");
  const prevIdeasRef = useRef(new Map());
  const lastSpeechAtRef = useRef(0);

  useEffect(() => {
    localStorage.setItem(VOICE_ENABLED_KEY, voiceEnabled ? "true" : "false");
  }, [voiceEnabled]);

  useEffect(() => {
    setIdeas(mockIdeas);
  }, []);

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
    <div className="min-h-screen bg-slate-950 p-4 md:p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-6">
          <p className="text-xs uppercase tracking-widest text-cyan-400 mb-1">Актуальные идеи</p>
          <h1 className="text-2xl md:text-3xl font-bold text-white">AI-разбор рыночных сценариев</h1>
          <div className="mt-4 flex flex-col gap-2 text-sm text-slate-300">
            <label className="inline-flex items-center gap-2 cursor-pointer w-fit">
              <input
                type="checkbox"
                checked={voiceEnabled}
                onChange={(event) => setVoiceEnabled(event.target.checked)}
                className="accent-cyan-400"
              />
              <span>🔊 Озвучивать изменения</span>
            </label>
            <p>Голос: {voiceStatusText}</p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {ideas.map((idea) => (
            <IdeaCard key={idea.id} idea={idea} onOpen={setSelectedIdea} />
          ))}
        </div>

        <IdeaModal idea={selectedIdea} onClose={() => setSelectedIdea(null)} />
      </div>
    </div>
  );
}
