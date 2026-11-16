/* Research Center — run deep research, stream stage events, browse history. */
(function(){
  const $=s=>document.querySelector(s);
  let currentAnswer='';
  let currentMeta=null;
  let running=false;
  let actLog=null;

  AdminShell.mount('research');
  window.addEventListener('admin-ready',init);
  if(ADMIN_TOKEN)init();

  function addStage(ev){
    if(actLog){actLog.ingest(typeof ev==='string'?{event:'note',text:ev}:ev);return;}
    const d=document.createElement('div');
    d.className='rs-stage';
    d.innerHTML=`<span class="dot"></span><span>${esc(typeof ev==='string'?ev:(ev.text||ev.event))}</span>`;
    $('#stages').appendChild(d);
  }

  async function init(){
    loadModels();loadRuns();
    $('#runBtn').onclick=runResearch;
    $('#stopBtn').onclick=stopResearch;
    $('#q').addEventListener('keydown',e=>{if(e.key==='Enter')runResearch();});
    $('#exportBtn').onclick=exportMd;
    $('#printBtn').onclick=()=>{const w=window.open('','_blank');
      w.document.write('<html><head><title>Research report</title><style>body{font-family:ui-monospace,monospace;max-width:800px;margin:40px auto;white-space:pre-wrap;line-height:1.6}</style></head><body>');
      w.document.write(esc(currentAnswer));w.document.write('</body></html>');
      w.document.close();w.print();};
  }

  async function loadModels(){
    try{
      const d=await v1Api('/v1/models?usable=1');
      const sel=$('#model');
      (d.data||[]).forEach(m=>{
        const o=document.createElement('option');
        o.value=m.id;o.textContent=m.id;
        sel.appendChild(o);
      });
    }catch(_){}
  }

  async function loadRuns(){
    try{
      const rows=await v1Api('/v1/research/runs?limit=30');
      const host=$('#runs');host.innerHTML='';
      if(!rows.length){host.innerHTML='<div class="tx-3 t-sm" style="padding:14px">No research yet.</div>';return;}
      rows.forEach(r=>{
        const el=document.createElement('div');
        el.className='rs-run';
        el.innerHTML=`<div class="q">${esc(r.question)}</div>
          <div class="m"><span>${r.depth||'std'}</span><span>${r.sources} src</span>
          <span>${(r.created_at||'').slice(0,10)}</span></div>`;
        el.onclick=()=>openRun(r.id,el);
        host.appendChild(el);
      });
    }catch(e){$('#runs').innerHTML='<div class="tx-3 t-sm" style="padding:14px">'+esc(e.message)+'</div>';}
  }

  async function openRun(id,el){
    document.querySelectorAll('.rs-run.active').forEach(x=>x.classList.remove('active'));
    if(el)el.classList.add('active');
    try{
      const r=await v1Api('/v1/research/runs/'+id);
      renderAnswer(r.answer||'',{
        ...(r.meta||{}),
        question:r.question,run_id:r.id,created_at:r.created_at,
        sources:(r.meta&&r.meta.sources!=null)?r.meta.sources:(r.sources||[]).length,
        findings:(r.meta&&r.meta.findings!=null)?r.meta.findings:(r.findings||[]).length,
      });
    }catch(e){toast(e.message,'err');}
  }

  function rxRow(k,v){
    if(v==null||v==='')return '';
    return `<div class="rx-row"><span class="rx-k">${k}</span><span class="rx-v" title="${esc(String(v))}">${esc(String(v))}</span></div>`;
  }

  function renderRx(meta){
    if(!meta)return '';
    const q=meta.quality||{};
    const dur=meta.seconds!=null?meta.seconds+'s':null;
    const findings=meta.findings!=null
      ?(meta.verified!=null?`${meta.verified}/${meta.findings} verified`:String(meta.findings)):null;
    const date=(meta.created_at||'').slice(0,10)||new Date().toISOString().slice(0,10);
    const cites=q.citations!=null
      ?`${q.citations} checked${q.dead_urls&&q.dead_urls.length?` · ${q.dead_urls.length} dead`:''}`:null;
    const rows=[
      rxRow('Date',date),rxRow('Depth',meta.depth),
      rxRow('Model',meta.model),rxRow('Sources',meta.sources),
      rxRow('Findings',findings),rxRow('Rounds',meta.rounds),
      rxRow('Duration',dur),rxRow('Citations',cites),
    ].join('');
    if(!rows&&!meta.question)return '';
    return `<div class="rx-pad">
      <div class="rx-head"><span class="rx-sigil">℞</span>
        <div><div class="rx-title">Research Report</div>
          <div class="rx-topic">${esc(meta.question||'Untitled query')}</div></div>
        ${meta.run_id?`<div class="rx-no">№<b>${esc(String(meta.run_id))}</b></div>`:''}
      </div>
      ${rows?`<div class="rx-body">${rows}</div>`:''}
      <div class="rx-foot"><span>Verified research</span><span>FreeLLM Research Center</span></div>
    </div>`;
  }

  function renderAnswer(text,meta){
    currentAnswer=text;
    if(meta!==undefined)currentMeta=meta;
    $('#rxpad').innerHTML=renderRx(currentMeta);
    const host=$('#report');host.innerHTML='';
    const Nova=window.NovaRender||{};
    if(Nova.ResponseRenderer&&Nova.ResponseRenderer.renderMessage){
      try{
        Nova.ResponseRenderer.renderMessage({role:'assistant',text:text},host,{showActions:false});
        // Guard: if the renderer produced nothing visible, fall through
        // to the <pre> fallback rather than showing a blank report.
        if(host.textContent.trim()||host.querySelector('canvas,img,svg,video,audio')){
          $('#exportBtn').disabled=false;$('#printBtn').disabled=false;return;
        }
      }catch(_){}
    }
    host.innerHTML='<pre style="white-space:pre-wrap">'+esc(text)+'</pre>';
    $('#exportBtn').disabled=!text;
    $('#printBtn').disabled=!text;
  }

  function exportMd(){
    if(!currentAnswer)return;
    const blob=new Blob([currentAnswer],{type:'text/markdown'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download='research-report.md';
    a.click();URL.revokeObjectURL(a.href);
  }

  let runId='';
  let stopCtl=null;

  async function stopResearch(){
    const btn=$('#stopBtn');btn.disabled=true;
    try{
      await fetch(API_BASE+'/v1/research/stop',{method:'POST',headers:apiHeaders(false),
        body:JSON.stringify({conversation_id:runId})});
    }catch(_){}
    if(stopCtl){try{stopCtl.abort();}catch(_){}}
  }

  async function runResearch(){
    const q=$('#q').value.trim();
    if(!q||running)return;
    running=true;$('#runBtn').disabled=true;$('#runBtn').textContent='Researching…';
    $('#stopBtn').style.display='';$('#stopBtn').disabled=false;
    runId='research-'+Date.now()+'-'+Math.random().toString(36).slice(2,8);
    stopCtl=new AbortController();
    $('#stages').innerHTML='';$('#rxpad').innerHTML='';$('#report').innerHTML='<p class="tx-3" style="text-align:center;padding:48px 0">Research in progress — the report appears here when complete.</p>';
    actLog=window.Nova&&Nova.ActivityLog?Nova.ActivityLog.create($('#stages')):null;
    currentAnswer='';currentMeta=null;$('#exportBtn').disabled=true;
    try{
      const modeHints={academic:' Focus on academic and peer-reviewed sources — prefer papers, journals, and .edu domains.',
        technical:' Focus on official documentation, GitHub repositories, and technical specifications.',
        market:' Focus on market data, competitor analysis, pricing, and industry news.'};
      const msg=q+(modeHints[$('#mode').value]||'');
      const resp=await fetch(API_BASE+'/v1/research/stream',{
        method:'POST',headers:apiHeaders(false),signal:stopCtl.signal,
        body:JSON.stringify({message:msg,model:$('#model').value,depth:$('#depth').value||null,save:true,conversation_id:runId})
      });
      if(!resp.ok)throw new Error('research failed: '+resp.status);
      const reader=resp.body.getReader();const dec=new TextDecoder();
      let buf='';
      while(true){
        const{done,value}=await reader.read();
        if(done)break;
        buf+=dec.decode(value,{stream:true});
        let idx;
        while((idx=buf.indexOf('\n\n'))>=0){
          const frame=buf.slice(0,idx);buf=buf.slice(idx+2);
          for(const line of frame.split('\n')){
            if(!line.startsWith('data:'))continue;
            const data=line.slice(5).trim();
            if(data==='[DONE]')continue;
            try{
              const ev=JSON.parse(data);
              if(ev.freellm){
                const e=ev.freellm;
                if(e.event==='research_done'){
                  currentMeta={...e,question:q,model:$('#model').value,
                    created_at:new Date().toISOString()};
                  continue;
                }
                if(e.event==='research_answer')continue;
                addStage(e);
              }else if(ev.choices&&ev.choices[0]&&ev.choices[0].delta&&ev.choices[0].delta.content){
                renderAnswer(ev.choices[0].delta.content);
                loadRuns();
              }
            }catch(_){}
          }
        }
      }
    }catch(e){
      if(e.name==='AbortError'){
        addStage('Research stopped by user.');
      }else{
        if(actLog)actLog.fail(e.message);else addStage('Error: '+e.message);
        toast(e.message,'err');
      }
    }finally{
      if(actLog)actLog.finish();
      running=false;$('#runBtn').disabled=false;$('#runBtn').textContent='Run research';
      $('#stopBtn').style.display='none';
    }
  }
})();
