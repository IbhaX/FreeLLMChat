/* The Stage — immersive roleplay: streaming turns, presence, swipe,
   narrator/ooc/whisper composer modes, director panel. */
(function(){
  const $=s=>document.querySelector(s);
  const $$=s=>[...document.querySelectorAll(s)];
  let chars=[],rooms=[],room=null,mode='talk',beatOnly=false,
      editId=null,castPicks=[],streaming=null,swipeIdx={};

  const toast2=(m,k)=>{const t=$('#toast');t.textContent=m;
    t.className='st-toast show';clearTimeout(t._t);
    t._t=setTimeout(()=>t.classList.remove('show'),2200);};
  const face=(c,sz)=>`<div class="av" style="${c&&c.avatar_url?
    `background-image:url(${esc(c.avatar_url)});background-size:cover`:''}">${c&&c.avatar_url?'':esc(c&&c.emoji||'🎭')}</div>`;
  const charById=id=>chars.find(c=>c.id===id);
  const scroll=()=>{const s=$('#scroll');s.scrollTop=s.scrollHeight;};

  // ------------------------------------------------ bootstrap
  async function init(){
    bind();
    try{
      chars=await v1Api('/v1/characters');
      const d=await v1Api('/v1/roleplay/rooms');
      rooms=d.rooms||[];
      const last=parseInt(localStorage.getItem('rp_last')||'0');
      const r=rooms.find(x=>x.id===last)||rooms[0];
      if(r)openRoom(r.id);else{renderEmpty();renderCast();}
      // resume an in-flight turn
      if(r){const live=await v1Api(`/v1/roleplay/rooms/${r.id}/live`).catch(()=>({}));
        if(live&&live.running)consume(`/v1/roleplay/rooms/${r.id}/live/stream`);}
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
    $('#btnScenes').onclick=()=>{renderScenes();$('#scenesVeil').classList.add('open');};
    $('#btnCast').onclick=()=>{castPicks=[...(room?room.character_ids||[]:[])];
      renderCastCards();$('#castVeil').classList.add('open');};
    $('#btnDirector').onclick=()=>$('#director').classList.toggle('open');
    $('#btnNewScene').onclick=newScene;
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
      try{const r=await v1Api(`/v1/roleplay/rooms/${room.id}/summary`,{method:'POST'});
        room.summary=r.summary;renderDirector();toast2('Story recap updated');}
      catch(e){toast2(e.message);}};
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
    try{
      const r=await fetch(API_BASE+`/v1/roleplay/rooms/${room.id}/turn`,{
        method:'POST',headers:apiHeaders(false),
        body:JSON.stringify({kind,text,whisper_to:whisperTo||null,
          advance:advanceOverride!=null?advanceOverride:!beatOnly})});
      if(!r.ok)throw new Error('turn failed: '+r.status);
      await consumeStream(r);
    }catch(e){toast2(e.message);}
    finally{streaming=false;setRunning(false);}
  }

  async function consume(path){
    streaming=true;setRunning(true);
    try{const r=await fetch(API_BASE+path,{headers:apiHeaders(false)});
      if(r.ok)await consumeStream(r);}
    finally{streaming=false;setRunning(false);}
  }

  async function retryBeat(mid){
    if(!room||streaming)return;
    streaming=true;setRunning(true);
    try{
      const r=await fetch(API_BASE+`/v1/roleplay/rooms/${room.id}/retry`,{
        method:'POST',headers:apiHeaders(false),
        body:JSON.stringify({message_id:mid})});
      if(!r.ok)throw new Error('retry failed: '+r.status);
      await consumeStream(r);
    }catch(e){toast2(e.message);}
    finally{streaming=false;setRunning(false);}
  }

  function setRunning(on){
    $('#btnRun').disabled=on;
    if(on)$('#queueLine').innerHTML='<b>Curtain up…</b>';
    else if(!streaming)$('#queueLine').textContent='';
  }

  async function consumeStream(resp){
    const rd=resp.body.getReader(),dec=new TextDecoder();
    let buf='';
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
          if(data==='[DONE]')return;
          try{const j=JSON.parse(data);
            if(j.freellm)onEvent(j.freellm);}catch{}
        }
      }
    }
  }

  function onEvent(ev){
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
        b.classList.add('writing');b._streaming=true;break;}
      case 'char_delta':{
        const el=document.getElementById('m-'+ev.id);
        if(el){el._text=(el._text||'')+ev.text;
          el.querySelector('.txt').textContent=el._text;scroll();}break;}
      case 'char_skip':{
        const el=document.getElementById('m-'+ev.id);
        if(el)el.remove();
        presence(ev.character_id,false);break;}
      case 'char_end':{
        const el=document.getElementById('m-'+ev.id);
        if(el){el.classList.remove('writing');el._streaming=false;
          if(ev.text&&el._text!==ev.text){el._text=ev.text;
            el.querySelector('.txt').textContent=ev.text;}
          if(ev.variants&&ev.variants.length)attachSwipe(el,ev.id,ev.variants);}
        // keep room.messages in sync
        if(room){const m=(room.messages||[]).find(x=>x.id===ev.id);
          if(m){m.text=ev.text;if(ev.variants)m.variants=ev.variants;}}
        break;}
      case 'turn_done':{
        if(room)room.messages=ev.messages;
        $('#queueLine').textContent='';renderCast();
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
      <div class="b"><div class="who">${who}</div>
      <div class="txt">${esc(m.text||'')}</div></div>`;
    el._text=m.text||'';
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

  function attachSwipe(el,mid,variants){
    const b=el.querySelector('.b');
    let sw=b.querySelector('.swipe');
    if(!sw){sw=document.createElement('div');sw.className='swipe';
      b.appendChild(sw);}
    const total=variants.length+1;
    swipeIdx[mid]=total-1;
    const paint=()=>{
      const i=swipeIdx[mid];
      const txt=i===total-1?(el._saved||el._text):variants[i];
      el.querySelector('.txt').textContent=txt;
      sw.innerHTML=`<button data-p>‹</button><span>${i+1}/${total}</span><button data-n>›</button>`;
      sw.querySelector('[data-p]').onclick=()=>{if(swipeIdx[mid]>0){swipeIdx[mid]--;paint();}};
      sw.querySelector('[data-n]').onclick=()=>{if(swipeIdx[mid]<total-1){swipeIdx[mid]++;paint();}};
    };
    el._saved=el._saved||el._text;
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
      el.dataset.cid=id;el.draggable=true;el.title='drag to reorder';
      el.innerHTML=`${face(c)}<div class="nm">${esc(c.name)}</div>`;
      el.ondblclick=()=>setMode('whisper')||($('#whisperTo').value=id,$('#input').focus());
      el.ondragstart=e=>e.dataTransfer.setData('text/plain',String(id));
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
      el.innerHTML=`<span class="dot"></span>${esc(c.emoji||'')} ${esc(c.name)}`;
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
      renderTranscript();renderCast();renderDirector();
    }catch(e){toast2(e.message);}
  }

  function renderEmpty(){
    room=null;
    $('#sceneTitle').textContent='The Stage';
    $('#transcript').innerHTML='';
    $('#stageEmpty').style.display='';
    $('#btnBegin').style.display='none';
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
          body:JSON.stringify({name:pendingRoom,scenario:'',character_ids:castPicks})});
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

  AppNav.attach(document.getElementById('appNavBtn'),'roleplay');
  init();
})();
