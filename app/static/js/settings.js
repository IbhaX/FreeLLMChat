/* Settings page — theme, connection, admin token, provider defaults, system. */
AdminShell.mount('settings');

const STRATS=['round_robin','least_used','weighted','priority_failover'];
let PROVIDERS=[];

// theme segmented control
function syncThemeSeg(){
  document.querySelectorAll('#themeSeg button').forEach(b=>
    b.classList.toggle('sel',b.dataset.t===UITheme.get()));
}
document.querySelectorAll('#themeSeg button').forEach(b=>
  b.onclick=()=>{UITheme.set(b.dataset.t);syncThemeSeg();});
syncThemeSeg();

// connection
$('#gwUrl').value=localStorage.getItem('gateway_url')||'';
$('#gwKey').value=localStorage.getItem('gateway_key')||'';
$('#admTok').value=sessionStorage.getItem('admin_token')?'••••••••':'';

function saveConn(){
  const u=$('#gwUrl').value.trim(),k=$('#gwKey').value.trim();
  if(u)localStorage.setItem('gateway_url',u);else localStorage.removeItem('gateway_url');
  if(k)localStorage.setItem('gateway_key',k);else localStorage.removeItem('gateway_key');
  API_BASE=u;API_KEY=k;
  toast('Connection saved','ok');
}
async function testConn(){
  const st=$('#connStatus');st.textContent='testing…';st.style.color='var(--text-3)';
  try{
    const h=await fetch((API_BASE||'')+'/healthz');
    if(!h.ok)throw new Error('health '+h.status);
    const m=await fetch((API_BASE||'')+'/v1/models',{headers:apiHeaders()});
    st.textContent=m.ok?'✓ reachable, models OK':`✓ up, /v1/models → ${m.status}`;
    st.style.color='var(--success)';
  }catch(e){st.textContent='✗ unreachable: '+e.message;st.style.color='var(--error)';}
}
function saveTok(){
  const v=$('#admTok').value.trim();
  if(!v||v==='••••••••')return;
  ADMIN_TOKEN=v;sessionStorage.setItem('admin_token',v);
  toast('Admin token updated — verifying…');
  adminApi('/admin/stats').then(()=>{AdminShell.unlock();toast('Token verified','ok');})
    .catch(()=>toast('Token rejected','err'));
}

// provider defaults accordion
async function loadProviders(){
  PROVIDERS=await adminApi('/admin/providers');
  $('#provAcc').innerHTML=PROVIDERS.map(p=>`
    <details class="ui-acc" style="margin-bottom:8px">
      <summary>
        <span class="pill ${p.enabled?'ok':'bad'}">${p.enabled?'on':'off'}</span>
        <b>${esc(p.display_name)}</b>
        <span class="tx-3 t-xs">${esc(p.name)} · ${p.key_count} keys · ${p.rotation_strategy.replace('_',' ')}</span>
        ${p.docs_url?`<a href="${p.docs_url}" target="_blank" class="t-xs" style="margin-left:auto" onclick="event.stopPropagation()">docs ↗</a>`:''}
      </summary>
      <div class="acc-body">
        <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end;padding-top:10px">
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
            <button class="ui-btn ui-btn-sm" onclick="refreshModels('${p.name}')">↻ refresh list</button>
          </div>
        </div>
        <div style="display:flex;gap:5px;flex-wrap:wrap;margin-top:12px">
          ${Object.entries(p.limits||{}).map(([k,v])=>`<span class="ui-chip limit-chip">${k}: ${v}</span>`).join('')||'<span class="tx-3 t-xs">no limits configured</span>'}
          ${p.default_models.slice(0,6).map(m=>`<span class="ui-chip limit-chip mono">${esc(m)}</span>`).join('')}
          ${p.default_models.length>6?`<span class="ui-chip limit-chip">+${p.default_models.length-6} more</span>`:''}
        </div>
        ${p.notes?`<div class="tx-3 t-xs" style="margin-top:8px">${esc(p.notes)}</div>`:''}
      </div>
    </details>`).join('');
}
async function setStrategy(n,s){await adminApi(`/admin/providers/${n}`,{method:'PATCH',body:JSON.stringify({rotation_strategy:s})});toast('Strategy updated','ok');loadProviders();}
async function toggleP(n,en){await adminApi(`/admin/providers/${n}`,{method:'PATCH',body:JSON.stringify({enabled:en})});loadProviders();}
async function refreshModels(n){const r=await adminApi(`/admin/providers/${n}/refresh-models`,{method:'POST'});toast(`${r.count} models`,'ok');}

// custom provider
async function addProvider(){
  const name=$('#npName').value.trim();
  if(!name||!$('#npUrl').value.trim()){toast('Name and base URL required','err');return;}
  const models=$('#npModels').value.split(',').map(s=>s.trim()).filter(Boolean);
  const r=await fetch(API_BASE+'/admin/providers',{method:'POST',headers:apiHeaders(true),body:JSON.stringify({
    name,display_name:$('#npDisp').value||name,
    adapter:'openai_compat',base_url:$('#npUrl').value.trim(),
    auth_style:'bearer',auth_header:'Authorization',
    default_env_key:$('#npEnv').value.trim()||null,
    rotation_strategy:$('#npStrat').value,
    default_models:models})});
  if(r.ok){toast(`Provider "${name}" created`,'ok');
    ['npName','npDisp','npUrl','npEnv','npModels'].forEach(id=>$('#'+id).value='');
    loadProviders();
  }else{const e=await r.json().catch(()=>({}));toast('Error: '+(e.detail||r.status),'err');}
}

// system info
async function loadSys(){
  const [h,st]=await Promise.all([
    fetch(API_BASE+'/healthz').then(r=>r.json()).catch(()=>null),
    adminApi('/admin/stats')]);
  $('#sysInfo').innerHTML=[
    ['Status',h?'● online':'● down',h?'ok':'bad'],
    ['Total requests',st.total_requests],
    ['Providers',st.providers],
    ['Keys',`${st.keys_healthy}/${st.keys_total} healthy`],
    ['Cooldown',st.keys_in_cooldown],
    ['24h errors',(st.error_rate_24h*100).toFixed(1)+'%'],
  ].map(([l,v,c])=>`<div class="ui-panel" style="padding:10px 12px">
    <div class="t-xs tx-3">${l}</div>
    <div class="fw-6" ${c?`style="color:var(--${c==='ok'?'success':'error'})"`:''}>${v}</div></div>`).join('');
}
async function reseed(){const r=await adminApi('/admin/providers/reseed',{method:'POST'});toast(`Reseeded: ${r.upserted} providers`,'ok');loadProviders();}

// long-term memory
async function loadMem(){
  try{
    const ms=await adminApi('/admin/memories');
    $('#memList').innerHTML=ms.length?ms.map(m=>`
      <div style="display:flex;gap:8px;align-items:center;padding:7px 0;border-bottom:1px solid var(--border-1)">
        <span class="t-xs tx-3 mono" style="flex:none">#${m.id}</span>
        <span class="t-sm" style="flex:1">${esc(m.content)}</span>
        <button class="ui-btn ui-btn-ghost ui-btn-sm" onclick="delMem(${m.id})" style="color:var(--error)">✕</button>
      </div>`).join('')
      :'<div class="t-sm tx-3">No memories yet.</div>';
  }catch(_){$('#memList').innerHTML='<div class="t-sm tx-3">Could not load memories.</div>';}
}
async function addMem(){
  const v=$('#memInput').value.trim();if(!v)return;
  const r=await fetch(API_BASE+'/admin/memories',{method:'POST',headers:apiHeaders(true),body:JSON.stringify({content:v})});
  if(r.ok){$('#memInput').value='';loadMem();toast('Memory saved','ok');}
  else toast('Save failed','err');
}
async function delMem(id){
  const r=await fetch(API_BASE+'/admin/memories/'+id,{method:'DELETE',headers:apiHeaders(true)});
  if(r.ok||r.status===204){loadMem();toast('Memory deleted');}else toast('Delete failed','err');
}

window.addEventListener('admin-ready',()=>{loadProviders();loadSys();loadMem();});
if(innerWidth<=820)document.getElementById('menuBtn').style.display='grid';
