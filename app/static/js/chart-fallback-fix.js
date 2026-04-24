(function () {
  const API_URL = "/api/ideas";

  async function loadIdeas() {
    const res = await fetch(API_URL);
    const data = await res.json();
    return data.ideas || [];
  }

  function getCandles(idea) {
    return idea?.chartData?.candles || [];
  }

  function drawSimpleChart(container, candles) {
    container.innerHTML = "";
    const canvas = document.createElement("canvas");
    canvas.width = 800;
    canvas.height = 400;
    container.appendChild(canvas);

    const ctx = canvas.getContext("2d");

    const max = Math.max(...candles.map(c => c.high));
    const min = Math.min(...candles.map(c => c.low));

    candles.forEach((c, i) => {
      const x = i * 10 + 50;

      const yHigh = 400 - ((c.high - min) / (max - min)) * 350;
      const yLow = 400 - ((c.low - min) / (max - min)) * 350;
      const yOpen = 400 - ((c.open - min) / (max - min)) * 350;
      const yClose = 400 - ((c.close - min) / (max - min)) * 350;

      ctx.beginPath();
      ctx.moveTo(x, yHigh);
      ctx.lineTo(x, yLow);
      ctx.strokeStyle = "#aaa";
      ctx.stroke();

      ctx.fillStyle = c.close > c.open ? "green" : "red";
      ctx.fillRect(x - 2, Math.min(yOpen, yClose), 4, Math.abs(yOpen - yClose));
    });
  }

  async function fixChart() {
    const modal = document.getElementById("modal");
    if (!modal || !modal.classList.contains("open")) return;

    const container = document.getElementById("chart-host");
    if (!container) return;

    const ideas = await loadIdeas();
    if (!ideas.length) return;

    const idea = ideas[0];
    const candles = getCandles(idea);

    if (candles.length < 10) return;

    drawSimpleChart(container, candles);

    const placeholder = document.getElementById("chart-placeholder");
    if (placeholder) placeholder.style.display = "none";
  }

  setInterval(fixChart, 1500);
})();
