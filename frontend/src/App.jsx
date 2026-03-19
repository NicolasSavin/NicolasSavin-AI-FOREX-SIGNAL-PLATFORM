import React, { useState } from "react";
import IdeaCard, { IdeaModal } from "./components/IdeaCard";

const mockIdeas = [
  {
    id: 1,
    symbol: "DXY",
    direction: "SHORT",
    confidence: 68,
    timeframe: "Intraday",
    summary: "После новостного импульса цена подходит к зоне ликвидности.",
    technical: "Bearish order block, liquidity sweep.",
    options: "Опционный интерес ниже текущей цены может выступать целью.",
    scenario: "При удержании под зоной предложения ожидается продолжение вниз.",
    targets: "104.20 / 103.80",
    invalidation: "Закрепление выше 105.00 ломает сценарий.",
    image: "/images/default-chart.png",
    tags: ["News", "Liquidity", "SMC", "Options"],
  },
];

function HomePage({ onOpenIdeas }) {
  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <div className="max-w-6xl mx-auto px-4 py-10">
        <h1 className="text-3xl font-bold mb-3">AI Forex Signal Platform</h1>
        <p className="text-slate-300 mb-6">
          Главная страница платформы. Лента идей не должна открываться здесь автоматически.
        </p>

        <button
          onClick={onOpenIdeas}
          className="px-4 py-2 rounded-xl bg-cyan-500 text-slate-950 font-semibold"
        >
          Открыть идеи
        </button>
      </div>
    </div>
  );
}

function IdeasPage({ onBack }) {
  const [selectedIdea, setSelectedIdea] = useState(null);

  return (
    <div className="min-h-screen bg-slate-950 p-4 md:p-6 text-white">
      <div className="max-w-7xl mx-auto">
        <div className="mb-6 flex items-center justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-widest text-cyan-400 mb-1">
              Актуальные идеи
            </p>
            <h1 className="text-2xl md:text-3xl font-bold">
              AI-разбор рыночных сценариев
            </h1>
          </div>

          <button
            onClick={onBack}
            className="px-4 py-2 rounded-xl bg-slate-800 text-white"
          >
            Назад
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {mockIdeas.map((idea) => (
            <IdeaCard key={idea.id} idea={idea} onOpen={setSelectedIdea} />
          ))}
        </div>

        <IdeaModal idea={selectedIdea} onClose={() => setSelectedIdea(null)} />
      </div>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState("home");

  if (page === "ideas") {
    return <IdeasPage onBack={() => setPage("home")} />;
  }

  return <HomePage onOpenIdeas={() => setPage("ideas")} />;
}
