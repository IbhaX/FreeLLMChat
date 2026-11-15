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
})();
