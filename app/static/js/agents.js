/* Agent Directory — discover specialist experts, chat with one. */
(function(){
  const $=s=>document.querySelector(s);
  let experts=[];
  AdminShell.mount('agents');
  window.addEventListener('admin-ready',init);
  if(ADMIN_TOKEN)init();

  async function init(){
    $('#filter').oninput=render;
    try{
      const d=await v1Api('/v1/experts');
      experts=d.experts||d||[];
      render();
    }catch(e){$('#grid').innerHTML='<div class="tx-3">'+esc(e.message)+'</div>';}
  }

  function render(){
    const f=($('#filter').value||'').toLowerCase();
    const g=$('#grid');g.innerHTML='';
    const list=experts.filter(e=>!f||(e.name+e.description+(e.domain||'')).toLowerCase().includes(f));
    list.forEach(e=>{
      const el=document.createElement('div');
      el.className='ui-card ui-card-pad';
      el.innerHTML=`<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
          <span class="ag-domain">${esc(e.domain||'general')}</span>
          ${e.builtin?'<span class="ag-skill">built-in</span>':''}</div>
        <div style="font-weight:700">${esc(e.name)}</div>
        <div class="tx-2" style="font-size:var(--t-sm);margin:6px 0 10px;min-height:40px">${esc(e.description||'')}</div>
        <div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:10px">${(e.skills||[]).slice(0,4).map(s=>`<span class="ag-skill">${esc(s)}</span>`).join('')}</div>
        <a class="ui-btn ui-btn-sm ui-btn-primary" href="/chat?agent=${encodeURIComponent(e.name)}">Use agent →</a>`;
      g.appendChild(el);
    });
    if(!list.length)g.innerHTML='<div class="tx-3">No agents match.</div>';
  }

  async function loadRuns(){
    try{
      const d=await v1Api('/v1/agents/runs?limit=30');
      const tb=document.querySelector('#runs tbody');tb.innerHTML='';
      (d.runs||[]).forEach(r=>{
        const tr=document.createElement('tr');
        const st=r.status==='done'?'<span class="pill ok">done</span>'
          :r.status==='degraded'?'<span class="pill" style="color:var(--warn)">degraded</span>'
          :r.status==='running'?'<span class="pill mut">running</span>'
          :'<span class="pill" style="color:var(--error)">failed</span>';
        tr.innerHTML=`<td class="tx-3">${r.id}</td><td>${esc(r.kind)}</td>
          <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(r.question)}">${esc(r.question)}</td>
          <td>${st}</td><td class="tx-3" style="font-size:10px">${esc(r.model||'')}</td>
          <td class="tx-3">${(r.duration_ms/1000).toFixed(1)}s</td>
          <td class="tx-3" style="font-size:11px">${(r.started_at||'').slice(5,16).replace('T',' ')}</td>
          <td><button class="ui-btn ui-btn-sm" data-trace>trace</button></td>`;
        tr.querySelector('[data-trace]').onclick=()=>showTrace(r.id);
        tb.appendChild(tr);
      });
      if(!(d.runs||[]).length)tb.innerHTML='<tr><td colspan="8" class="tx-3" style="text-align:center;padding:16px">No agent runs recorded yet.</td></tr>';
    }catch(_){}
  }

  async function showTrace(id){
    const box=document.getElementById('traceBox');
    box.style.display='';box.innerHTML='<div class="tx-3 t-sm">Loading…</div>';
    try{
      const r=await v1Api('/v1/agents/runs/'+id);
      box.innerHTML=`<div style="font-weight:700;margin-bottom:8px">#${r.id} ${esc(r.kind)} — ${esc(r.question.slice(0,120))}
        <span class="pill ${r.status==='done'?'ok':'mut'}" style="margin-left:8px">${r.status}</span></div>
        ${(r.events||[]).map(e=>`<div style="display:flex;gap:10px;padding:5px 0;border-bottom:1px solid var(--border-1);font-size:var(--t-sm)">
          <span class="tx-3" style="width:34px;flex:none">#${e.seq}</span>
          <span class="tx-3" style="width:90px;flex:none">${esc(e.stage||e.event)}</span>
          <b style="width:110px;flex:none">${esc(e.actor)}</b>
          <span class="${e.status==='fail'?'':''}" style="flex:1;${e.status==='fail'?'color:var(--error)':''}">${esc(e.detail)}</span>
          <span class="tx-3" style="width:60px;flex:none;text-align:right">${e.duration_ms?e.duration_ms.toFixed(0)+'ms':''}</span>
        </div>`).join('')||'<div class="tx-3">No events.</div>'}`;
      box.scrollIntoView({behavior:'smooth',block:'nearest'});
    }catch(e){box.innerHTML='<div class="tx-3">'+esc(e.message)+'</div>';}
  }

  loadRuns();
})();
