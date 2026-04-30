import React, { useEffect, useMemo, useState } from "react";
import HomePage from "./pages/HomePage";
import IdeasPage from "./pages/ideas";
import AnalyticsPage from "./pages/AnalyticsPage";

const NAV_ITEMS = [
  { path: "/", label: "Главная" },
  { path: "/ideas", label: "Идеи" },
  { path: "/analytics", label: "Аналитика" },
];

function getCurrentPath() {
  if (typeof window === "undefined") return "/";
  return window.location.pathname || "/";
}

export default function App() {
  const [path, setPath] = useState(getCurrentPath);

  useEffect(() => {
    const onPopState = () => setPath(getCurrentPath());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = (nextPath) => {
    if (nextPath === path) return;
    window.history.pushState({}, "", nextPath);
    setPath(nextPath);
  };

  const page = useMemo(() => {
    if (path === "/ideas") return <IdeasPage />;
    if (path === "/analytics") return <AnalyticsPage />;
    return <HomePage />;
  }, [path]);

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <header className="border-b border-slate-800 bg-slate-950/90 backdrop-blur sticky top-0 z-20">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
          <p className="font-semibold text-cyan-300">AI Forex Signal Platform</p>
          <nav className="flex items-center gap-2">
            {NAV_ITEMS.map((item) => {
              const isActive = path === item.path;
              return (
                <button
                  key={item.path}
                  onClick={() => navigate(item.path)}
                  className={`rounded-md px-3 py-2 text-sm transition ${
                    isActive ? "bg-cyan-500 text-slate-950 font-semibold" : "text-slate-300 hover:bg-slate-800"
                  }`}
                >
                  {item.label}
                </button>
              );
            })}
          </nav>
        </div>
      </header>
      <main>{page}</main>
    </div>
  );
}
