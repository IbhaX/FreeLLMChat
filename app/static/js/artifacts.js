/* Artifactory — browse, preview and manage every file agents generate. */
(function(){
  const $=s=>document.querySelector(s);
  const ICONS={document:'📄',data:'📊',image:'🖼️',audio:'🔊',video:'🎬',
    code:'</>',archive:'📦',file:'📎'};
  let _items=[];

  AdminShell.mount('artifacts');
  window.addEventListener('admin-ready',init);
  if(ADMIN_TOKEN||USER_TOKEN)init();

  function init(){
    $('#refreshBtn').onclick=load;
    $('#kindSel').onchange=render;
    $('#sq').addEventListener('input',render);
    load();
  }

  function fmtSize(b){
    if(b>=1048576)return (b/1048576).toFixed(1)+' MB';
    if(b>=1024)return (b/1024).toFixed(1)+' KB';
    return (b||0)+' B';
  }
  function thumb(a){
    if(a.kind==='image')return `<img src="${esc(a.url)}" alt="" loading="lazy">`;
    return `<span>${ICONS[a.kind]||ICONS.file}</span>`;
  }
  function matches(a){
    const q=($('#sq').value||'').toLowerCase();
    const k=$('#kindSel').value;
    if(k&&a.kind!==k)return false;
    if(q&&!(a.name+' '+(a.title||'')+' '+(a.source||'')).toLowerCase().includes(q))
      return false;
    return true;
  }

  async function load(){
    try{
      const d=await v1Api('/v1/artifacts');
      _items=d.artifacts||[];
      render();
    }catch(e){toast(e.message,'err');}
  }

  function render(){
    const host=$('#grid');
    const items=_items.filter(matches);
    host.innerHTML=items.map((a,i)=>`
      <div class="ui-card af-card" data-i="${i}">
        <div class="af-thumb">${thumb(a)}</div>
        <div class="af-body">
          <div class="af-name" title="${esc(a.name)}">${esc(a.title||a.name)}</div>
          <div class="af-meta">
            <span class="af-pill">${esc(a.kind)}</span>
            <span class="af-src" title="${esc(a.source)}">${esc(a.source||'agent')} · ${fmtSize(a.size)}</span>
          </div>
        </div>
      </div>`).join('');
    $('#empty').hidden=items.length>0;
    host.querySelectorAll('.af-card').forEach(el=>
      el.onclick=()=>openModal(items[+el.dataset.i]));
    if(window.NovaMotion&&!render._painted&&items.length){render._painted=1;
      NovaMotion.stagger(host.children,{y:10,dur:.25,stagger:.02});}
  }

  async function previewBody(a){
    const t=a.kind;
    if(t==='image')return `<img src="${esc(a.url)}" alt="${esc(a.name)}">`;
    if(t==='audio')return `<audio controls src="${esc(a.url)}" style="width:100%"></audio>`;
    if(t==='video')return `<video controls src="${esc(a.url)}" style="width:100%"></video>`;
    if((a.mime||'').includes('html'))return `<iframe src="${esc(a.url)}" sandbox="allow-scripts"></iframe>`;
    if(t==='code'||t==='data'||/\.(txt|md|csv|json|ya?ml|xml|sql|py|js|ts|log)$/i.test(a.name)){
      try{
        const txt=await (await fetch(a.url)).text();
        return `<pre>${esc(txt.slice(0,40000))}${txt.length>40000?'\n…(truncated)':''}</pre>`;
      }catch(_){/* fall through */}
    }
    return `<div class="af-empty" style="padding:30px">
      <div style="font-size:42px">${ICONS[t]||ICONS.file}</div>
      <p>No inline preview for this file type — download it below.</p></div>`;
  }

  async function openModal(a){
    const old=document.getElementById('afModal');if(old)old.remove();
    const d=document.createElement('dialog');
    d.id='afModal';d.className='nova-dialog stat-modal';
    const when=a.created_at?new Date(a.created_at).toLocaleString():'';
    d.innerHTML=`
      <h2>${esc(a.title||a.name)}</h2>
      <div class="af-meta" style="margin-bottom:10px">
        <span class="af-pill">${esc(a.kind)}</span>
        <span class="af-src">${esc(a.name)} · ${fmtSize(a.size)}${a.mime?' · '+esc(a.mime):''}</span>
      </div>
      <div class="af-src" style="margin-bottom:12px">
        Source: ${esc(a.source||'agent')}${a.conversation_id?' · conv '+esc(a.conversation_id):''} · ${when}
      </div>
      <div class="af-modal-body">Loading preview…</div>
      <div class="nova-dialog-actions">
        <button class="ui-btn ui-btn-ghost" id="afDel">Delete</button>
        <span style="flex:1"></span>
        <a class="ui-btn ui-btn-ghost" href="${esc(a.url)}" download="${esc(a.name)}">Download</a>
        <button class="ui-btn ui-btn-primary" id="afClose">Close</button>
      </div>`;
    d.addEventListener('close',()=>d.remove(),{once:true});
    d.addEventListener('click',e=>{if(e.target===d)d.close();});
    document.body.appendChild(d);
    d.querySelector('#afClose').onclick=()=>d.close();
    d.querySelector('#afDel').onclick=async()=>{
      if(!confirm(`Delete ${a.name}?`))return;
      try{await v1Api('/v1/artifacts/'+a.id,{method:'DELETE'});
        d.close();toast('Artifact deleted');load();
      }catch(e){toast(e.message,'err');}
    };
    d.showModal();
    if(window.NovaMotion)NovaMotion.dialog(d);
    d.querySelector('.af-modal-body').innerHTML=await previewBody(a);
  }
})();
