import React from "react";

function resolveIdeaDescription(idea) {
  const candidates = [
    idea?.confluence_summary_ru,
    idea?.reason_ru,
    idea?.description_ru,
    idea?.market_context?.confluence_summary_ru,
    idea?.market_context?.message,
    "Описание идеи временно недоступно.",
  ];
  return candidates.map((item) => String(item || "").trim()).find((item) => item) || "Описание идеи временно недоступно.";
}

function buildAvailableTextFields(idea) {
  return {
    confluence_summary_ru: Boolean(String(idea?.confluence_summary_ru || "").trim()),
    reason_ru: Boolean(String(idea?.reason_ru || "").trim()),
    description_ru: Boolean(String(idea?.description_ru || "").trim()),
    market_context_confluence_summary_ru: Boolean(String(idea?.market_context?.confluence_summary_ru || "").trim()),
    market_context_message: Boolean(String(idea?.market_context?.message || "").trim()),
  };
}

export function IdeaCard({ idea, onOpen }) {
  const visibleDescription = resolveIdeaDescription(idea);
  const availableTextFields = buildAvailableTextFields(idea);
  if (!Object.values(availableTextFields).some(Boolean)) {
    console.warn("ideas_missing_text_fields", {
      idea_id: idea?.idea_id || idea?.id || null,
      symbol: idea?.symbol || null,
      availableTextFields,
    });
  }
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
        {visibleDescription}
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
  const availableTextFields = buildAvailableTextFields(idea);
  const aiFailed = Boolean(idea?.grok_analysis_failed || idea?.grok_failed || idea?.ai_analysis_failed);

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

        {aiFailed ? <Section title="AI" content="AI-пояснение временно недоступно" /> : null}
        <Section title="Описание" content={idea.description_ru || "—"} />
        <Section title="Причина" content={idea.reason_ru || "—"} />
        <Section title="Confluence summary" content={idea?.confluence_analysis?.summary_ru || idea?.confluence_summary_ru || "—"} />
        <Section title="Подтверждения" content={(idea?.confluence_analysis?.confirmations || idea?.confluence_confirmations || []).join(", ") || "—"} />
        <Section title="Риски / предупреждения" content={(idea?.confluence_analysis?.warnings || idea?.confluence_warnings || []).join(", ") || "—"} />
        {idea?.options_analysis?.summary_ru || idea?.options_summary_ru ? (
          <Section title="Options analysis" content={idea?.options_analysis?.summary_ru || idea?.options_summary_ru} />
        ) : null}
        {!Object.values(availableTextFields).some(Boolean) ? <Section title="Описание" content="Описание идеи временно недоступно." /> : null}
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
