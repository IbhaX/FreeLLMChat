/* Settings page — theme, connection, admin token, provider defaults, system. */
AdminShell.mount('settings');

const STRATS=['round_robin','least_used','weighted','priority_failover'];
let PROVIDERS=[];

// theme segmented control
function syncThemeSeg(){
  document.querySelectorAll('#themeSeg button').forEach(b=>{
    const selected=b.dataset.t===UITheme.get();
    b.classList.toggle('sel',selected);
    b.setAttribute('aria-pressed',String(selected));
  });
}
const themeSeg=document.getElementById('themeSeg');
if(themeSeg){
  themeSeg.replaceChildren(...UITheme.registry.map(theme=>{
    const button=document.createElement('button');
    button.type='button';
    button.dataset.t=theme.id;
    button.textContent=theme.label;
    button.onclick=()=>UITheme.set(theme.id);
    return button;
  }));
}
window.addEventListener('nova:themechange',syncThemeSeg);
syncThemeSeg();

// accent customization
const accentChip=$('#accentChip'),accentDot=$('#accentDot'),accentHex=$('#accentHex');
function syncAccent(){
  const hex=window.UITheme&&UITheme.accent();
  if(accentChip)accentChip.hidden=!hex;
  if(hex){accentDot.style.background=hex;accentHex.textContent=hex;}
}
const accentBtn=$('#accentBtn');
if(accentBtn)accentBtn.onclick=()=>UITheme.pick();
const accentReset=$('#accentReset');
if(accentReset)accentReset.onclick=()=>UITheme.clearAccent();
window.addEventListener('nova:themecustom',syncAccent);
syncAccent();

// connection
$('#gwUrl').value=localStorage.getItem('gateway_url')||'';
$('#admTok').value=sessionStorage.getItem('admin_token')?'••••••••':'';

function saveConn(){
  const u=$('#gwUrl').value.trim();
  if(u)localStorage.setItem('gateway_url',u);else localStorage.removeItem('gateway_url');
  API_BASE=u;
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
  adminApi('/admin/stats').then(()=>{toast('Token verified','ok');})
    .catch(()=>toast('Token rejected','err'));
}

// provider defaults accordion
const _denied='<div class="t-sm tx-3">Requires the admin role — your account sees appearance and account settings only.</div>';
async function loadProviders(){
  try{PROVIDERS=await adminApi('/admin/providers');}
  catch(e){
    if(e.status===403){$('#provAcc').innerHTML=_denied;return;}
    $('#provAcc').innerHTML='<div class="t-sm" style="color:var(--error)">'+esc(e.message)+'</div>';
    return;
  }
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
  let h,st;
  try{
    [h,st]=await Promise.all([
      fetch(API_BASE+'/healthz').then(r=>r.json()).catch(()=>null),
      adminApi('/admin/stats')]);
  }catch(e){
    $('#sysInfo').innerHTML=e.status===403?_denied
      :'<div class="t-sm" style="color:var(--error)">'+esc(e.message)+'</div>';
    return;
  }
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

// custom instructions (server-side system prompt addition)
async function loadCI(){
  try{
    const r=await adminApi('/admin/settings/custom-instructions');
    $('#ciInput').value=r.custom_instructions||'';
  }catch(_){/* admin not ready yet */}
}
async function saveCI(){
  const v=$('#ciInput').value;
  const r=await fetch(API_BASE+'/admin/settings/custom-instructions',{method:'PUT',headers:apiHeaders(true),body:JSON.stringify({custom_instructions:v})});
  if(r.ok){$('#ciStatus').textContent='Saved';$('#ciStatus').style.color='var(--success)';toast('Custom instructions saved','ok');}
  else{$('#ciStatus').textContent='Save failed';$('#ciStatus').style.color='var(--error)';toast('Save failed','err');}
}
async function clearCI(){
  $('#ciInput').value='';await saveCI();
}

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

window.addEventListener('admin-ready',()=>{
  if(window.NovaMotion)NovaMotion.stagger(
    document.querySelectorAll('.set-card'),{y:14,dur:.35,stagger:.05});
  loadProviders();loadSys();loadMem();loadCI();});
if(innerWidth<=820)document.getElementById('menuBtn').style.display='grid';

/* ---- Account (user auth + plan + usage) ---- */
function _auShow(logged){
  document.getElementById('acctLoggedOut').style.display=logged?'none':'';
  document.getElementById('acctLoggedIn').style.display=logged?'':'none';
}
async function loadAccount(){
  try{
    const d=await v1Api('/v1/auth/me');
    if(!d.user){_auShow(false);return;}
    _auShow(true);
    document.getElementById('au_disp').textContent=d.user.display_name||d.user.username;
    document.getElementById('au_avatar').textContent=(d.user.username||'?')[0].toUpperCase();
    document.getElementById('au_plan').textContent=d.plan;
    document.getElementById('au_role').textContent=d.user.role;
    const u=await v1Api('/v1/auth/usage');
    const bk=u.by_kind||{};
    document.getElementById('au_usage').innerHTML=Object.entries(bk).map(([k,v])=>
      `<span><b>${v.events}</b> ${k} · ${v.tokens_in+v.tokens_out} tok</span>`).join('')
      ||'<span class="tx-3">No usage yet</span>';
  }catch(_){_auShow(false);}
}
async function _auth(path){
  const body={username:document.getElementById('au_name').value.trim(),
              password:document.getElementById('au_pass').value};
  try{
    const d=await v1Api(path,{method:'POST',body:JSON.stringify(body)});
    USER_TOKEN=d.token;localStorage.setItem('nova_user_token',d.token);
    document.getElementById('au_err').textContent='';
    toast('Signed in as '+d.user.username,'ok');loadAccount();
  }catch(e){document.getElementById('au_err').textContent=e.message;}
}
document.getElementById('loginBtn').onclick=()=>_auth('/v1/auth/login');
document.getElementById('regBtn').onclick=()=>_auth('/v1/auth/register');
document.getElementById('logoutBtn').onclick=async()=>{
  try{await v1Api('/v1/auth/logout',{method:'POST'});}catch(_){}
  USER_TOKEN='';localStorage.removeItem('nova_user_token');loadAccount();toast('Signed out');
};
loadAccount();
