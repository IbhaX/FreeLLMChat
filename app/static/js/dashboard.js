/* Dashboard page logic — stats, providers, keys, recent requests. */
AdminShell.mount('dashboard');

const fmt={
  time:ts=>ts?new Date(ts).toLocaleTimeString():'—',
  limits:l=>Object.entries(l||{}).map(([k,v])=>k+':'+v).join(' ')||'—',
};
const ICONS={req:'⇄',err:'⚠',tin:'↓',tout:'↑',prov:'⬡',key:'⚿',cd:'⏸'};

async function refresh(){
  const [stats,providers,keys,reqs]=await Promise.all([
    adminApi('/admin/stats'),adminApi('/admin/providers'),
    adminApi('/admin/keys'),adminApi('/admin/requests?limit=30')]);

  $('#health').textContent='● online';$('#health').className='pill ok';

  $('#stats').innerHTML=[
    ['Requests 24h',stats.requests_24h,ICONS.req],
    ['Error rate',(stats.error_rate_24h*100).toFixed(1)+'%',ICONS.err],
    ['Tokens in',stats.tokens_in_24h,ICONS.tin],
    ['Tokens out',stats.tokens_out_24h,ICONS.tout],
    ['Providers',stats.providers,ICONS.prov],
    ['Keys healthy',stats.keys_healthy+'/'+stats.keys_total,ICONS.key],
    ['In cooldown',stats.keys_in_cooldown,ICONS.cd],
  ].map(([l,v,i])=>`<div class="ui-card stat-card"><div class="ico">${i}</div><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');

  const fmtTok=n=>n==null?'—':n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'k':n;
  document.querySelector('#budget tbody').innerHTML=(stats.token_budget||[]).map(b=>{
    const pct=b.est_daily?Math.min(100,(b.used_24h/b.est_daily)*100):null;
    const bar=pct==null?'<span class="tx-3 t-xs">no estimate</span>':
      `<div style="background:var(--bg-3,#222);border-radius:6px;height:8px;overflow:hidden">
        <div style="width:${pct.toFixed(1)}%;height:100%;background:${pct>85?'var(--danger,#e5484d)':pct>60?'var(--warn,#f5a524)':'var(--accent,#7c5cff)'}"></div>
      </div><div class="tx-3 t-xs" style="margin-top:3px">${pct.toFixed(1)}% used</div>`;
    return `<tr><td><b>${esc(b.provider)}</b></td><td>${b.keys}</td>
      <td>${fmtTok(b.used_24h)}</td><td>${b.est_daily?'~'+fmtTok(b.est_daily):'—'}</td><td>${bar}</td></tr>`;
  }).join('')||'<tr><td colspan="5" class="tx-3">No providers</td></tr>';
  if(stats.est_tokens_total)
    document.querySelector('#budget tbody').insertAdjacentHTML('beforeend',
      `<tr id="budgetTotal"><td><b>total est.</b></td><td></td>
       <td><b>${fmtTok(stats.tokens_in_24h+stats.tokens_out_24h)}</b></td>
       <td><b>~${fmtTok(stats.est_tokens_total)}</b></td><td></td></tr>`);

  document.querySelector('#providers tbody').innerHTML=providers.map(p=>`<tr>
    <td><b>${esc(p.name)}</b><br><span class="tx-3 t-xs">${esc(p.display_name)}</span></td>
    <td class="tx-2">${p.rotation_strategy}</td><td>${p.key_count}</td><td>${p.healthy_key_count}</td>
    <td class="tx-3 t-xs">${fmt.limits(p.limits)}</td>
    <td><div class="actions">
      <button class="ui-btn ui-btn-ghost ui-btn-sm" onclick="toggleP('${p.name}',${!p.enabled})">${p.enabled?'disable':'enable'}</button>
      <button class="ui-btn ui-btn-ghost ui-btn-sm" onclick="refreshModels('${p.name}')">refresh models</button>
    </div></td>
  </tr>`).join('');

  $('#kp').innerHTML=providers.map(p=>`<option>${esc(p.name)}</option>`).join('');

  document.querySelector('#keys tbody').innerHTML=keys.map(k=>{
    const status=!k.enabled?['bad','disabled']:
      (k.cooldown_until&&new Date(k.cooldown_until)>new Date())?['warn','cooldown']:['ok','healthy'];
    return `<tr>
      <td>${esc(k.provider)}</td><td>${esc(k.label)}</td><td class="mono">…${esc(k.key_hint)}</td>
      <td>${k.quota_group?`<span class="pill info">${esc(k.quota_group)}</span>`:'—'}</td>
      <td>${k.daily_request_count}</td><td>${k.daily_token_count}</td>
      <td class="tx-3">${k.cooldown_until?fmt.time(k.cooldown_until):'—'}</td>
      <td><span class="pill ${status[0]}">${status[1]}</span></td>
      <td><div class="actions">
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

window.addEventListener('admin-ready',refresh);
setInterval(()=>{if(document.getElementById('loginGate').style.display==='none')refresh();},15000);
if(innerWidth<=820)document.getElementById('menuBtn').style.display='grid';
