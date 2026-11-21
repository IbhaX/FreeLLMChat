/* Agent Directory — discover specialist experts, chat with one,
   edit/create custom agents via the admin CRUD API. */
(function(){
  const $=s=>document.querySelector(s);
  let experts=[],agents=[],models=[],toolDefs=[];
  AdminShell.mount('agents');
  window.addEventListener('admin-ready',()=>Promise.resolve(init()).catch(e=>{
    if(e&&e.status===403&&window.AdminShell)AdminShell.restricted();
    else if(window.toast)toast((e&&e.message)||'Failed to load','err');}));
  if(ADMIN_TOKEN)init();

  async function init(){
    $('#filter').oninput=render;
    $('#newAgent').onclick=()=>editDialog(null);
    try{
      const [ex,ag,md,td]=await Promise.all([
        v1Api('/v1/experts'),
        adminApi('/admin/agents'),
        v1Api('/v1/models?usable=1').catch(()=>({models:[]})),
        adminApi('/admin/tools').catch(()=>[]),
      ]);
      experts=ex.experts||ex||[];
      agents=ag||[];
      models=(md.data||md.models||[]).map(m=>m.id||m);
      toolDefs=td||[];
      render();
    }catch(e){$('#grid').innerHTML='<div class="tx-3">'+esc(e.message)+'</div>';}
  }

  const rowFor=n=>agents.find(a=>a.name===n);

  function toolOptions(saved){
    const opts=toolDefs.map(t=>({value:t.name,label:t.name,
      title:t.description||t.name}));
    const known=new Set(opts.map(o=>o.value));
    /* Surface saved-but-unlisted names so they stay visible/checked. */
    for(const n of saved||[])if(n!=='*'&&!known.has(n))
      opts.push({value:n,label:n+' (unlisted)',title:'Not in the tool registry'});
    return [{value:'*',label:'All tools',title:'Grant every tool',
      selectAll:true},...opts];
  }

  function modelOptions(current){
    const ids=['auto',...models.filter(id=>id!=='auto')];
    if(current&&!ids.includes(current))ids.push(current);
    return ids.map(id=>({value:id,label:id==='auto'?'auto — router picks':id}));
  }

  function card(e,row){
    const el=document.createElement('div');
    el.className='ui-card ui-card-pad';
    const customized=row&&(row.config||{}).customized;
    el.innerHTML=`<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
        <span class="ag-domain">${esc(e.domain||'general')}</span>
        ${row&&(row.config||{}).builtin?'<span class="ag-skill">built-in</span>':''}
        ${customized?'<span class="ag-skill" style="color:var(--warn)">customized</span>':''}
        ${row&&!row.enabled?'<span class="ag-skill" style="color:var(--error)">disabled</span>':''}</div>
      <div style="font-weight:700">${esc(e.name)}</div>
      <div class="tx-2" style="font-size:var(--t-sm);margin:6px 0 10px;min-height:40px">${esc(row?row.description:(e.description||e.summary||''))}</div>
      <div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:10px">${(e.skills||[]).slice(0,4).map(s=>`<span class="ag-skill">${esc(s)}</span>`).join('')}
        ${row?`<span class="ag-skill" title="model">${esc(row.model||'auto')}</span>`:''}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <a class="ui-btn ui-btn-sm ui-btn-primary" href="/chat?agent=${encodeURIComponent(e.name)}">Use agent →</a>
        ${row?'<button class="ui-btn ui-btn-sm" data-admin="1" data-edit>Edit</button>':''}
        ${customized?'<button class="ui-btn ui-btn-sm" data-admin="1" data-reset title="Restore the platform default">Reset</button>':''}
        ${row&&!(row.config||{}).builtin?'<button class="ui-btn ui-btn-sm" data-admin="1" data-del style="color:var(--error)">Delete</button>':''}
      </div>`;
    if(row){
      el.querySelector('[data-edit]').onclick=()=>editDialog(row,e);
      const rst=el.querySelector('[data-reset]');
      if(rst)rst.onclick=()=>resetAgent(row);
      const del=el.querySelector('[data-del]');
      if(del)del.onclick=()=>deleteAgent(row);
    }
    return el;
  }

  function render(){
    const f=($('#filter').value||'').toLowerCase();
    const g=$('#grid');g.innerHTML='';
    const match=s=>!f||s.toLowerCase().includes(f);
    experts.filter(e=>match(e.name+' '+(e.description||e.summary||'')+' '+(e.domain||'')))
      .forEach(e=>g.appendChild(card(e,rowFor(e.name))));
    /* Custom agents that aren't registry experts. */
    const names=new Set(experts.map(e=>e.name));
    agents.filter(a=>!names.has(a.name)&&match(a.name+' '+(a.description||'')))
      .forEach(a=>g.appendChild(card({name:a.name,domain:'custom',skills:[]},a)));
    if(!g.children.length)g.innerHTML='<div class="tx-3">No agents match.</div>';
    window.NovaAuth&&NovaAuth.applyRoleNav();
    if(window.NovaMotion){
      if(!render._painted){render._painted=1;
        NovaMotion.stagger(g.children,{y:16,dur:.35,stagger:.035});}
      NovaMotion.liftAll('#grid .ui-card',g,{y:-4});
    }
  }

  async function editDialog(row,expert){
    const builtin=row&&(row.config||{}).builtin;
    const savedTools=row?(((row.config||{}).tools)||[]):[];
    const fields=[
      ...(row?[]:[{name:'name',label:'Name (unique, snake_case)',required:true}]),
      {name:'description',label:'Description',type:'textarea',value:row?row.description:''},
      {name:'system_prompt',label:'System prompt',type:'textarea',value:row?row.system_prompt:''},
      {name:'model',label:'Model',type:'select',options:modelOptions(row?row.model:'auto'),
        value:row?row.model:'auto'},
      {name:'temperature',label:'Temperature (blank = provider default)',type:'number',
        value:row&&row.temperature!=null?row.temperature:''},
      {name:'max_tokens',label:'Max tokens (blank = provider default)',type:'number',
        value:row&&row.max_tokens!=null?row.max_tokens:''},
      {name:'tools',label:'Tools',type:'multiselect',
        options:toolOptions(savedTools),
        /* A saved '*' means every tool — pre-check the whole list so the
           select-all box renders checked, not half-ticked. */
        value:savedTools.includes('*')?toolDefs.map(t=>t.name):savedTools},
      {name:'enable_tools',label:'Enable tools',type:'checkbox',value:row?row.enable_tools:false},
      {name:'output_format',label:'Output format',type:'select',options:[
        {value:'text',label:'text'},{value:'structured',label:'structured (nova-response)'},
        {value:'json',label:'json'}],value:row?row.output_format:'text'},
      {name:'enabled',label:'Enabled',type:'checkbox',value:row?row.enabled:true},
    ];
    const out=await NovaUI.Dialog.form({
      title:row?('Edit agent — '+row.name):'New agent',
      message:builtin?'Built-in agent — edits detach it from platform updates; Reset restores the default.':undefined,
      fields,confirmLabel:row?'Save':'Create',
    });
    if(!out)return;
    const body={
      description:out.description,system_prompt:out.system_prompt,
      model:out.model,enable_tools:out.enable_tools,enabled:out.enabled,
      output_format:out.output_format,
      temperature:out.temperature===''?null:Number(out.temperature),
      max_tokens:out.max_tokens===''?null:Number(out.max_tokens),
      config:{...(row?row.config:{}),
        tools:(out.tools||[]).includes('*')?['*']:(out.tools||[])},
    };
    try{
      if(row){
        await adminApi('/admin/agents/'+row.id,{method:'PATCH',body:JSON.stringify(body)});
        toast('Agent updated','ok');
      }else{
        body.name=String(out.name).trim();
        await adminApi('/admin/agents',{method:'POST',body:JSON.stringify(body)});
        toast('Agent created','ok');
      }
      agents=await adminApi('/admin/agents');
      render();
    }catch(e){toast(e.message||'Save failed','err');}
  }

  async function resetAgent(row){
    if(!await NovaUI.Dialog.confirm(
      `Reset "${row.name}" to the platform default? Your customizations are lost.`,
      {title:'Reset agent',confirmLabel:'Reset'}))return;
    try{
      await adminApi('/admin/agents/'+row.id+'/reset',{method:'POST'});
      agents=await adminApi('/admin/agents');
      render();toast('Agent reset to default','ok');
    }catch(e){toast(e.message||'Reset failed','err');}
  }

  async function deleteAgent(row){
    if(!await NovaUI.Dialog.confirm(
      `Delete agent "${row.name}"? This cannot be undone.`,
      {title:'Delete agent',confirmLabel:'Delete',danger:true}))return;
    try{
      await adminApi('/admin/agents/'+row.id,{method:'DELETE'});
      agents=await adminApi('/admin/agents');
      render();toast('Agent deleted','ok');
    }catch(e){toast(e.message||'Delete failed','err');}
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
        <div class="act-steps" style="max-width:none"><div class="act-list"></div></div>`;
      const host=box.querySelector('.act-list');
      if(window.Nova&&Nova.ActivityLog){
        const log=Nova.ActivityLog.create(host);
        (r.events||[]).forEach(e=>log.ingest(e));
        log.finish(r.status==='done'?'Replayed trace':'Ended: '+r.status,r.status!=='failed');
      }else{
        host.innerHTML=(r.events||[]).map(e=>`<div class="act-item">
          <span class="act-dot"></span><span>${esc(e.title||e.detail||e.event)}</span></div>`).join('');
      }
      box.scrollIntoView({behavior:'smooth',block:'nearest'});
    }catch(e){box.innerHTML='<div class="tx-3">'+esc(e.message)+'</div>';}
  }

  loadRuns();
})();
