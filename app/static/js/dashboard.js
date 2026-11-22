/* Dashboard page logic — stats, providers, keys, recent requests. */
AdminShell.mount('dashboard');

const fmt={
  time:ts=>ts?new Date(ts).toLocaleTimeString():'—',
  limits:l=>Object.entries(l||{}).map(([k,v])=>k+':'+v).join(' ')||'—',
};
const ICONS={req:'⇄',err:'⚠',tin:'↓',tout:'↑',prov:'⬡',key:'⚿',cd:'⏸'};

/* Last fetched payloads — stat-card detail modals read from here. */
let _data={stats:null,providers:[],keys:[],reqs:[]};

const _fmtN=n=>n==null?'—':Number(n).toLocaleString();
const _fmtTok=n=>n==null?'—':n>=1e6?(n/1e6).toFixed(1)+'M'
  :n>=1e3?(n/1e3).toFixed(1)+'k':String(n);
function _kv(pairs){
  return `<div class="rq-grid">${pairs.map(([k,v])=>
    `<div class="rq-item"><span class="rq-k">${k}</span><span class="rq-v">${esc(String(v??'—'))}</span></div>`
  ).join('')}</div>`;
}
function _tbl(heads,rows){
  if(!rows.length)return '<div class="tx-3 t-sm">No data</div>';
  return `<div class="ui-table-scroll"><table class="ui-table"><thead><tr>${
    heads.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}
function _sec(t){return `<div class="sm-sec">${t}</div>`;}
function _keyStatus(k){
  return !k.enabled?['bad','disabled']
    :k.decryptable===false?['bad','undecryptable']
    :(k.cooldown_until&&new Date(k.cooldown_until)>new Date())?['warn','cooldown']
    :['ok','healthy'];
}
function _keyRow(k){
  const s=_keyStatus(k);
  return `<tr><td>${esc(k.provider)}</td><td>${esc(k.label)}</td>
    <td class="mono">…${esc(k.key_hint)}</td>
    <td>${k.quota_group?`<span class="pill info">${esc(k.quota_group)}</span>`:'—'}</td>
    <td>${_fmtN(k.daily_request_count)}</td><td>${_fmtTok(k.daily_token_count)}</td>
    <td class="tx-3">${k.cooldown_until?new Date(k.cooldown_until).toLocaleString():'—'}</td>
    <td><span class="pill ${s[0]}">${s[1]}</span></td></tr>`;
}
const _KEY_HEADS=['Provider','Label','Hint','Quota grp','Req/day','Tok/day','Cooldown until','Status'];
const _PROV_ROW=p=>`<tr><td><b>${esc(p.name)}</b><br><span class="tx-3 t-xs">${esc(p.display_name)}</span></td>
  <td><span class="pill ${p.enabled?'ok':'bad'}">${p.enabled?'enabled':'disabled'}</span></td>
  <td class="tx-2">${esc(p.rotation_strategy)}</td><td>${p.key_count}</td><td>${p.healthy_key_count}</td>
  <td class="tx-3 t-xs">${fmt.limits(p.limits)}</td></tr>`;
const _PROV_HEADS=['Provider','Status','Strategy','Keys','Healthy','Limits'];

function statDetail(id){
  const {stats:S,providers,keys,reqs}=_data;
  if(!S)return;
  let title='',body='';
  const pp=S.per_provider||[];
  const errCount=Math.round((S.error_rate_24h||0)*(S.requests_24h||0));
  const provReqTbl=_tbl(['Provider','Requests 24h','Tokens 24h'],
    pp.map(r=>`<tr><td><b>${esc(r.provider)}</b></td><td>${_fmtN(r.requests)}</td><td>${_fmtTok(r.tokens)}</td></tr>`));
  const reqRows=rs=>rs.map(r=>`<tr>
    <td class="tx-3">${r.ts?new Date(r.ts).toLocaleString():'—'}</td>
    <td class="mono t-xs">${esc(r.model_requested)}</td>
    <td>${esc(r.model_served||'—')}</td><td>${esc(r.provider||'—')}</td>
    <td><span class="pill ${r.status==='ok'?'ok':'bad'}">${r.status}</span></td>
    <td class="tx-2">${r.latency_ms?Math.round(r.latency_ms):'—'}</td>
    <td class="tx-3 t-xs">${esc((r.error||'').slice(0,120))}</td></tr>`);
  const REQ_HEADS=['Time','Model','Served','Provider','Status','ms','Error'];

  if(id==='requests'){
    title='Requests — last 24h';
    const lats=reqs.filter(r=>r.latency_ms).map(r=>r.latency_ms);
    body=_sec('Summary')+_kv([
      ['Requests · 24h',_fmtN(S.requests_24h)],
      ['Requests · all time',_fmtN(S.total_requests)],
      ['Errors · 24h',_fmtN(errCount)],
      ['Success rate',S.requests_24h?((1-S.error_rate_24h)*100).toFixed(1)+'%':'—'],
      ['Avg latency (recent)',lats.length?Math.round(lats.reduce((a,b)=>a+b,0)/lats.length)+' ms':'—'],
      ['Max latency (recent)',lats.length?Math.round(Math.max(...lats))+' ms':'—'],
    ])+_sec('Per provider')+provReqTbl
     +_sec(`Recent requests (${reqs.length} shown)`)
     +_tbl(REQ_HEADS,reqRows(reqs));
  }else if(id==='errors'){
    title='Error rate — last 24h';
    const errs=reqs.filter(r=>r.status!=='ok');
    body=_sec('Summary')+_kv([
      ['Error rate',(S.error_rate_24h*100).toFixed(1)+'%'],
      ['Errors · 24h',_fmtN(errCount)],
      ['Requests · 24h',_fmtN(S.requests_24h)],
      ['Successful · 24h',_fmtN((S.requests_24h||0)-errCount)],
      ['Errors in recent sample',`${errs.length} of ${reqs.length}`],
    ])+_sec(`Recent errors (${errs.length} shown)`)
     +_tbl(REQ_HEADS,reqRows(errs));
  }else if(id==='tin'||id==='tout'){
    const tin=S.tokens_in_24h||0,tout=S.tokens_out_24h||0;
    title=id==='tin'?'Tokens in — last 24h':'Tokens out — last 24h';
    body=_sec('Summary')+_kv([
      ['Tokens in · 24h',_fmtN(tin)],
      ['Tokens out · 24h',_fmtN(tout)],
      ['Total tokens · 24h',_fmtN(tin+tout)],
      ['Credits consumed',_fmtN(Math.ceil((tin+tout)/100))+' (100 tok = 1 cr)'],
      ['Est. daily budget',S.est_tokens_total?'~'+_fmtN(S.est_tokens_total):'—'],
    ])+_sec('Per provider')+provReqTbl
     +_sec('Token budget')+_tbl(['Provider','Keys','Used 24h','Est. daily'],
      (S.token_budget||[]).map(b=>`<tr><td><b>${esc(b.provider)}</b></td><td>${b.keys}</td>
        <td>${_fmtTok(b.used_24h)}</td><td>${b.est_daily?'~'+_fmtTok(b.est_daily):'—'}</td></tr>`));
  }else if(id==='providers'){
    title=`Providers (${providers.length})`;
    const en=providers.filter(p=>p.enabled),keyed=providers.filter(p=>p.key_count>0);
    body=_sec('Summary')+_kv([
      ['Providers',providers.length],
      ['Enabled',en.length],['Disabled',providers.length-en.length],
      ['With keys',keyed.length],['Without keys',providers.length-keyed.length],
      ['Requests · 24h',_fmtN(S.requests_24h)],
    ])+_sec('All providers')+_tbl(_PROV_HEADS,providers.map(_PROV_ROW))
     +_sec('Per provider · 24h')+provReqTbl;
  }else if(id==='keys'){
    title=`API keys — ${S.keys_healthy}/${S.keys_total} healthy`;
    const undec=keys.filter(k=>k.decryptable===false).length;
    body=_sec('Summary')+_kv([
      ['Total keys',S.keys_total],['Healthy',S.keys_healthy],
      ['In cooldown',S.keys_in_cooldown],
      ['Disabled',keys.filter(k=>!k.enabled).length],
      ['Undecryptable',undec||'—'],
      ['Quota groups',new Set(keys.map(k=>k.quota_group).filter(Boolean)).size],
    ])+_sec(`All keys (${keys.length})`)
     +_tbl(_KEY_HEADS,keys.map(_keyRow));
  }else if(id==='cooldown'){
    title=`Keys in cooldown (${S.keys_in_cooldown})`;
    const now=new Date();
    const cool=keys.filter(k=>k.cooldown_until&&new Date(k.cooldown_until)>now);
    body=_sec('Summary')+_kv([
      ['In cooldown',cool.length],
      ['Total keys',S.keys_total],
      ['Healthy now',S.keys_healthy],
      ['Earliest recovery',cool.length
        ?new Date(Math.min(...cool.map(k=>+new Date(k.cooldown_until)))).toLocaleString():'—'],
    ])+_sec(`Cooling keys (${cool.length})`)
     +_tbl(_KEY_HEADS,cool.map(_keyRow));
  }else return;

  const old=document.getElementById('statModal');if(old)old.remove();
  const d=document.createElement('dialog');
  d.id='statModal';d.className='nova-dialog stat-modal';
  d.innerHTML=`<h2>${esc(title)}</h2><div class="stat-modal-body">${body}</div>
    <div class="nova-dialog-actions"><button class="ui-btn ui-btn-primary" id="statModalClose">Close</button></div>`;
  d.addEventListener('close',()=>d.remove(),{once:true});
  d.addEventListener('click',e=>{if(e.target===d)d.close();});
  document.body.appendChild(d);
  d.querySelector('#statModalClose').onclick=()=>d.close();
  d.showModal();
  if(window.NovaMotion)NovaMotion.dialog(d);
}

let _plans=null;
async function loadUsers(){
  if(!_plans){
    const pj=await adminApi('/api/plans');
    _plans=(pj.plans||[]).map(p=>p.id);
  }
  const users=await adminApi('/admin/users');
  const q=($('#uq')?.value||'').toLowerCase();
  const rows=users.filter(u=>!q||(u.username+' '+u.email+' '+u.display_name)
    .toLowerCase().includes(q));
  document.querySelector('#users tbody').innerHTML=rows.map(u=>`<tr>
    <td><b>${esc(u.display_name||u.username)}</b><br>
      <span class="tx-3 t-xs">@${esc(u.username)} · #${u.id}</span></td>
    <td class="tx-2">${esc(u.email||'—')}</td>
    <td><span class="pill ${u.role==='admin'?'ok':'mut'}">${esc(u.role)}</span></td>
    <td><select class="ui-select" style="width:110px"
        onchange="setPlan(${u.id},this.value)">
      ${_plans.map(p=>`<option value="${p}"${p===u.plan?' selected':''}>${p}</option>`).join('')}
    </select></td>
    <td>${u.deleted_at
        ?`<span class="pill bad">deleted</span>
          <button class="ui-btn ui-btn-ghost ui-btn-sm" onclick="toggleUser(${u.id},0)">restore</button>`
        :`<button class="ui-btn ui-btn-ghost ui-btn-sm ${u.disabled?'ui-btn-danger':''}"
          onclick="toggleUser(${u.id},${u.disabled?0:1})">${u.disabled?'disabled':'enabled'}</button>
          <button class="ui-btn ui-btn-ghost ui-btn-sm ui-btn-danger"
          onclick="deleteUser(${u.id},'${esc(u.username)}')">delete</button>`}</td>
    <td class="tx-3 t-xs">${u.created_at?new Date(u.created_at).toLocaleDateString():'—'}</td>
  </tr>`).join('')||'<tr><td colspan="6" class="tx-3">No users</td></tr>';
}
async function setPlan(id,plan){
  try{await adminApi(`/admin/users/${id}`,{method:'PATCH',
      body:JSON.stringify({plan})});
    toast(`Plan → ${plan}`,'ok');}
  catch(e){toast(e.message||'Plan change failed','err');}
  loadUsers().catch(()=>{});
}
async function deleteUser(id,username){
  const v=await NovaUI.Dialog.form({
    title:`Delete @${username}`,
    message:'Soft delete disables the account and revokes sessions '
      +'(recoverable). Hard delete removes the user permanently.',
    fields:[
      {name:'mode',type:'select',label:'Delete mode',options:[
        {value:'soft',label:'Soft delete — recoverable'},
        {value:'hard',label:'Hard delete — permanent'}]},
      {name:'confirm',type:'text',required:true,
       label:`Type "${username}" to confirm`},
    ],
    confirmLabel:'Delete',danger:true,
  });
  if(!v)return;
  if((v.confirm||'').trim()!==username){
    toast('Confirmation text did not match — user not deleted','err');return;
  }
  try{
    const r=await adminApi(`/admin/users/${id}?mode=${v.mode}`,{method:'DELETE'});
    toast(v.mode==='hard'?`@${username} permanently deleted`
        :`@${username} soft-deleted`,'ok');
  }catch(e){toast(e.message||'Delete failed','err');}
  loadUsers().catch(()=>{});
}
async function toggleUser(id,dis){
  try{await adminApi(`/admin/users/${id}`,{method:'PATCH',
      body:JSON.stringify({disabled:!!dis})});
    toast(dis?'Account disabled':'Account enabled','ok');}
  catch(e){toast(e.message||'Access change failed','err');}
  loadUsers().catch(()=>{});
}

async function refresh(){
  const [stats,providers,keys,reqs]=await Promise.all([
    adminApi('/admin/stats'),adminApi('/admin/providers'),
    adminApi('/admin/keys'),adminApi('/admin/requests?limit=30')]);
  _data={stats,providers,keys,reqs};
  loadUsers().catch(()=>{});

  $('#health').textContent='● online';$('#health').className='pill ok';

  $('#stats').innerHTML=[
    ['Requests 24h',stats.requests_24h,ICONS.req,'requests'],
    ['Error rate',(stats.error_rate_24h*100).toFixed(1)+'%',ICONS.err,'errors'],
    ['Tokens in',stats.tokens_in_24h,ICONS.tin,'tin'],
    ['Tokens out',stats.tokens_out_24h,ICONS.tout,'tout'],
    ['Providers',stats.providers,ICONS.prov,'providers'],
    ['Keys healthy',stats.keys_healthy+'/'+stats.keys_total,ICONS.key,'keys'],
    ['In cooldown',stats.keys_in_cooldown,ICONS.cd,'cooldown'],
  ].map(([l,v,i,id])=>`<button type="button" class="ui-card stat-card stat-card-btn"
    onclick="statDetail('${id}')" title="Click for details"><div class="ico">${i}</div>
    <div class="v">${v}</div><div class="l">${l}</div></button>`).join('');
  if(window.NovaMotion&&!refresh._statsPainted){refresh._statsPainted=1;
    NovaMotion.stagger(document.querySelectorAll('#stats .stat-card'),{y:14,dur:.35,stagger:.05});}
  if(window.NovaMotion)NovaMotion.liftAll('#stats .stat-card',null,{y:-3});

  const fmtTok=n=>n==null?'—':n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'k':n;
  document.querySelector('#budget tbody').innerHTML=(stats.token_budget||[]).map(b=>{
    const pct=b.est_daily?Math.min(100,(b.used_24h/b.est_daily)*100):null;
    const bar=pct==null?'<span class="tx-3 t-xs">no estimate</span>':
      `<div style="background:var(--bg-3,#222);border-radius:6px;height:8px;overflow:hidden">
        <div class="budget-fill" style="width:${pct.toFixed(1)}%;height:100%;background:${pct>85?'var(--danger,#e5484d)':pct>60?'var(--warn,#f5a524)':'var(--accent,#7c5cff)'}"></div>
      </div><div class="tx-3 t-xs" style="margin-top:3px">${pct.toFixed(1)}% used</div>`;
    return `<tr><td><b>${esc(b.provider)}</b></td><td>${b.keys}</td>
      <td>${fmtTok(b.used_24h)}</td><td>${b.est_daily?'~'+fmtTok(b.est_daily):'—'}</td><td>${bar}</td></tr>`;
  }).join('')||'<tr><td colspan="5" class="tx-3">No providers</td></tr>';
  if(stats.est_tokens_total)
    document.querySelector('#budget tbody').insertAdjacentHTML('beforeend',
      `<tr id="budgetTotal"><td><b>total est.</b></td><td></td>
       <td><b>${fmtTok(stats.tokens_in_24h+stats.tokens_out_24h)}</b></td>
       <td><b>~${fmtTok(stats.est_tokens_total)}</b></td><td></td></tr>`);

  const keyed=providers.filter(p=>p.key_count>0);
  const unkeyed=providers.filter(p=>!p.key_count);
  const provRow=p=>`<tr>
    <td><b>${esc(p.name)}</b><br><span class="tx-3 t-xs">${esc(p.display_name)}</span></td>
    <td class="tx-2">${p.rotation_strategy}</td><td>${p.key_count}</td><td>${p.healthy_key_count}</td>
    <td class="tx-3 t-xs">${fmt.limits(p.limits)}</td>
    <td><div class="actions" data-admin="1">
      <button class="ui-btn ui-btn-ghost ui-btn-sm" onclick="toggleP('${p.name}',${!p.enabled})">${p.enabled?'disable':'enable'}</button>
      <button class="ui-btn ui-btn-ghost ui-btn-sm" onclick="refreshModels('${p.name}')">refresh models</button>
    </div></td>
  </tr>`;
  document.querySelector('#providers tbody').innerHTML=
    keyed.map(provRow).join('')+
    (unkeyed.length?`<tr><td colspan="6" style="padding:0;border:none">
      <button class="ui-btn ui-btn-ghost ui-btn-sm" style="width:100%;opacity:.6"
        onclick="this.closest('tr').nextElementSibling.style.display='';this.closest('tr').remove()">
        show ${unkeyed.length} provider(s) with no keys…</button></td></tr>
      <tr style="display:none"><td colspan="6" style="padding:0"><table style="width:100%">${unkeyed.map(provRow).join('')}</table></td></tr>`:'');

  $('#kp').innerHTML=providers.map(p=>`<option>${esc(p.name)}</option>`).join('');

  const badDecrypt=keys.filter(k=>k.decryptable===false);
  const warn=$('#keyDecryptWarn');
  warn.style.display=badDecrypt.length?'block':'none';
  warn.textContent=badDecrypt.length
    ?`⚠️ ${badDecrypt.length} stored API key(s) can't be decrypted — the ENCRYPTION_KEY changed. `
      +'Delete and re-enter them below, or restore the previous ENCRYPTION_KEY.'
    :'';
  document.querySelector('#keys tbody').innerHTML=keys.map(k=>{
    const undecryptable=k.decryptable===false;
    const status=!k.enabled?['bad','disabled']:
      undecryptable?['bad','undecryptable']:
      (k.cooldown_until&&new Date(k.cooldown_until)>new Date())?['warn','cooldown']:['ok','healthy'];
    return `<tr>
      <td>${esc(k.provider)}</td><td>${esc(k.label)}</td><td class="mono">…${esc(k.key_hint)}</td>
      <td>${k.quota_group?`<span class="pill info">${esc(k.quota_group)}</span>`:'—'}</td>
      <td>${k.daily_request_count}</td><td>${k.daily_token_count}</td>
      <td class="tx-3">${k.cooldown_until?fmt.time(k.cooldown_until):'—'}</td>
      <td><span class="pill ${status[0]}"${undecryptable?' title="ENCRYPTION_KEY changed — delete and re-enter this key"':''}>${status[1]}</span></td>
      <td><div class="actions" data-admin="1">
        <button class="ui-btn ui-btn-ghost ui-btn-sm" onclick="testKey(${k.id})">test</button>
        <button class="ui-btn ui-btn-ghost ui-btn-sm" onclick="resetKey(${k.id})">reset</button>
        <button class="ui-btn ui-btn-ghost ui-btn-sm" onclick="toggleK(${k.id},${!k.enabled})">${k.enabled?'disable':'enable'}</button>
        <button class="ui-btn ui-btn-ghost ui-btn-sm ui-btn-danger" onclick="delKey(${k.id})">del</button>
      </div></td></tr>`;}).join('');

  const reqMeta=r=>[
    ['Request ID',r.request_id],['Time',r.ts?new Date(r.ts).toLocaleString():'—'],
    ['Model requested',r.model_requested],['Model served',r.model_served||'—'],
    ['Provider',r.provider||'—'],['Key',r.key_label||'—'],
    ['Status',`${r.status}${r.http_status?' ('+r.http_status+')':''}`],
    ['Latency',r.latency_ms?Math.round(r.latency_ms)+' ms':'—'],
    ['Tokens in',r.tokens_in??'—'],['Tokens out',r.tokens_out??'—'],
    ['Total tokens',(r.tokens_in||0)+(r.tokens_out||0)||'—'],
    ['Error',r.error||'—'],
  ].map(([k,v])=>`<div class="rq-item"><span class="rq-k">${k}</span><span class="rq-v">${esc(String(v??'—'))}</span></div>`).join('')
    + (r.request_input?`<div class="rq-item"><span class="rq-k">Input</span><span class="rq-v"><pre style="white-space:pre-wrap;margin:0;font-size:12px;max-height:200px;overflow:auto">${esc(r.request_input)}</pre></span></div>`:'')
    + (r.response_text?`<div class="rq-item"><span class="rq-k">Response</span><span class="rq-v"><pre style="white-space:pre-wrap;margin:0;font-size:12px;max-height:300px;overflow:auto">${esc(r.response_text)}</pre></span></div>`:'');
  document.querySelector('#reqs tbody').innerHTML=reqs.map((r,i)=>`<tr class="rq-row" data-i="${i}">
    <td class="tx-3">${fmt.time(r.ts)}</td><td class="mono t-xs">${esc(r.model_requested)}</td>
    <td>${esc(r.model_served||'—')}</td><td>${esc(r.provider||'—')}</td>
    <td>${esc(r.key_label||'—')}</td>
    <td><span class="pill ${r.status==='ok'?'ok':'bad'}">${r.status}</span></td>
    <td class="tx-2">${r.latency_ms?Math.round(r.latency_ms):'—'}</td></tr>
    <tr class="rq-detail" style="display:none"><td colspan="7"><div class="rq-grid">${reqMeta(r)}</div></td></tr>`).join('');
  window.NovaAuth&&NovaAuth.applyRoleNav();
  if(window.NovaMotion&&window.gsap){
    /* Budget bars sweep in on each refresh — cheap transform-only anim. */
    document.querySelectorAll('#budget .budget-fill').forEach((b,i)=>{
      if(b._mo)return;b._mo=1;
      gsap.fromTo(b,{scaleX:0},{scaleX:1,transformOrigin:'left center',
        duration:.55,ease:'power3.out',delay:i*.04,clearProps:'transform'});
    });
    if(!refresh._rowsPainted){refresh._rowsPainted=1;
      NovaMotion.stagger(document.querySelectorAll('#reqs .rq-row'),{y:8,dur:.25,stagger:.02});}
  }
  document.querySelectorAll('#reqs .rq-row').forEach(row=>row.onclick=()=>{
    const d=row.nextElementSibling,open=d.style.display!=='none';
    d.style.display=open?'none':'table-row';row.classList.toggle('open',!open);
  });
}

async function addKeys(){
  const keys=$('#kv').value.split(',').map(s=>s.trim()).filter(Boolean);
  await adminApi('/admin/keys',{method:'POST',body:JSON.stringify({
    provider:$('#kp').value,keys,
    label_prefix:$('#kl').value,
    quota_group:$('#kg').value||null})});
  $('#kv').value='';toast('Keys added','ok');refresh();
}
async function toggleK(id,en){await adminApi(`/admin/keys/${id}`,{method:'PATCH',body:JSON.stringify({enabled:en})});refresh();}
async function toggleP(name,en){await adminApi(`/admin/providers/${name}`,{method:'PATCH',body:JSON.stringify({enabled:en})});refresh();}
async function resetKey(id){await adminApi(`/admin/keys/${id}/reset-cooldown`,{method:'POST'});refresh();}
async function delKey(id){if(!confirm('Delete key?'))return;await adminApi(`/admin/keys/${id}`,{method:'DELETE'});refresh();}
async function testKey(id){const r=await adminApi(`/admin/keys/${id}/test`,{method:'POST'});
  toast(r.ok?`Key OK (${Math.round(r.latency_ms)}ms)`:`Key failed: ${r.error}`,r.ok?'ok':'err');}
async function refreshModels(name){const r=await adminApi(`/admin/providers/${name}/refresh-models`,{method:'POST'});
  toast(`${r.count} models cached`,'ok');}

window.addEventListener('admin-ready',()=>refresh().catch(e=>{
  if(e.status===403)AdminShell.restricted();
  else toast(e.message||'Failed to load','err');}));
setInterval(()=>{const d=document.getElementById('adminDot');if(d&&d.classList.contains('ok'))refresh();},15000);
document.getElementById('uq').oninput=()=>loadUsers().catch(()=>{});
if(innerWidth<=820)document.getElementById('menuBtn').style.display='grid';
