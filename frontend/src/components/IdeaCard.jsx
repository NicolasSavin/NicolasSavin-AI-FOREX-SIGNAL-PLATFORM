import React from "react";

export function IdeaCard({ idea, onOpen }) {
  return (
    <div
      className="bg-slate-900 rounded-2xl p-4 border border-slate-800 shadow-md hover:shadow-lg transition cursor-pointer"
      onClick={() => onOpen(idea)}
    >
      <div className="flex items-start justify-between gap-3 mb-2">
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-400">
            {idea.symbol}
          </div>
          <div className="text-sm font-semibold text-white">
            {idea.direction} · {idea.timeframe} · {idea.confidence}%
          </div>
        </div>

        <button
          type="button"
          className="text-xs bg-yellow-500 text-black px-3 py-1 rounded-full font-medium"
        >
          Watch
        </button>
      </div>

      <p className="text-sm leading-snug text-slate-300 line-clamp-2 mb-3">
        {idea.summary}
      </p>

      <img
        src={idea.image}
        alt={`${idea.symbol} preview`}
        className="w-full h-32 object-cover rounded-xl mb-3"
      />

      <div className="flex gap-2 flex-wrap">
        {(idea.tags || []).map((tag) => (
          <span
            key={tag}
            className="text-xs bg-slate-800 text-slate-200 px-2 py-1 rounded-md"
          >
            {tag}
          </span>
        ))}
      </div>
    </div>
  );
}

export function IdeaModal({ idea, onClose }) {
  if (!idea) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 p-4 overflow-y-auto">
      <div className="max-w-4xl mx-auto bg-slate-900 rounded-2xl p-6 border border-slate-800">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div>
            <h2 className="text-xl font-bold text-white">
              {idea.symbol} — {idea.direction}
            </h2>
            <p className="text-sm text-slate-400">
              {idea.timeframe} · Confidence {idea.confidence}%
            </p>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="px-3 py-2 rounded-lg bg-slate-800 text-white text-sm"
          >
            Close
          </button>
        </div>

        <img
          src={idea.image}
          alt={`${idea.symbol} full analysis`}
          className="w-full rounded-xl mb-5"
        />

        <Section title="Summary" content={idea.summary} />
        <Section title="Technical Logic" content={idea.technical} />
        <Section title="Options Analysis" content={idea.options} />
        <Section title="Scenario" content={idea.scenario} />
        <Section title="Targets" content={idea.targets} />
        <Section title="Invalidation" content={idea.invalidation} />
      </div>
    </div>
  );
}

function Section({ title, content }) {
  return (
    <div className="mb-4">
      <h3 className="text-sm font-semibold text-slate-400 mb-1">{title}</h3>
      <p className="text-sm leading-6 text-slate-200">{content}</p>
    </div>
  );
}

export default IdeaCard;
