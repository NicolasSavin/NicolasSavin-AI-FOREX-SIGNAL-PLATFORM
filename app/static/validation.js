(function(){
  const statsEl=document.getElementById('validationStats'); const listEl=document.getElementById('validationList');
  const esc=(v)=>String(v??'—').replace(/[&<>"']/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
  function stat(k,v){return `<article><span>${esc(k)}</span><strong>${esc(v)}</strong></article>`}
  function card(i){return `<article class="idea-card animated-signal-card"><p class="section-kicker">${esc(i.symbol)} · ${esc(i.direction)}</p><h3>${esc(i.author||'Unknown')}</h3><div class="tv-trade-levels"><span>Entry<b>${esc(i.entry)}</b></span><span>SL<b>${esc(i.stop_loss)}</b></span><span>TP<b>${esc(i.take_profit)}</b></span><span>RR<b>${esc(i.rr)}</b></span></div><p>Статус: <b>${esc(i.status)}</b> · Outcome: <b>${esc(i.outcome)}</b></p><p>MFE: ${esc(i.max_favorable_excursion)} · MAE: ${esc(i.max_adverse_excursion)}</p><div class="chart-placeholder">Chart snapshot placeholder</div></article>`}
  async function load(){try{const r=await fetch('/api/validation',{cache:'no-store'}); const p=await r.json(); const s=p.stats||{}; statsEl.innerHTML=[stat('Pending',s.pending),stat('Running',s.running),stat('Validated',s.validated),stat('Failed',s.failed),stat('Expired',s.expired)].join(''); listEl.innerHTML=(p.items||[]).slice(-24).reverse().map(card).join('')||'<article class="panel">Проверок пока нет.</article>';}catch(e){listEl.textContent='Ошибка загрузки validation API';}}
  load(); setInterval(load,60000);
})();
