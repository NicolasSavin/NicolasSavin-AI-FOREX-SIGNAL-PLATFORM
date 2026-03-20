import React, { useState } from "react";
import IdeaCard, { IdeaModal } from "../components/IdeaCard";

const mockIdeas = [
  {
    id: 1,
    symbol: "DXY",
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

export default function IdeasPage() {
  const [selectedIdea, setSelectedIdea] = useState(null);

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
