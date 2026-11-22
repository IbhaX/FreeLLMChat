/* Connectors — per-user external-service credentials. */
(function(){
'use strict';
const $=s=>document.querySelector(s);
let _specs=[],_conf={};

async function load(){
  try{
    const d=await v1Api('/v1/connectors');
    _specs=d.connectors||[];_conf=d.configured||{};
    render();
  }catch(e){
    const el=$('#err');el.hidden=false;
    el.textContent=e&&e.status===401
      ?'Sign in to manage connectors — credentials are private to your account.'
      :('Could not load connectors: '+(e.message||e));
  }
}

function render(){
  $('#grid').innerHTML=_specs.map(s=>{
    const c=_conf[s.name];
    return `<div class="ui-card ui-card-pad cx-card">
      <div class="cx-head">
        <div class="cx-ico">${s.icon}</div>
        <div>
          <div class="cx-name">${esc(s.label)}</div>
          <div class="cx-status ${c&&c.connected?'on':''}">
            <span class="dot"></span>${c&&c.connected?'Connected':'Not connected'}
          </div>
        </div>
      </div>
      <div class="cx-desc">${esc(s.description)}</div>
      <div class="cx-foot">
        <button class="ui-btn ui-btn-sm" data-cx="${s.name}">
          ${c&&c.connected?'Manage':'Connect'}</button>
      </div>
    </div>`;
  }).join('');
  document.querySelectorAll('[data-cx]').forEach(b=>
    b.onclick=()=>openModal(b.dataset.cx));
}

function openModal(name){
  const s=_specs.find(x=>x.name===name);if(!s)return;
  const c=_conf[name];
  const dlg=document.createElement('dialog');
  dlg.className='nova-dialog stat-modal';
  const saved=c&&c.fields?Object.entries(c.fields)
    .map(([k,v])=>`${esc(k)}: ${esc(v)}`).join(' · '):'';
  dlg.innerHTML=`<div class="ui-card ui-card-pad">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
      <div class="cx-ico">${s.icon}</div>
      <div><h3 style="margin:0">${esc(s.label)}</h3>
      <div class="cx-hint" style="background:none;padding:0">${esc(s.docs)}</div></div>
    </div>
    ${saved?`<div class="cx-hint">Saved: <span class="cx-saved">${saved}</span>
      — re-enter fields to replace</div>`:''}
    <div class="cx-fields">
      ${s.fields.map(f=>`<label>${esc(f.label)}${f.required?' *':''}
        <input class="ui-input" data-f="${f.key}"
          type="${f.secret?'password':'text'}"
          placeholder="${esc(f.placeholder||f.default||'')}"
          autocomplete="off"></label>`).join('')}
    </div>
    <div style="display:flex;gap:8px;margin-top:16px">
      <button class="ui-btn ui-btn-primary" id="cxSave">Save</button>
      <button class="ui-btn" id="cxTest" ${c&&c.connected?'':'disabled'}>Test</button>
      <button class="ui-btn ui-btn-danger" id="cxDel"
        ${c&&c.connected?'':'disabled'}>Remove</button>
      <button class="ui-btn ui-btn-ghost" id="cxClose" style="margin-left:auto">Close</button>
    </div>
  </div>`;
  document.body.appendChild(dlg);
  dlg.addEventListener('click',e=>{if(e.target===dlg)dlg.close();});
  dlg.addEventListener('close',()=>{dlg.remove();load();});
  dlg.querySelector('#cxClose').onclick=()=>dlg.close();
  const vals=()=>Object.fromEntries(
    [...dlg.querySelectorAll('[data-f]')]
      .map(i=>[i.dataset.f,i.value]).filter(([,v])=>v!==''));
  dlg.querySelector('#cxSave').onclick=async()=>{
    try{
      await v1Api(`/v1/connectors/${name}`,
        {method:'PUT',body:JSON.stringify(vals())});
      toast(`${s.label} connected`);
      dlg.close();
    }catch(e){toast(e.message||'Save failed','err');}
  };
  dlg.querySelector('#cxTest').onclick=async()=>{
    toast('Testing connection…');
    try{
      const r=await v1Api(`/v1/connectors/${name}/test`,{method:'POST'});
      toast(r.message||'Connection OK');
    }catch(e){toast(e.message||'Test failed','err');}
  };
  dlg.querySelector('#cxDel').onclick=async()=>{
    try{
      await v1Api(`/v1/connectors/${name}`,{method:'DELETE'});
      toast(`${s.label} disconnected`);dlg.close();
    }catch(e){toast(e.message||'Remove failed','err');}
  };
  dlg.showModal();
  if(window.NovaMotion&&NovaMotion.dialog)NovaMotion.dialog(dlg);
}
load();
})();
