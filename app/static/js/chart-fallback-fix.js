(function () {
  const API_URL = "/api/ideas";

  async function getIdeas() {
    const res = await fetch(API_URL + "?t=" + Date.now());
    const data = await res.json();
    return data.ideas || [];
  }

  function getModalSymbol() {
    const title = document.getElementById("modal-title")?.textContent || "";
    const match = title.match(/[A-Z]{6}|XAUUSD/);
    return match ? match[0] : "";
  }

  function getCandles(idea) {
    return idea?.chartData?.candles || idea?.chart_data?.candles || [];
  }

  function pickIdea(ideas) {
    const symbol = getModalSymbol();
    return ideas.find(i => (i.symbol || i.pair) === symbol && getCandles(i).length > 0)
      || ideas.find(i => getCandles(i).length > 0);
  }

  function drawChart(candles, idea) {
    const host = document.getElementById("chart-host");
    const liveLayer = document.getElementById("chart-live-layer");
    const placeholder = document.getElementById("chart-placeholder");
    const snapshot = document.getElementById("chart-snapshot-layer");

    if (!host || candles.length < 5) return;

    host.innerHTML = "";
    host.style.display = "block";

    if (liveLayer) {
      liveLayer.classList.add("open");
      liveLayer.style.display = "block";
    }

    if (snapshot) {
      snapshot.classList.remove("open");
      snapshot.style.display = "none";
    }

    if (placeholder) {
      placeholder.classList.remove("open");
      placeholder.style.display = "none";
    }

    const canvas = document.createElement("canvas");
    host.appendChild(canvas);

    const w = host.clientWidth || 900;
    const h = host.clientHeight || 520;
    canvas.width = w;
    canvas.height = h;
    canvas.style.width = "100%";
    canvas.style.height = "100%";

    const ctx = canvas.getContext("2d");

    const highs = candles.map(c => Number(c.high));
    const lows = candles.map(c => Number(c.low));
    const max = Math.max(...highs);
    const min = Math.min(...lows);
    const pad = (max - min) * 0.08 || 0.001;

    const top = 30;
    const bottom = 35;
    const left = 55;
    const right = 20;
    const chartW = w - left - right;
    const chartH = h - top - bottom;

    const toY = price => {
      return top + ((max + pad - price) / ((max + pad) - (min - pad))) * chartH;
    };

    ctx.fillStyle = "#08111f";
    ctx.fillRect(0, 0, w, h);

    ctx.strokeStyle = "rgba(148,163,184,0.15)";
    ctx.fillStyle = "#94a3b8";
    ctx.font = "12px Arial";

    for (let i = 0; i <= 5; i++) {
      const y = top + (chartH / 5) * i;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(w - right, y);
      ctx.stroke();

      const price = max + pad - (((max + pad) - (min - pad)) / 5) * i;
      ctx.fillText(price.toFixed(5), 8, y + 4);
    }

    const step = chartW / candles.length;
    const bodyW = Math.max(3, Math.min(12, step * 0.6));

    candles.forEach((c, i) => {
      const open = Number(c.open);
      const high = Number(c.high);
      const low = Number(c.low);
      const close = Number(c.close);

      const x = left + i * step + step / 2;
      const yHigh = toY(high);
      const yLow = toY(low);
      const yOpen = toY(open);
      const yClose = toY(close);

      const bullish = close >= open;
      ctx.strokeStyle = "rgba(203,213,225,0.65)";
      ctx.beginPath();
      ctx.moveTo(x, yHigh);
      ctx.lineTo(x, yLow);
      ctx.stroke();

      ctx.fillStyle = bullish ? "#22c55e" : "#ef4444";
      ctx.fillRect(
        x - bodyW / 2,
        Math.min(yOpen, yClose),
        bodyW,
        Math.max(2, Math.abs(yClose - yOpen))
      );
    });

    function line(price, label, color) {
      const p = Number(price);
      if (!Number.isFinite(p)) return;

      const y = toY(p);
      ctx.strokeStyle = color;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(w - right, y);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = color;
      ctx.font = "bold 12px Arial";
      ctx.fillText(label + " " + p.toFixed(5), w - 130, y - 6);
    }

    line(idea.entry, "Вход", "#facc15");
    line(idea.stopLoss || idea.stop_loss, "SL", "#fb7185");
    line(idea.takeProfit || idea.take_profit, "TP", "#4ade80");

    ctx.fillStyle = "#dbeafe";
    ctx.font = "bold 14px Arial";
    ctx.fillText("Свечной fallback-график", left, 20);
  }

  async function fixChart() {
    const modal = document.getElementById("modal");
    if (!modal || !modal.classList.contains("open")) return;

    const ideas = await getIdeas();
    const idea = pickIdea(ideas);
    if (!idea) return;

    const candles = getCandles(idea);
    if (!candles.length) return;

    drawChart(candles, idea);
  }

  setInterval(fixChart, 1000);

  document.addEventListener("click", () => {
    setTimeout(fixChart, 300);
    setTimeout(fixChart, 1000);
  });

  window.addEventListener("load", () => {
    setTimeout(fixChart, 500);
    setTimeout(fixChart, 1500);
  });
})();
