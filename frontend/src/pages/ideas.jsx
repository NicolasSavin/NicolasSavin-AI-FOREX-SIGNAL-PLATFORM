import React, { useState } from "react";
import IdeaCard, { IdeaModal } from "../components/IdeaCard";

const mockIdeas = [
  {
    id: 1,
    symbol: "DXY",
    direction: "SHORT",
    confidence: 68,
    timeframe: "Intraday",
    summary:
      "После новостного импульса цена подходит к зоне ликвидности. При отклонении от области предложения возможна реакция вниз.",
    technical:
      "Bearish order block, liquidity sweep и слабая локальная структура. Подтверждение — отказ от роста в зоне предложения.",
    options:
      "Опционный интерес ниже текущей цены может выступать целевой зоной. Возможен сценарий притяжения цены к страйкам.",
    scenario:
      "При слабой реакции покупателей и удержании под зоной предложения ожидается продолжение вниз.",
    targets: "104.20 / 103.80",
    invalidation: "Закрепление выше 105.00 ломает сценарий.",
    image: "/images/chart-dxy.png",
    tags: ["News", "Liquidity", "SMC", "Options"],
  },
  {
    id: 2,
    symbol: "EURUSD",
    direction: "LONG",
    confidence: 74,
    timeframe: "1-2 days",
    summary:
      "Цена сняла ликвидность снизу и вернулась в discount-зону. При подтверждении структуры возможен рост к ближайшим целям.",
    technical:
      "Liquidity sweep, bullish FVG и возврат в order block. Подтверждение — формирование higher low.",
    options:
      "Крупные страйки выше могут служить магнитом для цены. Также возможен эффект хеджирования фьючерсов через опционы.",
    scenario: "Удержание над зоной спроса повышает шанс движения вверх.",
    targets: "1.0920 / 1.0960",
    invalidation: "Потеря зоны спроса и закрепление ниже 1.0840.",
    image: "/images/chart-eurusd.png",
    tags: ["FVG", "Order Block", "Liquidity", "Options"],
  },
];

export default function IdeasPage() {
  const [selectedIdea, setSelectedIdea] = useState(null);
  const ideas = mockIdeas;

  return (
    <div className="min-h-screen bg-slate-950 p-4 md:p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-6">
          <p className="text-xs uppercase tracking-widest text-cyan-400 mb-1">
            Актуальные идеи
          </p>
          <h1 className="text-2xl md:text-3xl font-bold text-white">
            AI-разбор рыночных сценариев
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            В ленте показываются только компактные превью. Полный разбор
            открывается по клику.
          </p>
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
