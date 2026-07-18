(function () {
  const root = document.getElementById('committeeRoot');
  const select = document.getElementById('committeeVideoSelect');
  const esc = (v) => String(v ?? '—').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
  const pct = (v) => `${Math.max(0, Math.min(100, Number.parseInt(v || 0, 10) || 0))}%`;
  const has = (v) => Array.isArray(v) ? v.length > 0 : v !== undefined && v !== null && String(v).trim() !== '';
  const first = (...values) => values.find(has) ?? '—';
  const direction = (v) => {
    const text = String(v || '').toUpperCase();
    if (text.includes('STRONG BUY')) return 'STRONG BUY';
    if (text.includes('STRONG SELL')) return 'STRONG SELL';
    if (text.includes('BUY') || text.includes('BULL')) return 'BUY';
    if (text.includes('SELL') || text.includes('BEAR')) return 'SELL';
    return 'WAIT';
  };
  const finalRecommendation = (report) => {
    const score = Number(report.overall_score || 0);
    if (report.decision === 'BUY' && score >= 85) return 'STRONG BUY';
    if (report.decision === 'SELL' && score >= 85) return 'STRONG SELL';
    if (report.decision === 'SELL') return 'SELL';
    if (report.decision === 'BUY') return 'BUY';
    return 'WAIT';
  };
  const list = (items) => Array.isArray(items) && items.length ? `<ul class="committee-list">${items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
  const metric = (label, value, cls) => has(value) ? `<article class="committee-card ${cls || ''}"><span>${esc(label)}</span><strong>${esc(value)}</strong></article>` : '';
  const section = (title, body, cls) => body ? `<section class="panel committee-section ${cls || ''}"><h2>${esc(title)}</h2>${body}</section>` : '';
  const human = (value) => ({ BUY: 'Покупать', SELL: 'Продавать', WAIT: 'Ждать', IGNORE: 'Игнорировать', BULLISH: 'Бычий', BEARISH: 'Медвежий', NEUTRAL: 'Нейтральный', LOW: 'Низкий', MEDIUM: 'Средний', HIGH: 'Высокий' }[String(value || '').toUpperCase()] || value || '—');
  const member = (name, weight, confidence, vote, reason) => `<article class="committee-member ${direction(vote).toLowerCase().replace(' ', '-')}"><div><span>${esc(name)}</span><strong>${esc(human(vote))}</strong></div><p>${esc(reason)}</p><footer><b>Вес: ${esc(weight)}</b><b>Уверенность: ${pct(confidence)}</b></footer></article>`;
  function memberCards(report) {
    const score = report.overall_score || 0;
    const agreement = report.agreement_score || 0;
    const ruleVote = direction(report.decision);
    return [
      member('Rule Engine', '25%', Math.max(0, score - 5), ruleVote, `Правила комитета формируют базовое решение ${human(report.decision)} при качестве сигнала ${report.signal_quality || '—'}.`),
      member('Knowledge', '20%', agreement, report.institutional_bias, `Knowledge Layer задаёт институциональный уклон: ${human(report.institutional_bias)}.`),
      member('LLM Review', '20%', score, report.decision, 'Используется только сохранённый AI Review; новая LLM-генерация не запускается.'),
      member('Performance', '10%', Math.round((score + agreement) / 2), score >= 65 ? report.decision : 'WAIT', 'Proxy-оценка дисциплины и согласованности до появления реальных рыночных исходов.'),
      member('Consensus', '25%', agreement, report.decision, `Итоговое согласие слоёв: ${pct(agreement)}.`),
    ].join('');
  }
  function voting(report) {
    const decision = direction(report.decision);
    const agreement = Number(report.agreement_score || 0);
    const bullish = decision.includes('BUY') ? agreement : decision === 'WAIT' ? Math.round((100 - agreement) / 2) : Math.max(0, 100 - agreement);
    const bearish = decision.includes('SELL') ? agreement : decision === 'WAIT' ? Math.round((100 - agreement) / 2) : Math.max(0, 100 - agreement);
    const neutral = Math.max(0, 100 - Math.max(bullish, bearish));
    return `<section class="committee-voting">${metric('Bullish %', pct(bullish), 'bullish')}${metric('Bearish %', pct(bearish), 'bearish')}${metric('Neutral %', pct(neutral), 'neutral')}${metric('Agreement %', pct(agreement), 'agreement')}</section>`;
  }
  function tradingPlan(report) {
    const v = report.video || {};
    const symbol = first(report.primary_symbol, v.primary_symbol, v.symbol, v.ticker);
    const directionText = human(report.decision);
    const rows = [['Entry', first(report.entry, v.entry, 'По подтверждению setup')], ['Stop', first(report.stop, report.stop_loss, v.stop, 'Не указан — требуется риск-модель')], ['Targets', first(report.targets, v.targets, 'Не указаны')], ['Timeframe', first(report.timeframe, v.timeframe, 'Среднесрочный')], ['Primary symbol', symbol], ['Direction', directionText]];
    return `<div class="committee-plan">${rows.filter(([, value]) => has(value)).map(([k, value]) => `<div><span>${esc(k)}</span><strong>${esc(Array.isArray(value) ? value.join(', ') : value)}</strong></div>`).join('')}</div>`;
  }
  function render(report) {
    const rec = finalRecommendation(report);
    const missing = report.warnings || report.conflicts || [];
    root.innerHTML = `
      <section class="committee-report-hero ${rec.toLowerCase().replace(' ', '-')}">
        <div><span>Final Recommendation</span><strong>${esc(rec)}</strong><p>${esc(report.summary || 'Институциональный комитет использует сохранённые данные анализа.')}</p></div>
        <div class="committee-hero-metrics"><b>${esc(report.overall_score)}/100</b><small>Score</small><b>${pct(report.agreement_score)}</b><small>Confidence</small></div>
      </section>
      <section class="committee-score-grid">
        ${metric('Decision', human(report.decision), String(report.decision || '').toLowerCase())}
        ${metric('Confidence', pct(report.agreement_score), 'agreement')}
        ${metric('Institutional bias', human(report.institutional_bias), String(report.institutional_bias || '').toLowerCase())}
        ${metric('Risk', human(report.risk_level), String(report.risk_level || '').toLowerCase())}
        ${metric('Time horizon', first(report.time_horizon, report.video?.time_horizon, 'Среднесрочный'), 'neutral')}
      </section>
      ${section('Executive Verdict', `<p class="committee-verdict-text">Решение: <b>${esc(human(report.decision))}</b>. Уверенность комитета: <b>${pct(report.agreement_score)}</b>. Риск: <b>${esc(human(report.risk_level))}</b>. Вердикт: <b>${esc(report.committee_verdict)}</b>.</p>`)}
      ${section('Committee Members', `<div class="committee-members">${memberCards(report)}</div>`)}
      ${voting(report)}
      ${section('Evidence · Positive factors', list(report.pros), 'positive')}
      ${section('Evidence · Negative factors', list(report.cons), 'negative')}
      ${section('Evidence · Missing information', list(missing), 'missing')}
      ${section('Evidence · Risk factors', list([...(report.conflicts || []), ...(report.cons || [])]), 'risk')}
      ${section('Trading Plan', tradingPlan(report))}
      ${section('Decision Timeline', `<ol class="committee-timeline"><li><span>Media imported</span><b>${esc(report.video?.imported_at || report.video?.published_at || 'сохранено')}</b></li><li><span>Review generated</span><b>${esc(report.video?.review_generated_at || report.created_at || 'stored review')}</b></li><li><span>Committee generated</span><b>${esc(report.created_at || 'сейчас')}</b></li></ol>`)}
      <section class="panel committee-provider-note"><p>Read-only режим: страница читает только сохранённый committee/API payload и не запускает LLM.</p></section>`;
  }
  async function loadReport(id) {
    root.innerHTML = '<article class="panel">Комитет загружает сохранённый отчёт...</article>';
    const res = await fetch(`/api/media/committee/${encodeURIComponent(id)}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!res.ok) throw new Error('committee request failed');
    render(await res.json());
  }
  async function init() {
    try {
      const media = await fetch('/api/media', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then((r) => r.json());
      const items = Array.isArray(media) ? media : [];
      select.innerHTML = items.map((v) => `<option value="${esc(v.id)}">${esc(v.title || v.id)}</option>`).join('') || '<option>Видео не найдены</option>';
      const pathId = decodeURIComponent(location.pathname.replace(/^\/committee\/?/, '') || '');
      const queryId = new URLSearchParams(location.search).get('video_id');
      const preferred = items.find((v) => v.id === (queryId || pathId))?.id || items[0]?.id;
      if (preferred) { select.value = preferred; await loadReport(preferred); }
    } catch (e) {
      root.innerHTML = '<article class="panel">Не удалось загрузить Investment Committee.</article>';
    }
  }
  select?.addEventListener('change', () => select.value && loadReport(select.value));
  init();
})();
