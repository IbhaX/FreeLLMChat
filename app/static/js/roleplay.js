/* The Stage — immersive roleplay: streaming turns, presence, swipe,
   narrator/ooc/whisper composer modes, director panel. */
(function(){
  const $=s=>document.querySelector(s);
  const $$=s=>[...document.querySelectorAll(s)];
  let chars=[],rooms=[],room=null,mode='talk',beatOnly=false,
      editId=null,castPicks=[],streaming=null,swipeIdx={},summarizing=false,
      streamCtl=null;

  const toast2=(m,k)=>{const t=$('#toast');t.textContent=m;
    t.className='st-toast show';clearTimeout(t._t);
    t._t=setTimeout(()=>t.classList.remove('show'),2200);};
  const face=(c,sz)=>`<div class="av" style="${c&&c.avatar_url?
    `background-image:url(${esc(c.avatar_url)});background-size:cover`:''}">${c&&c.avatar_url?'':esc(c&&c.emoji||'🎭')}</div>`;
  const charById=id=>chars.find(c=>c.id===id);
  const scroll=()=>{const s=$('#scroll');s.scrollTop=s.scrollHeight;};
  let alignedTop=null;
  function alignTurn(el,target=el){
    const s=$('#scroll');
    s.style.setProperty('--st-tail-space',`${s.clientHeight}px`);
    s.scrollTop+=target.getBoundingClientRect().top-s.getBoundingClientRect().top-12;
    alignedTop=s.scrollTop;
  }
  function readerStayed(){
    const s=$('#scroll');
    return alignedTop==null||Math.abs(s.scrollTop-alignedTop)<=4||
      s.scrollTop>=s.scrollHeight-s.clientHeight-4;
  }

  // ------------------------------------------------ bootstrap
  async function init(){
    bind();
    loadSceneModels();
    try{
      chars=await v1Api('/v1/characters');
      const d=await v1Api('/v1/roleplay/rooms');
      rooms=d.rooms||[];
      const qRoom=parseInt(new URLSearchParams(location.search).get('room')||'0');
      const last=parseInt(localStorage.getItem('rp_last')||'0');
      const r=rooms.find(x=>x.id===qRoom)||rooms.find(x=>x.id===last)||rooms[0];
      if(r)await openRoom(r.id);else{renderEmpty();renderCast();}
      // resume an in-flight turn
      if(r){const live=await v1Api(`/v1/roleplay/rooms/${r.id}/live`).catch(()=>({}));
        if(live&&live.running&&room&&room.id===r.id&&!streaming)
          consume(`/v1/roleplay/rooms/${r.id}/live/stream`);}
    }catch(e){toast2(e.message);renderEmpty();}
  }

  function bind(){
    $$('.st-mode[data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
    $('#beatOnly').onclick=()=>{beatOnly=!beatOnly;
      $('#beatOnly').classList.toggle('on',beatOnly);};
    $('#btnRun').onclick=send;
    $('#input').addEventListener('keydown',e=>{
      if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}});
    $('#input').addEventListener('input',e=>{e.target.style.height='auto';
      e.target.style.height=Math.min(e.target.scrollHeight,140)+'px';});
    $('#btnScenes').onclick=()=>{renderScenes();$('#scenesVeil').classList.add('open');
      if(window.NovaMotion)NovaMotion.veil($('#scenesVeil'),true);};
    $('#btnCast').onclick=()=>{castPicks=[...(room?room.character_ids||[]:[])];
      renderCastCards();$('#castVeil').classList.add('open');
      if(window.NovaMotion)NovaMotion.veil($('#castVeil'),true);};
    $('#btnDirector').onclick=()=>$('#director').classList.toggle('open');
    $('#btnWorld').onclick=()=>{if(!room)return toast2('Open a scene first');
      renderWorldPanel();$('#worldVeil').classList.add('open');
      if(window.NovaMotion)NovaMotion.veil($('#worldVeil'),true);};
    $('#btnTimeline').onclick=()=>{if(!room)return toast2('Open a scene first');
      renderTimeline();$('#tlVeil').classList.add('open');
      if(window.NovaMotion)NovaMotion.veil($('#tlVeil'),true);};
    $('#btnRel').onclick=()=>{if(!room)return toast2('Open a scene first');
      renderConstellation();$('#relVeil').classList.add('open');
      if(window.NovaMotion)NovaMotion.veil($('#relVeil'),true);};
    $('#relAdd').onclick=addRelation;
    $('#btnNewScene').onclick=newScene;
    $('#btnQuickNewScene').onclick=newScene;
    $('#sceneModelBtn').onclick=e=>{
      e.stopPropagation();openSceneModelMenu();};
    document.addEventListener('click',e=>{
      if(!e.target.closest('#sceneModelBtn,#sceneModelMenu'))
        $('#sceneModelMenu').classList.remove('open');});
    $('#btnNewChar').onclick=()=>openEditor(null);
    $('#btnApplyCast').onclick=applyCast;
    $('#btnBegin').onclick=()=>beat('narrate','The scene opens.');
    $('#btnSurprise').onclick=()=>beat('ooc',
      'Introduce an unexpected twist that fits the scene.',null,true);
    $('#charSave').onclick=saveCharacter;
    $$('[data-close]').forEach(b=>b.onclick=e=>
      e.target.closest('.st-veil').classList.remove('open'));
    ['dScenario','dMood','dNotes','dTools'].forEach(id=>{
      $('#'+id).addEventListener('change',patchRoom);});
    $('#dSummarize').onclick=async()=>{
      if(summarizing)return;summarizing=true;
      const box=$('#dSummary');
      box.innerHTML='<span class="sum-live">Summarizing…</span>';
      let acc='';
      try{
        const r=await fetch(API_BASE+`/v1/roleplay/rooms/${room.id}/summary`,{
          method:'POST',headers:apiHeaders(false)});
        if(!r.ok||!r.body)throw new Error('summary failed: '+r.status);
        const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
        while(true){
          const{value,done}=await rd.read();
          if(done)break;
          buf+=dec.decode(value,{stream:true});
          let i;
          while((i=buf.indexOf('\n\n'))>=0){
            const part=buf.slice(0,i);buf=buf.slice(i+2);
            for(const line of part.split('\n')){
              if(!line.startsWith('data:'))continue;
              const data=line.slice(5).trim();
              if(data==='[DONE]'){summarizing=false;return;}
              try{const j=JSON.parse(data);const ev=j.nova;
                if(!ev)continue;
                if(ev.event==='sum_delta'){
                  acc+=ev.text;box.textContent=acc;}
                else if(ev.event==='note'){
                  $('#queueLine').textContent=ev.text||'';}
                else if(ev.event==='sum_end'){
                  room.summary=ev.summary;renderDirector();
                  if(ev.via&&ev.via.model)
                    toast2('Story recap updated · '+ev.via.model);
                  else toast2('Story recap updated');
                  $('#queueLine').textContent='';}
                else if(ev.event==='error'){box.textContent=ev.text||'failed';}
              }catch{}}
            }
          }
        }
      catch(e){box.textContent='Recap failed: '+e.message;}
      summarizing=false;};
    $('#dClear').onclick=async()=>{
      if(!room||!confirm('Clear the transcript and start the scene over?'))return;
      for(const m of[...(room.messages||[])])
        await v1Api(`/v1/roleplay/rooms/${room.id}/messages/${m.id}`,{method:'DELETE'}).catch(()=>{});
      openRoom(room.id);};
    $('#sceneTitle').onclick=renameScene;
  }

  // ------------------------------------------------ modes / composer
  function setMode(m){
    mode=m;
    $$('.st-mode[data-mode]').forEach(b=>b.classList.toggle('on',b.dataset.mode===m));
    $('#input').dataset.mode=m;
    const ph={talk:'Say something… (Enter to run, Shift+Enter for newline)',
      narrate:'Describe what happens in the scene…',
      ooc:'((Direction for the characters — not spoken in scene))',
      whisper:'Whisper so only one character hears…'};
    $('#input').placeholder=ph[m];
    const sel=$('#whisperTo');
    sel.style.display=m==='whisper'?'':'none';
    if(m==='whisper'&&room){sel.innerHTML='';
      (room.character_ids||[]).forEach(id=>{const c=charById(id);if(!c)return;
        const o=document.createElement('option');o.value=id;
        o.textContent=c.name;sel.appendChild(o);});}
  }

  async function send(){
    const text=$('#input').value.trim();
    if(!text||!room||streaming)return;
    $('#input').value='';$('#input').style.height='auto';
    const wt=mode==='whisper'?parseInt($('#whisperTo').value)||null:null;
    beat(mode,text,wt);
  }

  // ------------------------------------------------ SSE turns
  async function beat(kind,text,whisperTo,advanceOverride){
    if(!room)return;
    streaming=true;setRunning(true);
    streamCtl=new AbortController();
    try{
      const r=await fetch(API_BASE+`/v1/roleplay/rooms/${room.id}/turn`,{
        method:'POST',headers:apiHeaders(false),signal:streamCtl.signal,
        body:JSON.stringify({kind,text,whisper_to:whisperTo||null,
          advance:advanceOverride!=null?advanceOverride:!beatOnly})});
      if(!r.ok)throw new Error('turn failed: '+r.status);
      await consumeStream(r);
    }catch(e){if(e.name!=='AbortError')toast2(e.message);}
    finally{streaming=false;setRunning(false);streamCtl=null;}
  }

  async function consume(path){
    streaming=true;setRunning(true);
    streamCtl=new AbortController();
    try{const r=await fetch(API_BASE+path,{headers:apiHeaders(false),
        signal:streamCtl.signal});
      if(r.ok)await consumeStream(r);}
    finally{streaming=false;setRunning(false);streamCtl=null;}
  }

  async function retryBeat(mid){
    if(!room||streaming)return;
    streaming=true;setRunning(true);
    streamCtl=new AbortController();
    try{
      const r=await fetch(API_BASE+`/v1/roleplay/rooms/${room.id}/retry`,{
        method:'POST',headers:apiHeaders(false),signal:streamCtl.signal,
        body:JSON.stringify({message_id:mid})});
      if(!r.ok)throw new Error('retry failed: '+r.status);
      await consumeStream(r);
    }catch(e){if(e.name!=='AbortError')toast2(e.message);}
    finally{streaming=false;setRunning(false);streamCtl=null;}
  }

  async function stopTurn(){
    // Cancel the detached job server-side (partial replies persist) and
    // abort the local stream reader.
    if(room){
      try{await fetch(API_BASE+`/v1/roleplay/rooms/${room.id}/live`,
        {method:'DELETE',headers:apiHeaders(false)});}catch(_){}
    }
    if(streamCtl){try{streamCtl.abort();}catch(_){}}
    streaming=false;setRunning(false);streamCtl=null;
    $('#queueLine').textContent='⏹ interrupted — partial replies kept';
  }

  // Stop lives in its own stable control beside Run — queueLine text is
  // rewritten by turn_queue/tool/note events and must not own the button.
  let neuralTl=null;
  function paintNeural(on){
    const nodes=$$('#backdrop .st-neural i');
    if(neuralTl){neuralTl.kill();neuralTl=null;}
    if(!window.gsap){return;}
    if(!on||matchMedia('(prefers-reduced-motion: reduce)').matches){
      gsap.set(nodes,{clearProps:'transform,opacity'});return;}
    neuralTl=gsap.timeline({repeat:-1,yoyo:true}).to(nodes,
      {x:36,y:-24,scale:1.8,opacity:.35,duration:2.8,stagger:.35,ease:'sine.inOut'});
  }

  function setRunning(on){
    $('#backdrop').classList.toggle('is-live',on);
    paintNeural(on);
    $('#btnRun').disabled=on;
    $('#btnRun').hidden=on;
    const ql=$('#queueLine');
    let sb=document.getElementById('btnStop');
    if(on){
      $('#choices').hidden=true;
      if(!sb){
        sb=document.createElement('button');sb.type='button';sb.id='btnStop';
        sb.className='st-run st-run-stop';
        sb.title='Stop turn';
        sb.setAttribute('aria-label','Stop turn');
        sb.innerHTML='<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="3" width="10" height="10" rx="2" fill="currentColor"/></svg>';
        sb.onclick=stopTurn;
        $('#btnRun').insertAdjacentElement('afterend',sb);
      }
      sb.hidden=false;
      ql.innerHTML='<b>Curtain up…</b>';
    }else{
      if(sb)sb.hidden=true;
      if(!streaming&&!ql.textContent.startsWith('⏹'))ql.textContent='';
    }
  }

  // Clear any stray streaming state when the SSE stream ends without a
  // matching char_end/turn_done (truncated stream, job cancel): clears the
  // character's presence face, marks live findings/activity as interrupted
  // and finishes the StreamingRenderer, keeping the partial text.
  function finalizeStrayStreams(){
    $$('#transcript .st-msg.writing, #transcript .st-msg._streaming')
      .forEach(el=>{
        el.classList.remove('writing');el._streaming=false;
        if(el._cid!=null)presence(el._cid,false);
        if(el._lr){try{el._lr.finish('cancelled');}catch(_){}el._lr=null;}
        if(el._log){try{el._log.finish('interrupted',false);}catch(_){}el._log=null;}
        if(el._sr){try{el._sr.finish(el._text||'');}catch(_){}el._sr=null;}
        else{const t=el.querySelector('.txt');
          if(t&&el._text)t.textContent=el._text;}
      });
  }

  async function consumeStream(resp){
    const rd=resp.body.getReader(),dec=new TextDecoder();
    let buf='';
    try{
    while(true){
      const{value,done}=await rd.read();
      if(done)break;
      buf+=dec.decode(value,{stream:true});
      let i;
      while((i=buf.indexOf('\n\n'))>=0){
        const part=buf.slice(0,i);buf=buf.slice(i+2);
        for(const line of part.split('\n')){
          if(!line.startsWith('data:'))continue;
          const data=line.slice(5).trim();
          if(data==='[DONE]'){finalizeStrayStreams();return;}
          try{const j=JSON.parse(data);
            if(j.nova)onEvent(j.nova);}catch{}
        }
      }
    }
    }finally{finalizeStrayStreams();}
  }

  // Public activity/finding/answer events belong to the character currently
  // writing — not the transient queue line.
  function routeToHolder(ev){
    const holder=document.querySelector('#transcript .st-msg.writing');
    if(!holder)return false;
    const ch=ev.channel||'';
    const interactive=ch==='finding'||ch==='answer'||
      ev.event==='finding'||ev.event==='source_discovered'||
      ev.event==='answer_delta'||ev.event==='answer_section';
    if(interactive){holder._lr&&holder._lr.ingest(ev);return true;}
    if(holder._log&&ev.event&&!['char_delta','char_start','char_end',
        'turn_queue','turn_done','beat'].includes(ev.event)){
      holder._log.ingest(ev);return true;}
    return false;
  }

  function onEvent(ev){
    if(['tool','note','fail','retry','step_start','step_done','step_fail',
        'step_queued','tool_start','handoff','finding','source_discovered',
        'answer_delta','answer_section','usage','try','routing','next','skip',
        'rate_limited','degraded','budget'].includes(ev.event)||ev.channel){
      if(routeToHolder(ev))return;
    }
    switch(ev.event){
      case 'beat': upsertMsg(ev.message);scroll();break;
      case 'turn_queue':{
        const names=(ev.cast||[]).map(c=>c.name).join(' → ');
        $('#queueLine').innerHTML=`<b>Up next:</b> ${esc(names)}`;break;}
      case 'char_start':{
        presence(ev.character_id,true);
        const b=ensureMsgEl({id:ev.id,role:'character',kind:'dialogue',
          speaker:ev.name,emoji:ev.emoji,character_id:ev.character_id,
          text:'',variants:[],aside:ev.aside});
        b.classList.add('writing');b._streaming=true;b._cid=ev.character_id;
        // Reset per-element stream state on every start (regenerate too):
        // destroy the previous renderers and recreate them in a fresh host.
        if(b._sr){try{b._sr.abort();}catch(_){}b._sr=null;}
        if(b._lr){try{b._lr.destroy();}catch(_){}b._lr=null;}
        if(b._log){try{b._log.destroy();}catch(_){}b._log=null;}
        if(b._lrHost){(b._lrHost.closest('details.st-trace')||b._lrHost).remove();b._lrHost=null;}
        b._text='';b._curEnv='';
        const txt=b.querySelector('.txt');
        if(txt){txt.innerHTML='';txt.classList.add('rich');}
        if(window.NovaRender&&NovaRender.LiveRun&&window.Nova&&Nova.ActivityLog){
          const host=document.createElement('div');host.className='lr-host';
          b.querySelector('.b').insertBefore(host,txt);
          b._lrHost=host;
          b._log=Nova.ActivityLog.create(host,{compact:true,onModel:openModelPicker});
          b._lr=NovaRender.LiveRun.create(host,{scroller:null});
        }
        b._aligned=false;
        alignTurn(b);
        break;}
      case 'char_delta':{
        const el=document.getElementById('m-'+ev.id);
        if(el){el._text=(el._text||'')+ev.text;
          const txt=el.querySelector('.txt');
          if(!el._sr&&window.NovaRender&&NovaRender.StreamingRenderer&&txt){
            el._sr=NovaRender.StreamingRenderer.create(txt,null);}
          if(el._sr)el._sr.setText(el._text);
          else txt.textContent=el._text;
          if(!el._aligned&&el._text.trim()){
            el._aligned=true;
            if(readerStayed())requestAnimationFrame(()=>alignTurn(el,txt));}}break;}
      case 'char_skip':{
        const el=document.getElementById('m-'+ev.id);
        if(el)el.remove();
        presence(ev.character_id,false);break;}
      case 'char_end':{
        const stayed=readerStayed();
        const el=document.getElementById('m-'+ev.id);
        if(el){el.classList.remove('writing');el._streaming=false;
          if(el._cid!=null)presence(el._cid,false);
          const txt=el.querySelector('.txt');
          // authoritative envelope replaces any draft — never appended twice
          const finalTxt=ev.env_text||ev.text||'';
          if(el._sr){try{el._sr.abort();}catch(_){}}
          if(ev.env_text)renderRich(el,ev.env_text,{fallback:ev.text||''});
          else if(txt)txt.textContent=ev.text||'';
          el._sr=null;
          el._text=ev.text;
          el._curEnv=ev.env_text||'';
          el._variantMsgs=ev.variant_messages||[];
          if(el._lr){try{el._lr.finish('done');}catch(_){}el._lr=null;}
          if(el._log){try{el._log.finish('done',true);}catch(_){}el._log=null;}
          collapseFinishedActivity(el);
          if(ev.sources&&ev.sources.length){
            el._sources=ev.sources;
            if(room){const m=(room.messages||[]).find(x=>x.id===ev.id);
              if(m)m.sources=ev.sources;}}
          if(ev.via&&ev.via.model&&!el.querySelector('.via')){
            const via=document.createElement('span');via.className='via';
            via.title='powered by '+(ev.via.provider||'');
            via.textContent=ev.via.model;
            el.querySelector('.who').appendChild(via);}
          if(ev.variants&&ev.variants.length)attachSwipe(el,ev.id,ev.variants,
            el._variantMsgs);}
        // keep room.messages in sync
        if(room){const m=(room.messages||[]).find(x=>x.id===ev.id);
          if(m){m.text=ev.text;if(ev.env_text)m.env_text=ev.env_text;
            if(ev.variants)m.variants=ev.variants;
            if(ev.sources)m.sources=ev.sources;
            if(ev.via)m.via=ev.via;}}
        if(stayed){const t=document.getElementById('m-'+ev.id);
          const tx=t&&t.querySelector('.txt');if(tx)alignTurn(t,tx);}
        break;}
      case 'turn_done':{
        if(room)room.messages=ev.messages;
        $('#queueLine').textContent='';renderCast();
        loadChoices();
        break;}
      case 'tool':{
        const q=(ev.args&&(ev.args.query||ev.args.url))||'';
        $('#queueLine').innerHTML=`🔍 <b>${esc(ev.tool||'tool')}</b>`+
          (q?` ${esc(String(q)).slice(0,80)}`:'');
        break;}
      case 'note': case 'fail': case 'retry':{
        if(ev.text)$('#queueLine').textContent=ev.text;break;}
      case 'error': toast2(ev.text||'something went wrong');break;
    }
  }

  function presence(cid,on){
    const f=document.querySelector(`.st-face[data-cid="${cid}"]`);
    if(!f)return;
    f.classList.toggle('writing',on);
    let w=f.querySelector('.st-face-writ');
    if(on&&!w){w=document.createElement('div');w.className='st-face-writ';
      w.textContent='writing…';f.appendChild(w);}
    if(!on&&w)w.remove();
  }

  // ------------------------------------------------ transcript
  function upsertMsg(m){
    if(document.getElementById('m-'+m.id))return;
    const msgs=room.messages||[];
    if(!msgs.find(x=>x.id===m.id))room.messages=[...msgs,m];
    appendMsg(m);
  }

  function collapseFinishedActivity(el){
    const host=el._lrHost;
    if(!host||!host.isConnected||!matchMedia('(max-width:600px)').matches)return;
    if(host.parentElement.closest('details.st-trace'))return;
    const det=document.createElement('details');det.className='st-trace';
    const sum=document.createElement('summary');sum.textContent='activity & sources';
    det.appendChild(sum);host.before(det);det.appendChild(host);
  }

  function ensureMsgEl(m){
    let el=document.getElementById('m-'+m.id);
    if(!el)el=appendMsg(m);
    return el;
  }

  function appendMsg(m){
    $('#stageEmpty').style.display='none';
    const host=$('#transcript');
    const el=document.createElement('div');
    const isUser=m.role==='user'&&m.kind!=='ooc';
    el.className='st-msg'+(isUser?' user':'')+
      (m.kind==='narration'?' narration':'')+
      (m.kind==='ooc'?' ooc':'')+(m.kind==='whisper'?' whisper':'')+
      (m.aside?' aside':'');
    el.id='m-'+m.id;
    const who=m.kind==='narration'?'':m.kind==='ooc'?'out of character':
      m.kind==='whisper'?`whispered to ${esc(charById(m.whisper_to)?.name||'?')}`:
      esc(m.speaker||'');
    el.innerHTML=`${isUser?`<div class="av">🧑</div>`:
      m.role==='narrator'?'<div class="av">📜</div>':
      `<div class="av">${esc(m.emoji||'🎭')}</div>`}
      <div class="b"><div class="who">${who}${
        m.aside_label?`<span class="via" style="opacity:.7">· ${esc(m.aside_label)}</span>`:''}${
        m.via&&m.via.model?`<span class="via" title="powered by ${esc(m.via.provider||'')}">${esc(m.via.model)}</span>`:''}</div>
      <div class="txt">${m.env_text?'':esc(m.text||'')}</div></div>`;
    el._text=m.text||'';
    el._variantMsgs=m.variant_messages||[];
    if(m.env_text)renderRich(el,m.env_text,{showActions:false,showSuggestions:false});
    // Persisted interaction events → compact completed trace on reload.
    if(m.interaction&&m.interaction.length&&
       window.Nova&&Nova.ActivityLog){
      try{
        const det=document.createElement('details');det.className='st-trace';
        det.innerHTML='<summary>activity</summary><div class="st-trace-body"></div>';
        el.querySelector('.b').insertBefore(det,el.querySelector('.txt'));
        const lg=Nova.ActivityLog.create(det.querySelector('.st-trace-body'),
          {onModel:openModelPicker});
        m.interaction.forEach(e=>lg.ingest(e));
        lg.finish('done',true);
      }catch(_){}
    }
    attachActs(el,m);
    host.appendChild(el);
    return el;
  }

  function attachActs(el,m){
    const b=el.querySelector('.b');
    const acts=document.createElement('div');acts.className='acts';
    if(m.role==='character'){
      const rw=document.createElement('button');rw.textContent='⟳ reroll';
      rw.onclick=()=>retryBeat(m.id);acts.appendChild(rw);
    }
    if(m.role==='user'||m.role==='narrator'){
      const ed=document.createElement('button');ed.textContent='✎ edit';
      ed.onclick=async()=>{
        const t=prompt('Edit:',m.text);if(t==null||!t.trim())return;
        try{const r=await v1Api(`/v1/roleplay/rooms/${room.id}/messages/${m.id}`,
          {method:'PATCH',body:JSON.stringify({text:t.trim()})});
          m.text=r.message.text;el.querySelector('.txt').textContent=m.text;}
        catch(e){toast2(e.message);}};
      acts.appendChild(ed);
    }
    const del=document.createElement('button');del.textContent='✕';
    del.onclick=async()=>{
      try{await v1Api(`/v1/roleplay/rooms/${room.id}/messages/${m.id}`,
        {method:'DELETE'});
        room.messages=(room.messages||[]).filter(x=>x.id!==m.id);el.remove();}
      catch(e){toast2(e.message);}};
    acts.appendChild(del);
    if(acts.children.length)b.appendChild(acts);
  }

  function renderRich(el,envText,opts){
    const t=el.querySelector('.txt');
    if(!t||typeof NovaRender==='undefined'||
       !NovaRender.ResponseRenderer)return;
    t.innerHTML='';t.classList.add('rich');
    try{NovaRender.ResponseRenderer.renderMessage(
      {role:'assistant',content:envText},t,
      Object.assign({showActions:false,showSuggestions:false,richSources:true},opts||{}));}
    catch(e){t.classList.remove('rich');t.textContent=(opts&&opts.fallback)||'';}
  }

  function attachSwipe(el,mid,variants,variantMsgs){
    const b=el.querySelector('.b');
    let sw=b.querySelector('.swipe');
    if(!sw){sw=document.createElement('div');sw.className='swipe';
      b.appendChild(sw);}
    // variant_messages carries text+env_text+sources per variant (additive);
    // legacy string variants remain supported.
    const vms=Array.isArray(variantMsgs)?variantMsgs:[];
    const total=variants.length+1;
    swipeIdx[mid]=total-1;
    // Viewing a swipe never mutates the canonical room message: each variant
    // paints its own text + envelope (current = el._curEnv / el._text).
    const paint=()=>{
      const i=swipeIdx[mid];
      const current=i===total-1;
      const vm=current?null:vms[i];
      if(current){
        if(el._curEnv)renderRich(el,el._curEnv);
        else el.querySelector('.txt').textContent=el._text||'';
      }else if(vm&&typeof vm==='object'){
        if(vm.env_text)renderRich(el,vm.env_text);
        else el.querySelector('.txt').textContent=vm.text||'';
      }else{
        el.querySelector('.txt').textContent=variants[i]||'';
      }
      sw.innerHTML=`<button data-p>‹</button><span>${i+1}/${total}</span><button data-n>›</button>`;
      sw.querySelector('[data-p]').onclick=()=>{if(swipeIdx[mid]>0){swipeIdx[mid]--;paint();}};
      sw.querySelector('[data-n]').onclick=()=>{if(swipeIdx[mid]<total-1){swipeIdx[mid]++;paint();}};
    };
    el._variantMsgs=vms;
    paint();
  }

  function renderTranscript(){
    const host=$('#transcript');host.innerHTML='';
    const msgs=room.messages||[];
    $('#stageEmpty').style.display=msgs.length?'none':'';
    $('#btnBegin').style.display=msgs.length?'none':'';
    $('#emptyText').textContent=msgs.length?'':
      'The stage is set. Raise the curtain — the cast opens with their greetings.';
    msgs.forEach(appendMsg);
    $('#scroll').style.removeProperty('--st-tail-space');
    scroll();
  }

  // ------------------------------------------------ cast
  function renderCast(){
    const rail=$('#castRail');rail.innerHTML='';
    const you=document.createElement('div');
    you.className='st-face you';
    you.innerHTML=`<div class="av">🧑</div><div class="nm">You</div>`;
    rail.appendChild(you);
    const ids=room?room.character_ids||[]:[];
    ids.forEach(id=>{
      const c=charById(id);if(!c)return;
      const el=document.createElement('div');
      el.className='st-face'+((room.paused_ids||[]).includes(id)?' paused':'');
      el.dataset.cid=id;el.draggable=true;
      el.title='drag to reorder · drag out to remove';
      el.innerHTML=`${face(c)}<div class="nm">${esc(c.name)}</div>`;
      el.ondblclick=()=>setMode('whisper')||($('#whisperTo').value=id,$('#input').focus());
      el.ondragstart=e=>{
        e.dataTransfer.setData('text/plain',String(id));
        e.dataTransfer.effectAllowed='move';};
      el.ondragend=e=>{
        /* Drag out to remove: the drag ended somewhere no reorder target
           accepted (dropEffect 'none') and the pointer left the rail.
           ESC-cancel while still over the rail stays inside bounds. */
        if(e.dataTransfer.dropEffect!=='none')return;
        const r=$('#castRail').getBoundingClientRect();
        if(e.clientX>=r.left&&e.clientX<=r.right
          &&e.clientY>=r.top&&e.clientY<=r.bottom)return;
        room.character_ids=(room.character_ids||[]).filter(x=>x!==id);
        v1Api(`/v1/roleplay/rooms/${room.id}`,{method:'PATCH',
          body:JSON.stringify({character_ids:room.character_ids})})
          .catch(()=>{});
        toast2(`${c.name} left the stage — re-add them from the cast sheet`);
        renderCast();renderChips();renderDirector();};
      el.ondragover=e=>e.preventDefault();
      el.ondrop=e=>{e.preventDefault();
        const drag=parseInt(e.dataTransfer.getData('text/plain'));
        if(!drag||drag===id)return;
        const order=ids.filter(x=>x!==drag);
        order.splice(order.indexOf(id)+1,0,drag);
        room.character_ids=order;
        v1Api(`/v1/roleplay/rooms/${room.id}`,{method:'PATCH',
          body:JSON.stringify({character_ids:order})}).catch(()=>{});
        renderCast();renderChips();renderDirector();};
      rail.appendChild(el);
    });
    renderChips();
  }

  function renderChips(){
    const host=$('#castChips');host.innerHTML='';
    (room?room.character_ids||[]:[]).forEach(id=>{
      const c=charById(id);if(!c)return;
      const paused=(room.paused_ids||[]).includes(id);
      const el=document.createElement('div');
      el.className='st-chip'+(paused?' paused':'');
      el.title='Open dossier';
      el.innerHTML=`<span class="dot"></span>${esc(c.emoji||'')} ${esc(c.name)}`;
      el.onclick=()=>openDossier(id);
      host.appendChild(el);
    });
  }

  // ------------------------------------------------ director panel
  function renderDirector(){
    if(!room)return;
    $('#dScenario').value=room.scenario||'';
    $('#dMood').value=room.mood||'';
    $('#dNotes').value=room.director_notes||'';
    $('#dTools').checked=!!room.tools_enabled;
    paintSceneModel();
    $('#dSummary').textContent=room.summary||'No recap yet — it builds as the scene runs.';
    $('#backdrop').dataset.mood=room.mood||'';
    $('#scenarioLine').textContent=room.scenario||'';
    const host=$('#turnOrder');host.innerHTML='';
    (room.character_ids||[]).forEach((id,i)=>{
      const c=charById(id);if(!c)return;
      const paused=(room.paused_ids||[]).includes(id);
      const el=document.createElement('div');
      el.className='ord'+(paused?' paused':'');el.draggable=true;
      el.innerHTML=`<span>${i+1}.</span> ${esc(c.emoji||'')} ${esc(c.name)}
        <button class="pause-btn" title="${paused?'resume':'pause'}">${paused?'▶':'⏸'}</button>`;
      el.querySelector('.pause-btn').onclick=async()=>{
        const p=new Set(room.paused_ids||[]);
        p.has(id)?p.delete(id):p.add(id);
        room.paused_ids=[...p];
        await v1Api(`/v1/roleplay/rooms/${room.id}`,{method:'PATCH',
          body:JSON.stringify({paused_ids:room.paused_ids})}).catch(()=>{});
        renderDirector();renderCast();};
      el.ondragstart=e=>e.dataTransfer.setData('text/plain',String(id));
      el.ondragover=e=>e.preventDefault();
      el.ondrop=e=>{e.preventDefault();
        const drag=parseInt(e.dataTransfer.getData('text/plain'));
        if(!drag||drag===id)return;
        const order=room.character_ids.filter(x=>x!==drag);
        order.splice(order.indexOf(id)+1,0,drag);
        room.character_ids=order;
        v1Api(`/v1/roleplay/rooms/${room.id}`,{method:'PATCH',
          body:JSON.stringify({character_ids:order})}).catch(()=>{});
        renderDirector();renderCast();};
      host.appendChild(el);
    });
  }

  const SCENE_MODEL_KEY='nova_roleplay_model';
  let sceneModels=[],modelBusy=false;

  function savedSceneModel(){
    try{return localStorage.getItem(SCENE_MODEL_KEY)||'auto';}catch(_){return 'auto';}
  }

  async function loadSceneModels(){
    try{
      const d=await v1Api('/v1/models');
      sceneModels=(d.data||[]);
    }catch(_){}
    paintSceneModel();
  }

  function scenePrice(m){
    const pr=(m&&typeof m.pricing==='object'&&m.pricing)||m||{};
    const inp=pr.input_per_mtok,out=pr.output_per_mtok;
    const ok=v=>v!=null&&v!==''&&Number.isFinite(Number(v));
    if(!ok(inp)||!ok(out))return 'Pricing unavailable';
    if(Number(inp)===0&&Number(out)===0)return 'Free';
    return `$${Number(inp)} in / $${Number(out)} out per 1M tokens`;
  }

  let sceneModelQuery='';

  function sceneModelEntry(id){
    const m=sceneModels.find(x=>x.id===id);
    const usable=!m||m.usable!==false;
    let detail=id==='auto'?'pricing varies':scenePrice(m);
    if(!usable)detail+=' · provider cooling down';
    return {id,label:id==='auto'?'Auto':id,detail,usable};
  }

  function paintSceneModel(){
    const btn=$('#sceneModelBtn'),hidden=$('#sceneModel');
    const current=(room&&room.model)||'auto';
    const entry=sceneModelEntry(current);
    hidden.value=current;
    $('#sceneModelLabel').textContent=`${entry.label} — ${entry.detail}`;
    btn.disabled=!room||modelBusy;
    btn.setAttribute('aria-expanded',
      String($('#sceneModelMenu').classList.contains('open')));
  }

  function openModelPicker(){
    const d=$('#director');
    if(d&&!d.classList.contains('open'))d.classList.add('open');
    const menu=$('#sceneModelMenu');
    if(!menu||!menu.classList.contains('open'))openSceneModelMenu();
  }

  function openSceneModelMenu(){
    const menu=$('#sceneModelMenu');
    if(!room||modelBusy)return;
    menu.classList.toggle('open');
    $('#sceneModelBtn').setAttribute('aria-expanded',
      String(menu.classList.contains('open')));
    if(menu.classList.contains('open'))renderSceneModels();
  }

  function renderSceneModels(){
    const menu=$('#sceneModelMenu');
    const current=(room&&room.model)||'auto';
    const byId=new Map(sceneModels.map(m=>[m.id,m]));
    const ids=['auto',...[...byId.keys()].filter(id=>id!=='auto')];
    if(!ids.includes(current))ids.push(current);
    const q=sceneModelQuery.trim().toLowerCase();
    const list=ids.filter(id=>!q||id.toLowerCase().includes(q));
    const avail=list.filter(id=>{const m=byId.get(id);return !m||m.usable!==false;});
    const cool=list.filter(id=>{const m=byId.get(id);return m&&m.usable===false;});
    const row=id=>{
      const e=sceneModelEntry(id);
      return `<div class="mi ${id===current?'sel':''}" data-id="${esc(id)}" role="option" aria-selected="${id===current}">
        <div><b style="font-size:12.5px;display:block">${esc(e.label)}</b>
        <span class="t-xs tx-3">${esc(e.detail)}</span></div>
        ${id===current?'<span style="margin-left:auto;color:var(--accent)">✓</span>':''}
      </div>`;};
    menu.innerHTML='<input class="mi-search" type="search" placeholder="Search '
      +ids.length+' models…" value="'+esc(sceneModelQuery)+'">'
      +'<div class="mi-head">'+(q?list.length+' of '+ids.length:'Model')+'</div>'
      +(avail.length?avail.map(row).join(''):'')
      +(cool.length?'<div class="mi-head">Cooling down — provider temporarily unavailable</div>'+cool.map(row).join(''):'')
      +(list.length?'':'<div class="mi-empty">No models match “'+esc(sceneModelQuery)+'”</div>');
    const search=menu.querySelector('.mi-search');
    search.addEventListener('input',()=>{sceneModelQuery=search.value;renderSceneModels();
      const s=menu.querySelector('.mi-search');s.focus();s.setSelectionRange(s.value.length,s.value.length);});
    menu.querySelectorAll('.mi[data-id]').forEach(el=>el.onclick=e=>{
      e.stopPropagation();
      $('#sceneModel').value=el.dataset.id;
      sceneModelQuery='';
      menu.classList.remove('open');
      changeSceneModel(el.dataset.id);
    });
    requestAnimationFrame(()=>{search.focus();});
  }

  async function changeSceneModel(next){
    const target=room;
    if(!target||modelBusy)return;
    modelBusy=true;paintSceneModel();
    try{
      await v1Api(`/v1/roleplay/rooms/${target.id}`,{method:'PATCH',
        body:JSON.stringify({model:next})});
      target.model=next;
      const listed=rooms.find(r=>r.id===target.id);
      if(listed)listed.model=next;
      try{localStorage.setItem(SCENE_MODEL_KEY,next);}catch(_){}
    }catch(e){toast2(e.message);}
    finally{modelBusy=false;paintSceneModel();}
  }

  async function patchRoom(){
    if(!room)return;
    room.scenario=$('#dScenario').value;
    room.mood=$('#dMood').value;
    room.director_notes=$('#dNotes').value;
    room.tools_enabled=$('#dTools').checked;
    $('#backdrop').dataset.mood=room.mood;
    $('#scenarioLine').textContent=room.scenario;
    try{await v1Api(`/v1/roleplay/rooms/${room.id}`,{method:'PATCH',
      body:JSON.stringify({scenario:room.scenario,mood:room.mood,
        director_notes:room.director_notes,
        tools_enabled:room.tools_enabled})});}
    catch(e){toast2(e.message);}
  }

  // ------------------------------------------------ rooms / scenes
  async function openRoom(id){
    try{
      room=await v1Api(`/v1/roleplay/rooms/${id}`);
      localStorage.setItem('rp_last',String(id));
      $('#sceneTitle').textContent=room.name;
      renderTranscript();renderCast();renderDirector();renderWorldStrip();
      if((room.messages||[]).length)loadChoices();
    }catch(e){toast2(e.message);}
  }

  function renderEmpty(){
    room=null;
    $('#sceneTitle').textContent='The Stage';
    $('#transcript').innerHTML='';
    $('#stageEmpty').style.display='';
    $('#btnBegin').style.display='none';
    $('#worldStrip').hidden=true;
    $('#choices').hidden=true;
    $('#emptyText').textContent='Pick a scene — or raise the curtain on a new one.';
    renderCast();$('#turnOrder').innerHTML='';
  }

  function renderScenes(){
    const host=$('#sceneList');host.innerHTML='';
    rooms.forEach(r=>{
      const el=document.createElement('div');el.className='st-scene-item';
      el.innerHTML=`<span>🎬</span><div><div style="font-weight:600">${esc(r.name)}</div>
        <div class="meta">${(r.character_ids||[]).length} in cast · ${(r.messages||[]).length} beats</div></div>
        <button class="del" title="delete scene">✕</button>`;
      el.onclick=()=>{$('#scenesVeil').classList.remove('open');openRoom(r.id);};
      el.querySelector('.del').onclick=async e=>{e.stopPropagation();
        if(!confirm('Delete this scene?'))return;
        try{await v1Api(`/v1/roleplay/rooms/${r.id}`,{method:'DELETE'});
          rooms=rooms.filter(x=>x.id!==r.id);
          if(room&&room.id===r.id)renderEmpty();renderScenes();}
        catch(err){toast2(err.message);}};
      host.appendChild(el);
    });
    if(!rooms.length)host.innerHTML='<div style="color:var(--stage-faint);padding:14px">No scenes yet.</div>';
    if(window.NovaMotion)NovaMotion.stagger(host.querySelectorAll('.st-scene-item'),
      {y:10,dur:.25,stagger:.04});
  }

  async function newScene(){
    const name=prompt('Scene name:','A new scene');if(!name)return;
    castPicks=[];renderCastCards();$('#scenesVeil').classList.remove('open');
    pendingRoom=name;$('#castVeil').classList.add('open');
  }
  let pendingRoom=null;

  async function applyCast(){
    $('#castVeil').classList.remove('open');
    try{
      if(pendingRoom){
        const r=await v1Api('/v1/roleplay/rooms',{method:'POST',
          body:JSON.stringify({name:pendingRoom,scenario:'',character_ids:castPicks,
            model:savedSceneModel()})});
        rooms.unshift(r);pendingRoom=null;openRoom(r.id);
      }else if(room){
        room.character_ids=castPicks;
        await v1Api(`/v1/roleplay/rooms/${room.id}`,{method:'PATCH',
          body:JSON.stringify({character_ids:castPicks})});
        renderCast();renderDirector();
      }else toast2('Pick or create a scene first');
    }catch(e){toast2(e.message);}
  }

  async function renameScene(){
    if(!room)return;
    const n=prompt('Scene name:',room.name);if(!n||!n.trim())return;
    room.name=n.trim();$('#sceneTitle').textContent=room.name;
    await v1Api(`/v1/roleplay/rooms/${room.id}`,{method:'PATCH',
      body:JSON.stringify({name:room.name})}).catch(()=>{});
  }

  // ------------------------------------------------ cast sheet & editor
  function renderCastCards(){
    const host=$('#castCards');host.innerHTML='';
    chars.forEach(c=>{
      const inCast=castPicks.includes(c.id);
      const el=document.createElement('div');
      el.className='st-card'+(inCast?' in-cast':'');
      el.innerHTML=`${inCast?'<span class="badge">ON STAGE</span>':''}
        <div class="av">${esc(c.emoji||'🎭')}</div>
        <div class="nm">${esc(c.name)}</div>
        <div class="tl">${esc(c.tagline||c.category||'')}</div>
        <button class="edit-btn">edit</button>`;
      el.onclick=()=>{
        if(castPicks.includes(c.id))castPicks=castPicks.filter(x=>x!==c.id);
        else if(castPicks.length<6)castPicks.push(c.id);
        else return toast2('Cast is full — 6 max');
        renderCastCards();};
      el.querySelector('.edit-btn').onclick=e=>{e.stopPropagation();openEditor(c);};
      host.appendChild(el);
    });
    if(!chars.length)host.innerHTML='<div style="color:var(--stage-faint);padding:14px">No characters yet — create one below.</div>';
    if(window.NovaMotion){
      if(!renderCastCards._painted){renderCastCards._painted=1;
        NovaMotion.stagger(host.querySelectorAll('.st-card'),
          {y:16,scale:.97,dur:.3,stagger:.045});}
      NovaMotion.liftAll('.st-card',host,{y:-4,scale:1.02});
    }
  }

  const F={name:'cf_name',emoji:'cf_emoji',tagline:'cf_tagline',description:'cf_desc',
    personality:'cf_pers',speaking_style:'cf_speak',category:'cf_cat',background:'cf_bg',
    knowledge_domain:'cf_know',motivations:'cf_motiv',boundaries:'cf_bound',
    example_dialogue:'cf_dlg',greeting:'cf_greet'};

  function openEditor(c){
    editId=c?c.id:null;
    $('#charTitle').textContent=c?('Edit '+c.name):'New character';
    for(const[k,id]of Object.entries(F))
      document.getElementById(id).value=c?(c[k]||''):(k==='category'?'custom':'');
    $('#charVeil').classList.add('open');
    if(window.NovaMotion)NovaMotion.veil($('#charVeil'),true);
  }

  async function saveCharacter(){
    const body={};
    for(const[k,id]of Object.entries(F))body[k]=document.getElementById(id).value;
    if(!body.name.trim())return toast2('Name required');
    try{
      if(editId)await v1Api('/v1/characters/'+editId,{method:'PATCH',body:JSON.stringify(body)});
      else await v1Api('/v1/characters',{method:'POST',body:JSON.stringify(body)});
      $('#charVeil').classList.remove('open');toast2('Saved');
      chars=await v1Api('/v1/characters');renderCastCards();renderCast();
    }catch(e){toast2(e.message);}
  }

  // ------------------------------------------------ worlds layer
  const worldOf=()=>(room&&room.world)||{};

  function renderWorldStrip(){
    const w=worldOf(),strip=$('#worldStrip');
    const bits=[];
    if(w.location)bits.push(`<span class="w-loc">📍 ${esc(w.location)}</span>`);
    if(w.world_time)bits.push(`<span>🕰 ${esc(w.world_time)}</span>`);
    const beats=(room.messages||[]).length;
    if(beats)bits.push(`<span>◦ ${beats} beats</span>`);
    if(!bits.length&&!w.title){strip.hidden=true;return;}
    strip.innerHTML=bits.join('<span class="w-sep">·</span>')+
      `<span class="w-mode">${esc(w.mode||'free')}</span>`;
    strip.hidden=false;
  }

  /* contextual 'what do you do' choices */
  let choicesReq=0;
  async function loadChoices(){
    const bar=$('#choices');
    if(!room||streaming){bar.hidden=true;return;}
    const mine=++choicesReq;
    bar.hidden=true;bar.innerHTML='';
    try{
      const r=await v1Api(`/v1/roleplay/rooms/${room.id}/choices`,
        {method:'POST',body:'{}'});
      if(mine!==choicesReq||streaming||!room)return;
      const list=(r.choices||[]).slice(0,10);
      if(!list.length)return;
      const hint=document.createElement('span');
      hint.className='st-choices-hint';hint.textContent='What do you do?';
      bar.appendChild(hint);
      list.forEach(c=>{
        const b=document.createElement('button');
        b.className='st-choice';b.textContent=c;
        b.onclick=()=>{bar.hidden=true;bar.innerHTML='';beat('talk',c);};
        bar.appendChild(b);});
      const diy=document.createElement('button');
      diy.className='st-choice do';diy.textContent='do something else…';
      diy.onclick=()=>{bar.hidden=true;$('#input').focus();};
      bar.appendChild(diy);
      bar.hidden=false;
      if(window.gsap&&!matchMedia('(prefers-reduced-motion: reduce)').matches)
        gsap.from(bar.querySelectorAll('.st-choice'),
          {opacity:0,y:6,duration:.3,stagger:.05,ease:'power2.out'});
    }catch(_){bar.hidden=true;}
  }

  /* world state panel — shows only real room state */
  async function patchWorld(keys){
    try{
      const r=await v1Api(`/v1/roleplay/rooms/${room.id}`,{method:'PATCH',
        body:JSON.stringify({world:keys})});
      room.world=r.world;renderWorldStrip();
    }catch(e){toast2(e.message);}
  }

  function renderWorldPanel(){
    const w=worldOf(),host=$('#worldPanel');
    $('#wTitle').textContent=w.title||room.name;
    $('#wPremise').textContent=w.premise||room.scenario||
      'A stage you set — give it a place, a time, and rules.';
    const cast=(room.character_ids||[]).map(charById).filter(Boolean);
    const MODE_OPTS=['story','free','simulation','character','ensemble','director']
      .map(m=>`<option ${w.mode===m?'selected':''}>${m}</option>`).join('');
    host.innerHTML=`
      <div class="wp-grid">
        <div class="wp-row"><label>Location</label>
          <input id="wpLoc" value="${esc(w.location||'')}" placeholder="where are we?"></div>
        <div class="wp-row"><label>World time</label>
          <input id="wpTime" value="${esc(w.world_time||'')}" placeholder="when is it?"></div>
        <div class="wp-row"><label>Mode</label>
          <select id="wpMode">${MODE_OPTS}</select></div>
        ${(w.rules||[]).length?`<div class="wp-h">World rules</div>
          <ul class="wp-rules">${w.rules.map(r=>`<li>${esc(r)}</li>`).join('')}</ul>`:''}
        <div class="wp-h">Present (${cast.length})</div>
        <div class="wp-cast">${cast.map(c=>
          `<span class="wp-char${(room.paused_ids||[]).includes(c.id)?' paused':''}"
            data-cid="${c.id}"><span class="av" ${c.avatar_url?
            `style="background-image:url(${esc(c.avatar_url)})"`:''}>${
            c.avatar_url?'':esc(c.emoji||'🎭')}</span>${esc(c.name)}</span>`).join('')||
          '<span class="wp-sum">Nobody on stage yet.</span>'}</div>
        <div class="wp-h">Recorded events (${(w.events||[]).length})</div>
        <div class="wp-events">${(w.events||[]).slice().reverse().map(e=>
          `<div class="wp-ev"><span class="k">${esc(e.kind||'note')}</span>
           <span>${esc(e.text)}</span></div>`).join('')||
          '<span class="wp-sum">Nothing recorded yet — log the moments that matter.</span>'}</div>
        <div class="wp-ev-form">
          <input id="wpEvText" placeholder="Log an event — what just changed?">
          <button id="wpEvAdd">＋ Log</button></div>
        <div class="wp-h">Story so far</div>
        <div class="wp-sum">${esc(room.summary||
          'No recap yet — it builds as the scene runs.')}</div>
      </div>`;
    host.querySelectorAll('.wp-char').forEach(el=>
      el.onclick=()=>openDossier(parseInt(el.dataset.cid)));
    const patch=()=>patchWorld({location:$('#wpLoc').value.trim(),
      world_time:$('#wpTime').value.trim(),mode:$('#wpMode').value});
    ['wpLoc','wpTime','wpMode'].forEach(id=>
      document.getElementById(id).addEventListener('change',patch));
    $('#wpEvAdd').onclick=async()=>{
      const t=$('#wpEvText').value.trim();if(!t)return;
      try{
        const r=await v1Api(`/v1/roleplay/rooms/${room.id}/world/events`,
          {method:'POST',body:JSON.stringify({text:t,kind:'event'})});
        room.world=r.world;renderWorldPanel();renderWorldStrip();
      }catch(e){toast2(e.message);}};
  }

  /* character dossier — everything the persona actually holds */
  function openDossier(cid){
    const c=charById(cid);if(!c)return;
    const w=worldOf();
    const rels=(w.relationships||[]).filter(r=>r.from===cid||r.to===cid);
    const relName=id=>id==='user'?'you':(charById(id)||{}).name||'someone';
    const lines=(room?(room.messages||[]):[]).filter(m=>
      m.speaker===c.name&&m.kind!=='ooc').slice(-3);
    const sec=(h,body,q)=>body?
      `<div class="dos-sec"><h5>${h}</h5><p class="${q?'q':''}">${esc(body)}</p></div>`:'';
    $('#dossierBody').innerHTML=`
      <div class="dos">
        <div class="dos-head">
          <div class="dos-face" ${c.avatar_url?
            `style="background-image:url(${esc(c.avatar_url)})"`:''}>${
            c.avatar_url?'':esc(c.emoji||'🎭')}</div>
          <div><h3 class="dos-name">${esc(c.name)}</h3>
            <div class="dos-tag">${esc(c.tagline||'')}</div>
            <div class="dos-cat">${esc(c.category||'character')}${
              (room.paused_ids||[]).includes(c.id)?' · paused':''}</div></div>
        </div>
        ${sec('Who they are',c.description)}
        ${sec('Personality',c.personality)}
        ${sec('Background',c.background)}
        ${sec('Goals & motivations',c.motivations)}
        ${sec('What they know',c.knowledge_domain)}
        ${sec('How they speak',c.speaking_style||c.style)}
        ${sec('Boundaries',c.boundaries)}
        ${sec('In their own voice',c.example_dialogue||c.greeting,true)}
        ${rels.length?`<div class="dos-sec"><h5>Bonds</h5>
          <div class="dos-rels">${rels.map(r=>`<span class="dos-rel">${
            esc(relName(r.from))} — <b>${esc(r.kind)}</b> — ${esc(relName(r.to))}${
            r.label?` · ${esc(r.label)}`:''}</span>`).join('')}</div></div>`:''}
        ${lines.length?`<div class="dos-sec"><h5>Lately, in their own words</h5>
          <div class="dos-lines">${lines.map(m=>
            `<div class="dos-line">${esc((m.text||'').slice(0,160))}</div>`).join('')}</div></div>`:''}
        <div class="dos-sec"><div class="dos-acts">
          <button class="st-btn" id="dosWhisper">🤫 Whisper to them</button>
          <button class="st-btn ghost" id="dosEdit">✎ Edit character</button>
        </div></div>
      </div>`;
    $('#dosWhisper').onclick=()=>{
      $('#dossierVeil').classList.remove('open');
      setMode('whisper');$('#whisperTo').value=cid;$('#input').focus();};
    $('#dosEdit').onclick=()=>{
      $('#dossierVeil').classList.remove('open');openEditor(c);};
    $('#dossierVeil').classList.add('open');
    if(window.NovaMotion)NovaMotion.veil($('#dossierVeil'),true);
  }

  /* relationship constellation — edges are director-curated state */
  const REL_STYLE={trust:{c:'#4dd8ff',d:''},friendship:{c:'#4dd8ff',d:''},
    loyalty:{c:'#4dd8ff',d:''},family:{c:'#7ce8b5',d:''},
    romance:{c:'#ff7eb0',d:''},debt:{c:'#ffd166',d:'6 3'},
    rivalry:{c:'#ff6b6b',d:'7 4'},conflict:{c:'#ff6b6b',d:'7 4'},
    suspicion:{c:'#8b92a8',d:'2 4'}};

  function renderConstellation(){
    const w=worldOf(),host=$('#constellation');
    const cast=(room.character_ids||[]).map(charById).filter(Boolean);
    const rels=(w.relationships||[]);
    const nodeIds=cast.map(c=>c.id);
    if(rels.some(r=>r.from==='user'||r.to==='user'))nodeIds.push('user');
    /* rel selects */
    const opts='<option value="user">You</option>'+
      cast.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
    $('#relFrom').innerHTML=opts;$('#relTo').innerHTML=opts;
    if($('#relTo').options.length>1)$('#relTo').selectedIndex=1;
    if(!nodeIds.length){host.innerHTML='<div class="rel-empty">'+
      'No one on stage — add characters to see their bonds.</div>';return;}
    const W=440,H=340,cx=W/2,cy=H/2,R=Math.min(W,H)/2-52;
    const pos={};
    nodeIds.forEach((id,i)=>{
      const a=-Math.PI/2+i*2*Math.PI/nodeIds.length;
      pos[id]={x:cx+R*Math.cos(a),y:cy+R*Math.sin(a)};});
    const edges=rels.filter(r=>pos[r.from]&&pos[r.to]).slice(-24);
    const edgeSvg=edges.map((r,i)=>{
      const a=pos[r.from],b=pos[r.to],st=REL_STYLE[r.kind]||REL_STYLE.trust;
      const mx=(a.x+b.x)/2,my=(a.y+b.y)/2;
      const dx=b.x-a.x,dy=b.y-a.y,len=Math.hypot(dx,dy)||1;
      const ox=-dy/len*14,oy=dx/len*14;
      const qx=mx+ox,qy=my+oy;
      return `<path d="M${a.x} ${a.y} Q${qx} ${qy} ${b.x} ${b.y}" fill="none"
        stroke="${st.c}" stroke-width="2" stroke-dasharray="${st.d}" opacity=".75"/>
        ${r.label?`<text class="rel-edge-label" x="${qx}" y="${qy-4}"
          text-anchor="middle">${esc(r.label.slice(0,26))}</text>`:''}`;}).join('');
    const nodeSvg=nodeIds.map(id=>{
      const p=pos[id],isYou=id==='user';
      const c=isYou?{name:'You',emoji:'🧑'}:charById(id);
      return `<g class="rel-node${isYou?' you':''}" data-cid="${id}" transform="translate(${p.x},${p.y})">
        <circle r="24" stroke-width="1.5"/>
        <text y="5" text-anchor="middle" font-size="17">${esc(c.emoji||'🎭')}</text>
        <text y="40" text-anchor="middle" font-size="11.5"
          font-weight="600">${esc(c.name.slice(0,14))}</text></g>`;}).join('');
    host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}"
      role="img" aria-label="Relationship map">${edgeSvg}${nodeSvg}</svg>`+
      `<div class="rel-key">${Object.entries(REL_STYLE).slice(0,6).map(([k,v])=>
        `<span><i style="border-color:${v.c}"></i>${k}</span>`).join('')}</div>`;
    host.querySelectorAll('.rel-node').forEach(g=>g.onclick=()=>{
      const id=g.dataset.cid;
      if(id==='user')return;
      $('#relVeil').classList.remove('open');openDossier(parseInt(id));});
  }

  async function addRelation(){
    if(!room)return;
    const w=worldOf();
    const rel={from:isNaN(parseInt($('#relFrom').value))?'user':parseInt($('#relFrom').value),
      to:isNaN(parseInt($('#relTo').value))?'user':parseInt($('#relTo').value),
      kind:$('#relKind').value,label:$('#relLabel').value.trim().slice(0,80)};
    if(rel.from===rel.to)return toast2('A bond needs two different people');
    const list=[...(w.relationships||[])];
    const dup=list.findIndex(r=>r.from===rel.from&&r.to===rel.to);
    if(dup>=0)list[dup]=rel;else list.push(rel);
    await patchWorld({relationships:list.slice(-40)});
    $('#relLabel').value='';renderConstellation();
  }

  /* narrative timeline — transcript + recorded events, honestly ordered */
  function renderTimeline(){
    const w=worldOf(),host=$('#timeline');
    const msgs=room.messages||[];
    const seen=new Set();
    const items=[];
    (w.events||[]).slice(0,2).forEach(e=>
      items.push({cls:'mark',dot:'⚑',who:'recorded',text:e.text}));
    msgs.forEach(m=>{
      const kind=m.kind||'dialogue';
      if(kind==='narration')items.push({cls:'narr',dot:'📜',
        who:'narration',text:(m.text||'').slice(0,220)});
      else if(kind==='ooc')items.push({cls:'mark',dot:'((',who:"director's note",
        text:(m.text||'').slice(0,160)});
      else if(m.role==='user')items.push({cls:'user',dot:'🧑',
        who:kind==='whisper'?'you · whispered':'you',
        text:(m.text||'').slice(0,220)});
      else{
        if(m.speaker&&!seen.has(m.speaker)){seen.add(m.speaker);
          items.push({cls:'mark',dot:'✦',who:'entrance',
            text:`${m.speaker} enters the story`});}
        items.push({cls:'narr',dot:m.emoji||'🎭',who:m.speaker||'',
          text:(m.text||'').slice(0,220)});}
    });
    host.innerHTML=items.length?items.map(i=>
      `<div class="tl-item ${i.cls}"><div class="tl-dot">${i.dot}</div>
       <div class="tl-body"><span class="who">${esc(i.who)}</span>
       <span class="t">${esc(i.text)}</span></div></div>`).join('')
      :'<div class="tl-empty">The story hasn\'t started — write the first beat.</div>';
  }

  AppNav.attach(document.getElementById('appNavBtn'),'roleplay');
  /* Transcript entrances: new beats rise in once; stream edits inside an
     existing .st-msg never retrigger. Bulk room loads animate the tail. */
  if(window.NovaMotion)NovaMotion.watch($('#transcript'),'.st-msg',
    {batch:true,max:6,y:12,dur:.3});
  init();
})();
