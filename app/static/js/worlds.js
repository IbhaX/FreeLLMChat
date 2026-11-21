/* Worlds — the cinematic roleplay hub: hero, featured catalogue, your
   universe, genre discovery and the AI-assisted World Studio. */
(function(){
  const $=s=>document.querySelector(s);
  const $$=s=>[...document.querySelectorAll(s)];
  let worlds=[],rooms=[],genre='';
  const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,
    c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const toast=m=>{const t=$('#toast');t.textContent=m;
    t.className='st-toast show';clearTimeout(t._t);
    t._t=setTimeout(()=>t.classList.remove('show'),2400);};
  const SCENE='/roleplay/scene';

  /* ---------- art ---------- */
  function artStyle(art){
    const p=(art&&art.palette)||['#0b1026','#1b2a52','#79C9ED'];
    return `background:linear-gradient(135deg,${p[0]},${p[1]} 55%,${p[2]}33),`+
      `radial-gradient(80% 120% at 80% 0%,${p[2]}30,transparent)`;
  }

  /* ---------- featured worlds ---------- */
  function card(w){
    const el=document.createElement('article');
    el.className='wl-card';el.tabIndex=0;
    el.setAttribute('role','link');
    el.setAttribute('aria-label','Enter '+w.title);
    el.innerHTML=
      `<div class="wl-art" style="${artStyle(w.art)}">
         <div class="noise"></div>
         <span class="emo">${esc(w.art&&w.art.emoji||'🌍')}</span>
         <span class="wl-genre">${esc(w.genre)}</span></div>
       <div class="wl-body">
         <h3>${esc(w.title)}</h3>
         <p class="premise">${esc(w.premise)}</p>
         <div class="wl-meta">
           <span class="wl-badge mode">${esc(w.mode)}</span>
           <span><b>${w.char_count}</b> characters</span>
           <span><b>${(w.rules||[]).length}</b> rules</span></div>
         <div class="wl-enter">
           <span class="loc">${esc(w.location||'')}</span>
           <span class="go">Enter →</span></div></div>`;
    const go=()=>enterWorld(w.id);
    el.onclick=go;
    el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}};
    return el;
  }

  async function enterWorld(wid,blueprint){
    try{
      toast(wid==='custom'?'Building your world…':'Opening the world…');
      const r=await v1Api(`/v1/roleplay/worlds/${encodeURIComponent(wid)}/enter`,
        {method:'POST',body:JSON.stringify(blueprint?{blueprint}:{})});
      localStorage.setItem('rp_last',String(r.id));
      location.href=`${SCENE}?room=${r.id}`;
    }catch(e){toast(e.message||'Could not enter world');}
  }

  /* ---------- your universe ---------- */
  function renderRooms(){
    const box=$('#roomList');box.innerHTML='';
    if(!rooms.length){$('#universe').style.display='none';return;}
    $('#universe').style.display='';
    rooms.slice(0,12).forEach(r=>{
      const w=r.world||{};
      const el=document.createElement('div');
      el.className='wl-row';el.tabIndex=0;el.setAttribute('role','link');
      const nCast=(r.character_ids||[]).length;
      const nMsgs=(r.messages||[]).length;
      const when=(r.updated_at||'').slice(0,10);
      el.innerHTML=
        `<div class="rart" style="${artStyle(w.art)}">${esc(w.art&&w.art.emoji||'🌍')}</div>
         <div class="rmain">
           <div class="rname">${esc(w.title||r.name)}</div>
           <div class="rsub">${esc(w.location||r.scenario||'A stage you set')}</div></div>
         <div class="rright">
           <span>${nCast} in cast</span><span>${nMsgs} beats</span>
           <span>${esc(when)}</span><span class="go">Continue →</span></div>`;
      const go=()=>{localStorage.setItem('rp_last',String(r.id));
        location.href=`${SCENE}?room=${r.id}`;};
      el.onclick=go;
      el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}};
      box.appendChild(el);});
  }

  /* ---------- discover ---------- */
  const GENRES=['fantasy','science fiction','mystery','psychological',
    'realistic','philosophical','adventure','horror','romance'];
  function renderGenres(){
    const box=$('#genreChips');box.innerHTML='';
    const all=document.createElement('button');
    all.className='wl-chip'+(!genre?' on':'');all.textContent='All';
    all.onclick=()=>{genre='';renderGenres();renderDiscover();};
    box.appendChild(all);
    GENRES.forEach(g=>{
      const b=document.createElement('button');
      b.className='wl-chip'+(genre===g?' on':'');
      b.textContent=g;
      b.onclick=()=>{genre=genre===g?'':g;renderGenres();renderDiscover();};
      box.appendChild(b);});
  }
  function renderDiscover(){
    const box=$('#discoverGrid');box.innerHTML='';
    const list=worlds.filter(w=>!genre||w.genre===genre||
      (genre==='realistic'&&/interview/i.test(w.id)));
    if(!list.length){box.innerHTML='<div class="wl-empty">'+
      'Nothing here yet — create it in the World Studio.</div>';return;}
    list.forEach(w=>box.appendChild(card(w)));
  }

  /* ---------- studio ---------- */
  let blueprint=null;
  function openStudio(){$('#studioVeil').classList.add('open');
    if(window.NovaMotion)NovaMotion.veil($('#studioVeil'),true);
    setTimeout(()=>$('#stConcept').focus(),60);}
  async function generate(){
    const concept=$('#stConcept').value.trim();
    if(!concept&&!confirm('No concept given — generate a world from thin air?'))return;
    $('#stStatus').textContent='Dreaming up a world…';
    $('#stGenerate').disabled=true;
    try{
      const r=await v1Api('/v1/roleplay/worlds/blueprint',{method:'POST',
        body:JSON.stringify({concept,
          genre:$('#stGenre').value,mood:$('#stMood').value,
          cast_size:parseInt($('#stCast').value)||3})});
      blueprint=r.blueprint;renderPreview(blueprint);
      $('#stStatus').textContent='';
    }catch(e){$('#stStatus').textContent=e.message||'Generation failed';}
    finally{$('#stGenerate').disabled=false;}
  }
  function renderPreview(bp){
    $('#bpPreview').style.display='';
    $('#bpEmoji').textContent=(bp.characters&&bp.characters[0]&&
      bp.characters[0].emoji)||'🌍';
    $('#bpTitle').textContent=bp.title||'Untitled world';
    $('#bpPremise').textContent=bp.premise||'';
    $('#bpMeta').innerHTML='';
    [bp.location,bp.world_time].filter(Boolean).forEach(m=>{
      const s=document.createElement('span');s.textContent=m;
      $('#bpMeta').appendChild(s);});
    (bp.rules||[]).forEach(r=>{
      const s=document.createElement('span');s.textContent='⚑ '+r;
      $('#bpMeta').appendChild(s);});
    $('#bpScenario').textContent=bp.scenario||'';
    const cs=$('#bpChars');cs.innerHTML='';
    (bp.characters||[]).forEach(c=>{
      const d=document.createElement('div');d.className='wl-bp-char';
      d.innerHTML=`<span class="e">${esc(c.emoji||'🎭')}</span>
        <div><div class="n">${esc(c.name)}</div>
        <div class="t">${esc(c.tagline||'')}</div>
        <div class="d">${esc(c.description||'')}</div></div>`;
      cs.appendChild(d);});
    if(window.gsap&&!matchMedia('(prefers-reduced-motion: reduce)').matches)
      gsap.from('#bpPreview',{opacity:0,y:12,duration:.4,ease:'power2.out'});
  }
  async function createBlank(){
    const name=$('#stConcept').value.trim().split('\n')[0].slice(0,80)
      ||'New World';
    try{
      const r=await v1Api('/v1/roleplay/rooms',{method:'POST',
        body:JSON.stringify({name,scenario:$('#stConcept').value.trim()})});
      localStorage.setItem('rp_last',String(r.id));
      location.href=`${SCENE}?room=${r.id}`;
    }catch(e){toast(e.message||'Could not create world');}
  }

  /* ---------- hero actions ---------- */
  async function init(){
    $('#btnStudio').onclick=openStudio;
    $('#ctaCreate').onclick=openStudio;
    $('#stGenerate').onclick=generate;
    $('#stBlank').onclick=createBlank;
    $('#bpEnter').onclick=()=>blueprint&&enterWorld('custom',blueprint);
    $('#stCast').oninput=e=>$('#stCastVal').textContent=e.target.value;
    $('#ctaRandom').onclick=()=>{
      const pool=worlds.length?worlds:[{id:'last-human'}];
      enterWorld(pool[Math.floor(Math.random()*pool.length)].id);};
    $$('[data-close]').forEach(b=>b.onclick=()=>b.closest('.wl-veil')
      .classList.remove('open'));
    document.addEventListener('keydown',e=>{
      if(e.key==='Escape')$$('.wl-veil.open').forEach(v=>v.classList.remove('open'));});
    try{
      const [w,r]=await Promise.all([
        v1Api('/v1/roleplay/worlds'),v1Api('/v1/roleplay/rooms')]);
      worlds=w.worlds||[];rooms=r.rooms||[];
      const fg=$('#featuredGrid');
      worlds.forEach(x=>fg.appendChild(card(x)));
      renderRooms();renderGenres();renderDiscover();
      if(rooms.length){
        const top=rooms[0],w0=top.world||{};
        $('#ctaContinue').style.display='';
        $('#ctaContinueName').textContent=w0.title||top.name;
        $('#ctaContinue').onclick=()=>{
          localStorage.setItem('rp_last',String(top.id));
          location.href=`${SCENE}?room=${top.id}`;};
      }
      if(window.gsap&&!matchMedia('(prefers-reduced-motion: reduce)').matches){
        gsap.from('.wl-hero-in > *',{opacity:0,y:18,duration:.7,
          stagger:.09,ease:'power3.out'});
        gsap.from('#featuredGrid .wl-card',{opacity:0,y:22,duration:.55,
          stagger:.06,delay:.35,ease:'power2.out'});}
    }catch(e){toast(e.message||'Could not load worlds');}
  }
  init();
})();
