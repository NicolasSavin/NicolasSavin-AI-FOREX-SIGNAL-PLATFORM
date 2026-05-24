import React from "react";

function resolveIdeaDescription(idea) {
  const fields = [
    idea?.unified_narrative,
    idea?.confluence_summary_ru,
    idea?.reason_ru,
    idea?.description_ru,
    idea?.short_scenario_ru,
    idea?.market_context?.confluence_summary_ru,
    idea?.market_context?.message,
  ];
  return fields.map((v) => String(v || "").trim()).find(Boolean) || "Описание идеи временно недоступно.";
}

function gradeClass(grade) {
  const value = String(grade || "C").toUpperCase();
  if (value === "A") return "bg-emerald-500/15 text-emerald-300 border-emerald-400/30";
  if (value === "B") return "bg-sky-500/15 text-sky-300 border-sky-400/30";
  return "bg-amber-500/15 text-amber-300 border-amber-400/30";
}

function directionClass(direction) {
  const value = String(direction || "WAIT").toUpperCase();
  if (value === "BUY") return "text-emerald-300 bg-emerald-500/15";
  if (value === "SELL") return "text-rose-300 bg-rose-500/15";
  return "text-slate-300 bg-slate-700/60";
}

function confidenceClass(confidence) {
  if (confidence >= 80) return "text-emerald-300";
  if (confidence >= 60) return "text-cyan-300";
  if (confidence >= 40) return "text-amber-300";
  return "text-rose-300";
}

export function IdeaCard({ idea, onOpen }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(idea)}
      className="group rounded-2xl border border-slate-800 bg-slate-900/80 p-4 text-left shadow-lg shadow-black/20 transition hover:-translate-y-0.5 hover:border-cyan-600/40"
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wider text-slate-400">{idea.symbol}</p>
          <p className="text-lg font-bold text-white">{idea.timeframe}</p>
        </div>
        <span className={`rounded-full px-3 py-1 text-xs font-semibold ${directionClass(idea.direction)}`}>{idea.direction}</span>
      </div>

      <img src={idea.image} alt={`${idea.symbol} chart`} className="mb-3 h-36 w-full rounded-xl object-cover" />

      <p className="mb-3 line-clamp-2 text-sm leading-6 text-slate-300">{resolveIdeaDescription(idea)}</p>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded-full border px-2 py-1 font-semibold ${gradeClass(idea.grade)}`}>Grade {idea.grade}</span>
        <span className={`font-semibold ${confidenceClass(Number(idea.confidence || 0))}`}>Confidence {Number(idea.confidence || 0)}%</span>
      </div>
    </button>
  );
}

export function IdeaModal({ idea, onClose }) {
  if (!idea) return null;
  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/80 p-4">
      <div className="mx-auto max-w-5xl rounded-2xl border border-slate-700 bg-slate-900 p-6">
        <div className="mb-5 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-2xl font-bold text-white">{idea.symbol} · {idea.direction}</h2>
            <p className="text-sm text-slate-400">{idea.timeframe} · Grade {idea.grade} · Confidence {idea.confidence}%</p>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg bg-slate-800 px-3 py-2 text-sm text-white">Закрыть</button>
        </div>

        <img src={idea.image} alt={`${idea.symbol} big chart`} className="mb-5 h-[380px] w-full rounded-xl object-cover" />

        <div className="mb-5 grid grid-cols-1 gap-3 md:grid-cols-3">
          <LevelCard label="Вход" value={idea.entry} />
          <LevelCard label="Stop Loss" value={idea.sl} />
          <LevelCard label="Take Profit" value={idea.tp} />
        </div>

        <Section title="Детальный сценарий" content={resolveIdeaDescription(idea)} />
        <Section title="Причина" content={idea?.reason_ru || "—"} />
        <Section title="Confluence" content={idea?.confluence_analysis?.summary_ru || idea?.confluence_summary_ru || "—"} />
      </div>
    </div>
  );
}

function LevelCard({ label, value }) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/70 p-3">
      <p className="text-xs uppercase tracking-wide text-slate-400">{label}</p>
      <p className="mt-1 text-lg font-semibold text-white">{value ?? "—"}</p>
    </div>
  );
}

function Section({ title, content }) {
  return (
    <section className="mb-4 rounded-xl border border-slate-800 bg-slate-950/70 p-4">
      <h3 className="mb-2 text-sm font-semibold text-slate-400">{title}</h3>
      <p className="text-sm leading-6 text-slate-200">{content}</p>
    </section>
  );
}

export default IdeaCard;
