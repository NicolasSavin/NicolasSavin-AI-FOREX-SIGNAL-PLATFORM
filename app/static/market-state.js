(function(){
  const $=(id)=>document.getElementById(id); let items=[]; let debug={};
  const esc=(v)=>String(v??'—').replace(/[&<>"']/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
  const stat=(k,v)=>`<article class="animated-signal-card"><span>${esc(k)}</span><strong>${esc(v)}</strong></article>`;
  function cls(v){return String(v||'').toLowerCase().replace(/[^a-z]+/g,'-')}
  function render(){
    const q=$('qualityFilter').value; const s=$('marketSearch').value.trim().toUpperCase(); const sort=$('sortMarket').value;
    const rows=items.filter(i=>(!q||i.market_quality===q)&&(!s||String(i.symbol).includes(s))).sort((a,b)=> sort==='symbol'?String(a.symbol).localeCompare(String(b.symbol)):(Number(b[sort]||0)-Number(a[sort]||0)));
    $('marketStateStats').innerHTML=[stat('Символы',items.length),stat('Cache hit',debug.cache_hit?'yes':'no'),stat('Cache age',debug.cache_age_seconds??0),stat('Ошибки',(debug.errors||[]).length)].join('');
    $('marketStateRows').innerHTML=rows.map(i=>`<tr class="animated-signal-card"><td><b>${esc(i.symbol)}</b></td><td><span class="badge ${cls(i.direction)}">${esc(i.direction)}</span></td><td>${esc(i.trend_strength)}</td><td>${esc(i.confidence)}%</td><td>${esc(i.agreement)}%</td><td>${esc(i.validation_score)}%</td><td>${esc(i.author_score)}% · ${esc(i.author_count)}</td><td>${esc(i.performance_score)}%</td><td><span class="badge ${cls(i.market_quality)}">${esc(i.market_quality)}</span></td><td>${esc(i.updated_at).slice(0,19).replace('T',' ')}</td></tr>`).join('')||'<tr><td colspan="10">Нет данных Market State.</td></tr>';
    $('marketStateDebug').textContent=JSON.stringify(debug,null,2);
  }
  async function load(){try{const [r,d]=await Promise.all([fetch('/api/market-state',{cache:'no-store'}),fetch('/api/market-state/debug',{cache:'no-store'})]); const p=await r.json(); debug=await d.json(); items=p.items||[]; render();}catch(e){$('marketStateRows').innerHTML='<tr><td colspan="10">Ошибка загрузки Market State API</td></tr>';}}
  ['marketSearch','qualityFilter','sortMarket'].forEach(id=>$(id).addEventListener('input',render));
  load(); setInterval(load,30000);
})();
