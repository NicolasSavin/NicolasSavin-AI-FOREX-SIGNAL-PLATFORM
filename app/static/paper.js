(() => {
  const $ = (id) => document.getElementById(id);
  const token = () => $('opsToken')?.value || sessionStorage.getItem('fxpilot_ops_token') || '';
  const headers = () => ({Accept:'application/json','Content-Type':'application/json','X-FXPILOT-OPS-TOKEN':token()});
  const card = (k,v) => `<article class="stats-page-card"><span>${k}</span><strong>${v ?? '—'}</strong></article>`;
  async function json(url, opts={}) { const r=await fetch(url,{cache:'no-store',...opts}); return r.json(); }
  async function load() {
    const [a,p,t,s]=await Promise.all([json('/api/paper/account'),json('/api/paper/positions'),json('/api/paper/trades'),json('/api/paper/statistics')]);
    $('paperAccount').innerHTML=[['Баланс',a.balance],['Equity',a.equity],['Free margin',a.free_margin],['Риск %',a.risk_percent],['Открыто',a.open_trades],['Закрыто',a.closed_trades]].map(x=>card(...x)).join('');
    $('equityCurve').textContent=JSON.stringify(a.equity_curve||[],null,2);
    const open=(p.items||[]).filter(x=>['PENDING','OPEN','PARTIAL','BREAKEVEN'].includes(x.state));
    $('openPositions').innerHTML=open.map(x=>`<article class="signal-card signal-card--${x.direction}"><div class="signal-card__top"><div><h3>${x.symbol} ${x.direction}</h3><p class="signal-card__subtitle">${x.state}</p></div><strong>${Number(x.floating_pnl||0).toFixed(2)}</strong></div><div class="signal-card__levels"><span>Entry ${x.entry}</span><span>SL ${x.stop_loss}</span><span>TP ${x.take_profit}</span></div></article>`).join('') || '<p class="section-text">Открытых позиций нет.</p>';
    $('closedTrades').innerHTML=(t.items||[]).map(x=>`<tr><td>${x.symbol}</td><td>${x.direction}</td><td>${x.entry}</td><td>${x.exit_price}</td><td>${x.pnl}</td><td>${x.rr}</td><td>${x.outcome}</td></tr>`).join('') || '<tr><td colspan="7">Закрытых сделок нет.</td></tr>';
    $('paperStats').innerHTML=[['Win Rate',`${s.win_rate}%`],['Profit Factor',s.profit_factor],['Average RR',s.average_rr],['Expectancy',s.expectancy],['Max DD',s.max_drawdown],['Data',s.data_label]].map(x=>card(...x)).join('');
  }
  async function op(url){ await json(url,{method:'POST',headers:headers(),body:'{}'}); load(); }
  $('paperRebuild').addEventListener('click',()=>op('/api/ops/paper/rebuild'));
  $('paperReset').addEventListener('click',()=>confirm('Сбросить виртуальный счёт?')&&op('/api/ops/paper/reset'));
  load().catch(e=>{$('paperStats').textContent=e.message;});
})();
