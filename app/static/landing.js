const quoteTrack = document.getElementById('quoteTrack');
const tickerUpdatedAt = document.getElementById('tickerUpdatedAt');
const quoteDisclaimer = document.getElementById('quoteDisclaimer');

function formatPrice(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return Number(value).toFixed(5).replace(/0+$/, '').replace(/\.$/, '');
}

function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return `${new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short', timeZone: 'UTC' }).format(date)} UTC`;
}

function buildMarqueeItems(signals) {
  if (!Array.isArray(signals) || !signals.length) {
    return ['Котировки временно недоступны'];
  }

  return signals.map((signal) => {
    const symbol = signal.symbol || '—';
    const price = formatPrice(signal.current_price ?? signal.price);
    const sourceLabel = signal.data_status === 'real' ? 'real' : signal.data_status === 'delayed' ? 'delayed' : 'proxy';
    return `${symbol} ${price} (${sourceLabel})`;
  });
}

async function loadQuotes() {
  try {
    const response = await fetch('/api/signals');
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const payload = await response.json();
    const signals = Array.isArray(payload.signals) ? payload.signals : [];
    const items = buildMarqueeItems(signals);
    const marqueeText = `✦ ${items.join('   ✦   ')} ✦`;

    quoteTrack.textContent = `${marqueeText}   ${marqueeText}`;
    tickerUpdatedAt.textContent = `Обновление: ${formatDateTime(payload.updated_at_utc)}`;

    const hasProxy = signals.some((signal) => !['real', 'delayed'].includes(String(signal.data_status || '')));
    quoteDisclaimer.textContent = hasProxy
      ? 'Источник: /api/signals. Метки real/delayed — рыночные данные, proxy — прокси-метрика (не реальная котировка).'
      : 'Источник: /api/signals. Используются рыночные данные (real/delayed).';
  } catch {
    quoteTrack.textContent = 'Не удалось загрузить котировки. Проверьте API /api/signals.';
    tickerUpdatedAt.textContent = 'Обновление: ошибка загрузки';
    quoteDisclaimer.textContent = 'Источник: /api/signals недоступен.';
  }
}

loadQuotes();
setInterval(loadQuotes, 60000);
