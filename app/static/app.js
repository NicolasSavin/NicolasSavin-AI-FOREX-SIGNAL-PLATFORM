const ticker = document.getElementById("ticker");
const refreshBtn = document.getElementById("refreshBtn");
const symbolInput = document.getElementById("symbol");

const signalValue = document.getElementById("signalValue");
const signalReason = document.getElementById("signalReason");
const dataStatus = document.getElementById("dataStatus");
const realPrice = document.getElementById("realPrice");
const dayChange = document.getElementById("dayChange");
const sourceName = document.getElementById("sourceName");
const proxyList = document.getElementById("proxyList");

function normalizeSymbol(raw) {
  const clean = raw.trim().toUpperCase().replace(/\s+/g, "");
  if (clean.endsWith("USD") && clean.length === 6) {
    return clean.slice(0, 3);
  }
  return clean;
}

async function loadSignal() {
  const symbol = normalizeSymbol(symbolInput.value);
  if (!symbol) return;

  ticker.textContent = `Тикер: обновление ${symbol}/USD...`;

  const response = await fetch(`/api/signals/${symbol}`);
  const payload = await response.json();

  signalValue.textContent = `${payload.signal} (${Math.round(payload.confidence * 100)}%)`;
  signalReason.textContent = payload.reason_ru;

  dataStatus.textContent = payload.market.data_status === "real" ? "REAL" : "UNAVAILABLE";
  realPrice.textContent = payload.market.real_price ?? "нет данных";
  dayChange.textContent = payload.market.day_change_percent != null
    ? `${payload.market.day_change_percent}%`
    : "нет данных";
  sourceName.textContent = payload.market.source ?? "источник недоступен";

  proxyList.innerHTML = "";
  for (const metric of payload.market.proxy_metrics) {
    const item = document.createElement("li");
    item.textContent = `${metric.name}: ${metric.value} [${metric.label}]`;
    proxyList.appendChild(item);
  }

  ticker.textContent = `Тикер: ${symbol}/USD | Данные: ${dataStatus.textContent} | Обновлено ${new Date().toLocaleTimeString("ru-RU")}`;
}

refreshBtn.addEventListener("click", loadSignal);
window.addEventListener("load", loadSignal);
