(async function loadMultiTimeframeHome(){
  const root=document.getElementById('multiTimeframeHome'); if(!root) return;
  const esc=(v)=>String(v??'—').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  try{
    const r=await fetch('/api/multi-timeframe',{cache:'no-store'}); if(!r.ok) throw new Error('HTTP '+r.status);
    const rows=((await r.json()).items||[]);
    const top=[...rows].sort((a,b)=>(b.alignment_score||0)-(a.alignment_score||0)).slice(0,3).map(i=>`${i.symbol} ${i.alignment_score}%`).join('<br>')||'Нет данных';
    const conflict=[...rows].sort((a,b)=>(b.conflict_score||0)-(a.conflict_score||0)).slice(0,3).map(i=>`${i.symbol} ${i.conflict_score}%`).join('<br>')||'Нет данных';
    const trend=[...rows].sort((a,b)=>(b.confidence||0)-(a.confidence||0)).slice(0,3).map(i=>`${i.symbol} ${i.trend_strength}`).join('<br>')||'Нет данных';
    root.innerHTML=`<article class="stats-page-card"><span>Top aligned</span><strong>${top}</strong></article><article class="stats-page-card"><span>Highest conflict</span><strong>${conflict}</strong></article><article class="stats-page-card"><span>Strongest trend</span><strong>${trend}</strong></article>`;
  }catch(_){root.innerHTML='<article class="stats-page-card"><span>Multi TF</span><strong>API недоступен</strong></article>';}
})();
