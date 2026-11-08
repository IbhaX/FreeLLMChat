/* API Keys page — multi-provider, multi-key management. */
AdminShell.mount('keys');

let PROVIDERS=[],KEYS=[],SEL='*',EDIT_ID=null;
const STRATS=['round_robin','least_used','weighted','priority_failover'];
const GRADS={'groq':'#f55036','gemini':'#4e8ef7','openrouter':'#8b5cf6','mistral':'#ff7000','cloudflare':'#f6821f','nvidia':'#76b900','cohere':'#39594d','sambanova':'#e63e62','cerebras':'#5b9bd5'};

function pgrad(n){return GRADS[n]||'linear-gradient(135deg,var(--accent),var(--accent-2))'}

async function refresh(){
  [PROVIDERS,KEYS]=await Promise.all([adminApi('/admin/providers'),adminApi('/admin/keys')]);
  renderStrip();renderTable();renderProvSettings();
}

function renderStrip(){
  const strip=$('#provStrip');
  const all=`<div class="ui-card ui-card-pad prov-card ${SEL==='*'?'sel':''}" onclick="selProv('*')">
    <div class="pn">All providers</div><div class="pk">${KEYS.length} keys</div></div>`;
  strip.innerHTML=all+PROVIDERS.map(p=>`
    <div class="ui-card ui-card-pad prov-card ${SEL===p.name?'sel':''} ${p.enabled?'':'disabled'}" onclick="selProv('${p.name}')">
      <div class="pn">${esc(p.name)}</div>
      <div class="pk"><span class="${p.healthy_key_count?'':'bad'}" style="${p.healthy_key_count?'':'color:var(--error)'}">${p.healthy_key_count}/${p.key_count} healthy</span></div>
    </div>`).join('');
}
function selProv(n){SEL=n;renderStrip();renderTable();renderProvSettings();}

function shown(){return SEL==='*'?KEYS:KEYS.filter(k=>k.provider===SEL)}

function renderTable(){
  const ks=shown(),tb=document.querySelector('#keyTable tbody');
  $('#keyCount').textContent=`· ${ks.length} key${ks.length===1?'':'s'}`;
  $('#keyEmpty').style.display=ks.length?'none':'block';
  document.getElementById('keyTable').style.display=ks.length?'table':'none';
  tb.innerHTML=ks.map(k=>{
    const status=!k.enabled?['bad','disabled']:
      (k.cooldown_until&&new Date(k.cooldown_until)>new Date())?['warn','cooldown']:['ok','healthy'];
    return `<tr>
      <td><span class="ui-chip"><span style="width:8px;height:8px;border-radius:3px;background:${GRADS[k.provider]||'var(--accent)'}"></span>${esc(k.provider)}</span></td>
      <td><input class="inline-edit" value="${esc(k.label)}" onchange="renKey(${k.id},this.value)" title="Click to rename"></td>
      <td class="key-hint">…${esc(k.key_hint)}</td>
      <td>${k.quota_group?`<span class="pill info">${esc(k.quota_group)}</span>`:'—'}</td>
      <td class="tx-2">${k.priority}</td><td class="tx-2">${k.weight}</td>
      <td>${k.daily_request_count}</td>
      <td class="tx-3 t-xs">${k.cooldown_until?new Date(k.cooldown_until).toLocaleTimeString():'—'}</td>
      <td><span class="pill ${status[0]}">${status[1]}</span></td>
      <td><div class="actions">
        <button class="ui-btn ui-btn-ghost ui-btn-sm" onclick="testKey(${k.id})" title="Test key">test</button>
        <button class="ui-btn ui-btn-ghost ui-btn-sm" onclick="resetKey(${k.id})" title="Reset cooldown">reset</button>
        <button class="ui-btn ui-btn-ghost ui-btn-sm" onclick="editKey(${k.id})" title="Edit">edit</button>
        <label class="ui-switch" title="${k.enabled?'Enabled':'Disabled'}"><input type="checkbox" ${k.enabled?'checked':''} onchange="toggleK(${k.id},this.checked)"><span class="knob"></span></label>
        <button class="ui-btn ui-btn-ghost ui-btn-sm ui-btn-danger" onclick="delKey(${k.id})" title="Delete">✕</button>
      </div></td></tr>`;}).join('');
}

function renderProvSettings(){
  const p=SEL==='*'?null:PROVIDERS.find(x=>x.name===SEL);
  const el=$('#provSettings');
  if(!p){el.innerHTML='<div class="tx-3 t-sm">Select a provider above to edit its rotation strategy and settings.</div>';return;}
  el.innerHTML=`
    <div style="display:flex;gap:18px;flex-wrap:wrap;align-items:center">
      <div style="min-width:200px">
        <div style="font-weight:600">${esc(p.display_name)}</div>
        <div class="tx-3 t-xs">${esc(p.base_url)}</div>
      </div>
      <div>
        <div class="label">Rotation strategy</div>
        <div class="ui-seg">${STRATS.map(s=>`<button type="button" class="${p.rotation_strategy===s?'sel':''}" onclick="setStrategy('${p.name}','${s}')">${s.replace('_',' ')}</button>`).join('')}</div>
      </div>
      <div>
        <div class="label">Enabled</div>
        <label class="ui-switch"><input type="checkbox" ${p.enabled?'checked':''} onchange="toggleP('${p.name}',this.checked)"><span class="knob"></span></label>
      </div>
      <div>
        <div class="label">Models</div>
        <button class="ui-btn ui-btn-sm" onclick="refreshModels('${p.name}')">↻ refresh</button>
      </div>
      <div style="margin-left:auto">
        <div class="label">Limits</div>
        <div style="display:flex;gap:5px;flex-wrap:wrap">${Object.entries(p.limits||{}).map(([k,v])=>`<span class="ui-chip">${k}:${v}</span>`).join('')||'<span class="tx-3 t-xs">—</span>'}</div>
      </div>
    </div>`;
}

async function addKeys(){
  const raw=$('#keyArea').value.trim();
  if(!raw){toast('Paste at least one key','err');return;}
  const keys=raw.split(/[\n,]+/).map(s=>s.trim()).filter(Boolean);
  const provider=SEL==='*'?(PROVIDERS[0]&&PROVIDERS[0].name):SEL;
  if(!provider){toast('No provider selected','err');return;}
  const r=await adminApi('/admin/keys',{method:'POST',body:JSON.stringify({
    provider,keys,label_prefix:$('#keyLabel').value,
    quota_group:$('#keyQuota').value||null,
    priority:+$('#keyPriority').value||100,
    weight:+$('#keyWeight').value||1,
    notes:$('#keyNotes').value||null})});
  $('#keyArea').value='';
  toast(`${r.created} key${r.created===1?'':'s'} added${r.skipped?`, ${r.skipped} skipped (dup/empty)`:''}`,'ok');
  refresh();
}

async function importEnv(){
  const mapping={};
  for(const p of PROVIDERS){
    const guess=(p.name.replace(/[^a-z0-9]/gi,'_')+'_API_KEY').toUpperCase();
    const v=prompt(`Env var name for ${p.name} (blank to skip):`,guess);
    if(v)mapping[p.name]=v.trim();
  }
  if(!Object.keys(mapping).length)return;
  const r=await adminApi('/admin/keys/import-env',{method:'POST',body:JSON.stringify(mapping)});
  toast(`${r.created} imported, ${r.skipped} skipped`,r.created?'ok':'err');
  refresh();
}

async function importCsv(input){
  const f=input.files[0];if(!f)return;
  const fd=new FormData();fd.append('file',f);
  const r=await fetch(API_BASE+'/admin/keys/import-csv',{method:'POST',
    headers:{'X-Admin-Token':ADMIN_TOKEN},body:fd});
  const j=await r.json();
  toast(`${j.created} imported, ${j.skipped} skipped`,j.created?'ok':'err');
  input.value='';refresh();
}

async function toggleK(id,en){await adminApi(`/admin/keys/${id}`,{method:'PATCH',body:JSON.stringify({enabled:en})});refresh();}
async function resetKey(id){await adminApi(`/admin/keys/${id}/reset-cooldown`,{method:'POST'});toast('Cooldown reset','ok');refresh();}
async function delKey(id){if(!confirm('Delete this key?'))return;await adminApi(`/admin/keys/${id}`,{method:'DELETE'});refresh();}
async function renKey(id,label){await adminApi(`/admin/keys/${id}`,{method:'PATCH',body:JSON.stringify({label})});toast('Renamed','ok');}
async function testKey(id){const r=await adminApi(`/admin/keys/${id}/test`,{method:'POST'});
  toast(r.ok?`OK (${Math.round(r.latency_ms)}ms)`:`FAIL: ${r.error}`,r.ok?'ok':'err');}
async function toggleP(name,en){await adminApi(`/admin/providers/${name}`,{method:'PATCH',body:JSON.stringify({enabled:en})});refresh();}
async function setStrategy(name,s){await adminApi(`/admin/providers/${name}`,{method:'PATCH',body:JSON.stringify({rotation_strategy:s})});toast('Strategy updated','ok');refresh();}
async function refreshModels(name){const r=await adminApi(`/admin/providers/${name}/refresh-models`,{method:'POST'});toast(`${r.count} models cached`,'ok');}

function editKey(id){
  const k=KEYS.find(x=>x.id===id);if(!k)return;
  EDIT_ID=id;
  $('#edLabel').value=k.label;$('#edQuota').value=k.quota_group||'';
  $('#edPriority').value=k.priority;$('#edWeight').value=k.weight;
  $('#edNotes').value=k.notes||'';
  $('#keyDrawer').style.display='block';
}
function closeDrawer(){$('#keyDrawer').style.display='none';EDIT_ID=null;}
async function saveKey(){
  await adminApi(`/admin/keys/${EDIT_ID}`,{method:'PATCH',body:JSON.stringify({
    label:$('#edLabel').value,quota_group:$('#edQuota').value||null,
    priority:+$('#edPriority').value,weight:+$('#edWeight').value,
    notes:$('#edNotes').value||null})});
  closeDrawer();toast('Key updated','ok');refresh();
}
document.addEventListener('click',e=>{
  if(e.target.id==='keyDrawer')return; // drawer stays until closed
});

window.addEventListener('admin-ready',refresh);
if(innerWidth<=820)document.getElementById('menuBtn').style.display='grid';
