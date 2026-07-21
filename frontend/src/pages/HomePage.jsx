import React from "react";

export default function HomePage() {
  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <div className="max-w-6xl mx-auto px-4 py-10">
        <h1 className="text-3xl font-bold mb-3">AI Forex Signal Platform</h1>
        <p className="text-slate-300">
          Главная страница платформы.
        </p>
        <details className="mt-6 rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
          <summary className="cursor-pointer text-lg font-semibold text-cyan-200">Требования к местам</summary>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <label className="text-sm text-slate-300">Класс вагона
              <select className="mt-1 w-full rounded-lg bg-slate-950 border border-slate-700 p-2">
                <option>Любой</option><option>Купе</option><option>СВ</option><option>Плацкарт</option><option>Сидячий</option>
              </select>
            </label>
            <label className="text-sm text-slate-300">Расположение
              <select className="mt-1 w-full rounded-lg bg-slate-950 border border-slate-700 p-2">
                <option>Любые</option><option>Только нижние</option><option>Только верхние</option><option>Желательно нижние</option><option>Желательно верхние</option>
              </select>
            </label>
            {['Все в одном купе','Купе без посторонних','Места рядом','Все в одном вагоне','Исключить боковые места','Допускается разделить группу'].map((label) => (
              <label key={label} className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" className="accent-cyan-400" />{label}</label>
            ))}
            <label className="text-sm text-slate-300">Тип купе
              <select className="mt-1 w-full rounded-lg bg-slate-950 border border-slate-700 p-2">
                <option>Любое</option><option>Мужское</option><option>Женское</option><option>Смешанное</option>
              </select>
            </label>
            <label className="text-sm text-slate-300">Максимум купе
              <input type="number" min="1" className="mt-1 w-full rounded-lg bg-slate-950 border border-slate-700 p-2" />
            </label>
          </div>
          <ul className="mt-4 space-y-1 text-xs text-slate-400">
            <li>«Одно купе» не означает выкуп целого купе.</li>
            <li>«Купе без посторонних» требует доступности всех мест купе.</li>
            <li>Проверка возможна только при наличии данных от билетного провайдера.</li>
          </ul>
        </details>
        <div className="mt-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">
          Конкретные места не проверены. Расписание актуальное. Данные по конкретным местам отсутствуют.
        </div>
      </div>
    </div>
  );
}
