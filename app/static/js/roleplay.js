/* Roleplay Studio — character library, editor, multi-character rooms. */
(function(){
  const $=s=>document.querySelector(s);
  let chars=[],rooms=[],activeRoom=null,editId=null;

  AdminShell.mount('roleplay');
  window.addEventListener('admin-ready',init);
  if(ADMIN_TOKEN)init();

  function init(){
    document.querySelectorAll('.rp-tab').forEach(t=>t.onclick=()=>switchTab(t.dataset.tab));
    $('#newCharBtn').onclick=()=>openEditor(null);
    $('#charSave').onclick=saveCharacter;
    $('#newRoomBtn').onclick=newRoom;
    $('#sendBtn').onclick=sendMsg;
    $('#roomInput').addEventListener('keydown',e=>{if(e.key==='Enter')sendMsg();});
    loadChars();loadRooms();
  }

  function switchTab(t){
    document.querySelectorAll('.rp-tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===t));
    $('#tabChars').style.display=t==='chars'?'':'none';
    $('#tabRooms').style.display=t==='rooms'?'':'none';
  }

  // ---------------------------------------------------------- characters
  async function loadChars(){
    try{
      chars=await v1Api('/v1/characters');
      const g=$('#charGrid');g.innerHTML='';
      chars.forEach(c=>{
        const el=document.createElement('div');
        el.className='ui-card ui-card-pad rp-card';
        el.innerHTML=`<div style="display:flex;gap:10px;align-items:center;margin-bottom:8px">
            <div class="rp-face">${c.emoji||'🎭'}</div>
            <div><div style="font-weight:700">${esc(c.name)}</div>
            <div class="tx-3" style="font-size:var(--t-xs)">${esc(c.tagline||c.category)}</div></div></div>
          <div class="tx-2" style="font-size:var(--t-sm);line-height:1.4;min-height:36px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(c.description||'')}</div>
          <div style="display:flex;gap:6px;margin-top:10px;align-items:center">
            <span class="rp-tag">${esc(c.category||'custom')}</span>
            <span style="flex:1"></span>
            <button class="ui-btn ui-btn-sm" data-edit>Edit</button>
            <button class="ui-btn ui-btn-sm ui-btn-primary" data-chat>Chat</button>
          </div>`;
        el.querySelector('[data-edit]').onclick=e=>{e.stopPropagation();openEditor(c);};
        el.querySelector('[data-chat]').onclick=e=>{e.stopPropagation();chatWith(c);};
        g.appendChild(el);
      });
      if(!chars.length)g.innerHTML='<div class="tx-3" style="padding:20px">No characters yet — create one.</div>';
    }catch(e){toast(e.message,'err');}
  }

  const F={name:'cf_name',emoji:'cf_emoji',tagline:'cf_tagline',description:'cf_desc',
    personality:'cf_pers',speaking_style:'cf_speak',category:'cf_cat',background:'cf_bg',
    knowledge_domain:'cf_know',motivations:'cf_motiv',boundaries:'cf_bound',
    example_dialogue:'cf_dlg',greeting:'cf_greet'};

  function openEditor(c){
    editId=c?c.id:null;
    $('#charModalTitle').textContent=c?('Edit '+c.name):'New character';
    for(const[k,id]of Object.entries(F))document.getElementById(id).value=c?(c[k]||''):(k==='category'?'custom':'');
    if(!c)document.getElementById('cf_cat').value='custom';
    $('#charModal').style.display='grid';
  }

  async function saveCharacter(){
    const body={};
    for(const[k,id]of Object.entries(F))body[k]=document.getElementById(id).value;
    if(!body.name.trim()){toast('Name required','err');return;}
    try{
      if(editId)await v1Api('/v1/characters/'+editId,{method:'PATCH',body:JSON.stringify(body)});
      else await v1Api('/v1/characters',{method:'POST',body:JSON.stringify(body)});
      $('#charModal').style.display='none';toast('Saved');loadChars();
    }catch(e){toast(e.message,'err');}
  }

  async function chatWith(c){
    switchTab('rooms');
    await createRoom(`${c.name} — 1:1`, '', [c.id]);
  }

  // ---------------------------------------------------------------- rooms
  async function loadRooms(){
    try{
      const d=await v1Api('/v1/roleplay/rooms');
      rooms=d.rooms||[];
      const host=$('#roomList');host.innerHTML='';
      rooms.forEach(r=>{
        const el=document.createElement('div');
        el.className='rs-run ui-navitem';
        el.style.padding='9px 10px';
        el.innerHTML=`<div style="font-weight:600;font-size:var(--t-sm)">${esc(r.name)}</div>
          <div class="tx-3" style="font-size:10px">${(r.character_ids||[]).length} chars · ${(r.messages||[]).length} msgs</div>`;
        el.onclick=()=>openRoom(r.id);
        host.appendChild(el);
      });
      if(!rooms.length)host.innerHTML='<div class="tx-3 t-sm" style="padding:12px">No rooms yet.</div>';
    }catch(e){toast(e.message,'err');}
  }

  async function newRoom(){
    if(!chars.length){toast('Create a character first','err');return;}
    const name=prompt('Room name:','New scene');
    if(!name)return;
    const scenario=prompt('Scenario (optional):','')||'';
    const picks=await pickCharacters();
    if(!picks.length){toast('Pick at least one character','err');return;}
    await createRoom(name,scenario,picks);
  }

  function pickCharacters(){
    // simple multi-pick via comma-separated numbers
    const menu=chars.map((c,i)=>`${i+1}. ${c.name}`).join('\n');
    const ans=prompt('Characters in the room (numbers, comma separated):\n'+menu,'1');
    if(!ans)return Promise.resolve([]);
    const ids=ans.split(',').map(x=>chars[parseInt(x.trim())-1]).filter(Boolean).map(c=>c.id);
    return Promise.resolve(ids.slice(0,6));
  }

  async function createRoom(name,scenario,ids){
    try{
      const r=await v1Api('/v1/roleplay/rooms',{method:'POST',
        body:JSON.stringify({name,scenario,character_ids:ids})});
      await loadRooms();openRoom(r.id);
    }catch(e){toast(e.message,'err');}
  }

  async function openRoom(id){
    try{
      activeRoom=await v1Api('/v1/roleplay/rooms/'+id);
      $('#roomEmpty').style.display='none';
      $('#roomPane').style.display='';
      $('#roomTitle').textContent=activeRoom.name;
      const members=chars.filter(c=>(activeRoom.character_ids||[]).includes(c.id));
      $('#roomChars').textContent='Cast: '+members.map(c=>`${c.emoji||''} ${c.name}`).join(' · ');
      const sel=$('#speakerSel');sel.innerHTML='<option value="">Everyone replies</option>';
      members.forEach(c=>{const o=document.createElement('option');o.value=c.id;o.textContent=c.name;sel.appendChild(o);});
      renderTranscript();
    }catch(e){toast(e.message,'err');}
  }

  function renderTranscript(){
    const host=$('#transcript');host.innerHTML='';
    (activeRoom.messages||[]).forEach(m=>{
      const el=document.createElement('div');
      el.className='rp-msg'+(m.role==='user'?' user':'');
      el.innerHTML=`<div class="av">${m.role==='user'?'🧑':(m.emoji||'🎭')}</div>
        <div class="b"><div class="who">${esc(m.speaker||'')}</div>
        <div class="txt">${esc(m.text||'')}</div></div>`;
      host.appendChild(el);
    });
    host.scrollTop=host.scrollHeight;
  }

  async function sendMsg(){
    const text=$('#roomInput').value.trim();
    if(!text||!activeRoom)return;
    $('#roomInput').value='';$('#sendBtn').disabled=true;
    const speaker=$('#speakerSel').value||null;
    try{
      const r=await v1Api('/v1/roleplay/rooms/'+activeRoom.id+'/message',{method:'POST',
        body:JSON.stringify({text,speaker:speaker?parseInt(speaker):null})});
      activeRoom.messages=r.messages;
      renderTranscript();
    }catch(e){toast(e.message,'err');}
    finally{$('#sendBtn').disabled=false;}
  }
})();
