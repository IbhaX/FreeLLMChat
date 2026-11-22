/* Profile page — account hero, edit form, linked GitHub profile. */
(function(){
  "use strict";
  if(window.AdminShell)AdminShell.mount('profile');
  const $=(s)=>document.querySelector(s);

  const LANG_COLORS={JavaScript:'#f1e05a',TypeScript:'#3178c6',Python:'#3572A5',
    Go:'#00ADD8',Rust:'#dea584',Java:'#b07219','C++':'#f34b7d',C:'#555',
    'C#':'#178600',Ruby:'#701516',PHP:'#4F5D95',Swift:'#F05138',
    Kotlin:'#A97BFF',HTML:'#e34c26',CSS:'#563d7c',Shell:'#89e051'};

  function initials(u){
    const n=(u.display_name||u.username||'?').trim().split(/\s+/);
    return ((n[0]&&n[0][0])||'?')+((n[1]&&n[1][0])||'');
  }

  function fmtDate(iso){
    if(!iso)return'';
    const d=new Date(iso);
    return isNaN(d)?'':d.toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'});
  }

  function setAvatar(el,url,init){
    el.textContent='';
    if(url){
      const img=document.createElement('img');
      img.src=url;img.alt='avatar';img.referrerPolicy='no-referrer';
      img.onerror=()=>{el.textContent=init;};
      el.append(img);
    }else el.textContent=init;
  }

  function paintHero(u,limits,ghAvatar,usage){
    const init=initials(u).toUpperCase();
    setAvatar($('#pfAvatar'),u.avatar_url||ghAvatar||'',init);
    $('#pfName').textContent=u.display_name||u.username;
    $('#pfHandle').textContent='@'+u.username+(u.pronouns?' · '+u.pronouns:'');
    $('#pfPills').innerHTML=
      `<span class="pill ${u.role==='admin'?'ok':'mut'}">${esc(u.role||'user')}</span>`+
      `<span class="pill">${esc(u.plan||'free')} plan</span>`;
    const bio=$('#pfBio');
    if(u.bio){bio.textContent=u.bio;bio.classList.remove('pf-empty');}
    else{bio.textContent='No bio yet — add one in Edit profile.';bio.classList.add('pf-empty');}
    const meta=[];
    if(u.email)meta.push(`<span>✉ ${esc(u.email)}</span>`);
    if(u.company)meta.push(`<span>🏢 ${esc(u.company)}</span>`);
    if(u.location)meta.push(`<span>📍 ${esc(u.location)}</span>`);
    if(u.website){
      const label=u.website.replace(/^https?:\/\//,'');
      meta.push(`<span>🔗 <a href="${esc(u.website)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(label)}</a></span>`);
    }
    if(u.github_connected&&u.github_username)
      meta.push(`<span>⌥ <a href="https://github.com/${esc(u.github_username)}" target="_blank" rel="noopener" style="color:var(--accent)">github.com/${esc(u.github_username)}</a></span>`);
    if(u.created_at)meta.push(`<span>Joined <b>${fmtDate(u.created_at)}</b></span>`);
    $('#pfMeta').innerHTML=meta.join('');
    const stats=$('#pfStats');
    const tiles=[];
    if(limits&&limits.requests_per_day)
      tiles.push(statTile(limits.requests_per_day,'Requests/day'));
    if(limits&&limits.research_per_day)
      tiles.push(statTile(limits.research_per_day,'Research/day'));
    if(usage){
      tiles.push(statTile(usage.total_events,'Events · 30d'));
      const tokens=(usage.credits&&usage.credits.tokens)||0;
      if(tokens)tiles.push(statTile(tokens,'Tokens · 30d'));
    }
    if(tiles.length){stats.innerHTML=tiles.join('');stats.hidden=false;}
    else stats.hidden=true;
  }

  /* Credit meter — 100 billed tokens = 1 credit, allowance per plan.
     usage.credits comes from /v1/auth/usage (30-day window). */
  function paintCredits(usage){
    const box=$('#pfCredit');
    const c=usage&&usage.credits;
    if(!c||!c.limit){box.hidden=true;return;}
    box.hidden=false;
    const used=c.used||0,left=Math.max(0,c.limit-used);
    $('#pfCredLeft').textContent=left.toLocaleString();
    $('#pfCredUsed').textContent=
      `${used.toLocaleString()} / ${c.limit.toLocaleString()} credits · ${usage.plan||''} plan`;
    $('#pfCredBar').style.width=
      Math.min(100,Math.round(used/c.limit*100))+'%';
  }

  async function refreshUsage(){
    try{
      const usage=await v1Api('/v1/auth/usage');
      paintCredits(usage);
      if(currentUser)paintHero(currentUser,currentUser.limits,null,usage);
      return usage;
    }catch(e){return null;}
  }

  function statTile(n,l){
    return `<div class="pf-stat"><div class="n">${Number(n||0).toLocaleString()}</div><div class="l">${l}</div></div>`;
  }

  let currentGh=null;

  function repoModal(r){
    const d=document.createElement('dialog');
    d.className='gh-modal';
    const kv=(k,v)=>v?`<div class="ghm-kv"><b>${k}</b>${v}</div>`:'';
    const langDot=r.language
      ?`<i class="dot" style="display:inline-block;width:9px;height:9px;border-radius:50%;
        background:${LANG_COLORS[r.language]||'var(--text-3)'};margin-right:6px"></i>${esc(r.language)}`:'';
    d.innerHTML=`
      <div style="display:flex;align-items:flex-start;gap:10px">
        <h2 style="font-size:17px;margin:0;word-break:break-all;flex:1">${esc(r.name)}
          ${r.archived?' <span class="pill warn" style="font-size:10px;vertical-align:2px">archived</span>':''}</h2>
        <button class="ui-btn ui-btn-ghost ui-btn-icon" id="ghmClose" title="Close">✕</button>
      </div>
      ${r.description?`<div class="t-sm tx-2" style="margin:8px 0 2px">${esc(r.description)}</div>`:''}
      ${r.topics&&r.topics.length?`<div class="ghm-topics">${r.topics.map(t=>`<span>${esc(t)}</span>`).join('')}</div>`:''}
      <div class="ghm-stats">
        <span>★ ${r.stars}</span><span>⑂ ${r.forks}</span>
        <span>👁 ${r.watchers}</span><span>⚠ ${r.open_issues} issues</span>
      </div>
      <div class="ghm-grid">
        ${kv('Language',langDot)}
        ${kv('Default branch',esc(r.default_branch))}
        ${kv('License',esc(r.license))}
        ${kv('Created',fmtDate(r.created_at))}
        ${kv('Last push',fmtDate(r.pushed_at||r.updated_at))}
        ${kv('Homepage',r.homepage?`<a href="${esc(r.homepage)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(r.homepage.replace(/^https?:\/\//,''))}</a>`:'')}
      </div>
      <div class="nova-dialog-actions">
        <button class="ui-btn" id="ghmCancel">Close</button>
        <a class="ui-btn ui-btn-primary" href="${esc(r.html_url)}" target="_blank" rel="noopener">Open on GitHub ↗</a>
      </div>`;
    const dismiss=()=>d.close();
    d.querySelector('#ghmClose').onclick=dismiss;
    d.querySelector('#ghmCancel').onclick=dismiss;
    d.addEventListener('close',()=>{window.removeEventListener('pagehide',dismiss);d.remove();},{once:true});
    window.addEventListener('pagehide',dismiss,{once:true});
    document.body.append(d);
    d.showModal();
    if(window.NovaMotion)NovaMotion.dialog(d);
  }

  function paintGithub(gh){
    currentGh=gh;
    const bits=[];
    bits.push(`<div style="display:flex;gap:8px;align-items:center;margin-bottom:14px;flex-wrap:wrap">
      <span class="pill ok">Connected</span>
      <a class="t-sm" style="color:var(--accent);font-weight:600"
        href="${esc(gh.html_url)}" target="_blank" rel="noopener">@${esc(gh.login)} ↗</a>
      <span class="t-xs tx-3">on GitHub since ${fmtDate(gh.created_at)||'—'}</span>
      <button class="ui-btn ui-btn-ghost ui-btn-sm ui-btn-danger" id="ghDisconnect"
        style="margin-left:auto">Disconnect</button></div>`);
    bits.push(`<div class="pf-stats">`+
      statTile(gh.public_repos,'Repositories')+
      statTile(gh.followers,'Followers')+
      statTile(gh.following,'Following')+
      statTile(gh.public_gists,'Gists')+`</div>`);
    if(gh.repos&&gh.repos.length){
      bits.push(`<div class="gh-repos">`+gh.repos.map(r=>{
        const lang=r.language
          ?`<span><i class="dot" style="background:${LANG_COLORS[r.language]||'var(--text-3)'}"></i>${esc(r.language)}</span>`:'';
        return `<button type="button" class="gh-repo" data-repo="${esc(r.name)}">
          <div class="rname">${esc(r.name)}</div>
          ${r.description?`<div class="rdesc">${esc(r.description)}</div>`:''}
          <div class="rmeta">${lang}<span>★ ${r.stars}</span><span>⑂ ${r.forks}</span></div>
        </button>`;}).join('')+`</div>`);
    }
    $('#ghBody').innerHTML=bits.join('');
    $('#ghDisconnect').onclick=disconnectGithub;
    document.querySelectorAll('#ghBody .gh-repo').forEach(b=>b.onclick=()=>{
      const r=currentGh&&currentGh.repos
        .find(x=>x.name===b.dataset.repo);
      if(r)repoModal(r);
    });
    if(window.NovaMotion){
      NovaMotion.stagger(document.querySelectorAll('#ghBody .pf-stat'),{y:10,dur:.3,stagger:.05});
      NovaMotion.stagger(document.querySelectorAll('#ghBody .gh-repo'),{y:12,dur:.35,stagger:.04});
      NovaMotion.liftAll('#ghBody .gh-repo',null,{y:-3});
    }
  }

  async function loadGithub(username){
    const body=$('#ghBody');
    body.innerHTML='<div class="t-sm tx-3">Loading GitHub profile…</div>';
    try{
      const j=await v1Api('/v1/auth/github?username='+encodeURIComponent(username));
      paintGithub(j.github);
      return j.github;
    }catch(e){
      body.innerHTML=`<div class="t-sm" style="color:var(--error)">${esc(e.message||'GitHub lookup failed')}</div>`;
      return null;
    }
  }

  /* GitHub card states: nothing renders as "connected" until the
     account passes through /github/connect or the OAuth callback. */
  let currentUser=null;

  function paintGhDisconnected(){
    const u=currentUser||{};
    $('#ghBody').innerHTML=
      `<div class="t-sm tx-2" style="margin-bottom:12px">`+
      (u.github_username
        ?`@${esc(u.github_username)} is saved but <b>not connected</b> — GitHub only shows here after a real connect.`
        :`No GitHub account connected. Connect GitHub to show your public profile and top repositories.`)+
      `</div><button class="ui-btn ui-btn-primary ui-btn-sm" id="ghConnect">Connect GitHub</button>`;
    $('#ghConnect').onclick=connectGithub;
  }

  async function connectGithub(){
    try{
      const j=await v1Api('/v1/auth/github/oauth/start');
      if(j&&j.url){location.href=j.url;return;}
    }catch(e){/* 501 — OAuth not configured; fall through to verified link */}
    const v=await NovaUI.Dialog.form({
      title:'Connect GitHub',
      message:'Enter your GitHub username. The account is marked '
        +'connected only after it resolves to a real public profile.',
      fields:[{name:'username',type:'text',required:true,
        label:'GitHub username',
        value:(currentUser&&currentUser.github_username)||''}],
      confirmLabel:'Connect',
    });
    if(!v)return;
    try{
      const j=await v1Api('/v1/auth/github/connect',{
        method:'POST',body:JSON.stringify({username:v.username})});
      currentUser=j.user;
      localStorage.setItem('nova_user',JSON.stringify(currentUser));
      paintHero(currentUser,null,null,null);
      paintGithub(j.github);
      toast('GitHub connected','ok');
    }catch(e){
      toast(e.message||'GitHub connect failed','err');
      paintGhDisconnected();
    }
  }

  async function disconnectGithub(){
    try{
      const j=await v1Api('/v1/auth/github/disconnect',{method:'POST'});
      currentUser=j.user;
      localStorage.setItem('nova_user',JSON.stringify(currentUser));
      paintHero(currentUser,null,null,null);
      paintGhDisconnected();
      toast('GitHub disconnected','ok');
    }catch(e){toast(e.message||'Disconnect failed','err');}
  }

  const FIELDS=['display_name','pronouns','company','location','website',
    'avatar_url','bio'];
  const INPUTS={display_name:'#pfNameIn',pronouns:'#pfProIn',company:'#pfCoIn',
    location:'#pfLocIn',website:'#pfWebIn',
    avatar_url:'#pfAvIn',bio:'#pfBioIn'};

  function fillForm(u){
    $('#pfUserIn').value=u.username||'';
    $('#pfEmailIn').value=u.email||'—';
    for(const f of FIELDS)$(INPUTS[f]).value=u[f]||'';
  }

  const editCard=$('#editCard');
  function setEdit(open){
    if(open){
      editCard.hidden=false;
      $('#pfEdit').textContent='Close editor';
      if(window.NovaMotion)NovaMotion.stagger(
        editCard.querySelectorAll('.pf-grid > div'),{y:10,dur:.25,stagger:.03});
    }else{
      editCard.hidden=true;
      $('#pfEdit').textContent='Edit profile';
    }
  }

  async function init(){
    let me;
    try{me=await v1Api('/v1/auth/me');}catch(e){return;} // auth.js redirects
    let u=me.user;if(!u)return;
    currentUser=u;currentUser.limits=me.limits;
    fillForm(u);
    paintHero(u,me.limits);

    // 30-day usage stats — optional; card still renders without them.
    // Polled every 15s while the tab is visible so credits live-update.
    let usage=await refreshUsage();
    setInterval(()=>{if(!document.hidden)refreshUsage();},15000);
    document.addEventListener('visibilitychange',()=>{
      if(!document.hidden)refreshUsage();});

    let gh=null;
    if(u.github_connected&&u.github_username)
      gh=await loadGithub(u.github_username);
    else paintGhDisconnected();
    if(gh&&gh.avatar_url&&!u.avatar_url)
      setAvatar($('#pfAvatar'),gh.avatar_url,initials(u).toUpperCase());

    $('#pfEdit').onclick=()=>setEdit(editCard.hidden);
    $('#pfCancel').onclick=()=>{fillForm(u);$('#pfErr').textContent='';setEdit(false);};

    $('#pfSave').onclick=async()=>{
      $('#pfErr').textContent='';
      const btn=$('#pfSave');btn.disabled=true;btn.textContent='Saving…';
      try{
        const body={};
        for(const f of FIELDS)body[f]=$(INPUTS[f]).value;
        const j=await v1Api('/v1/auth/me',{method:'PATCH',body:JSON.stringify(body)});
        const nu=j.user;
        u=nu;currentUser=nu;
        localStorage.setItem('nova_user',JSON.stringify(nu));
        paintHero(nu,me.limits,null,usage);
        setEdit(false);
        toast('Profile saved','ok');
      }catch(e){
        $('#pfErr').textContent=e.message||'Save failed';
      }finally{btn.disabled=false;btn.textContent='Save changes';}
    };

    $('#pfLogout').onclick=()=>{
      if(window.NovaAuth)NovaAuth.logout();else location.href='/login';
    };
  }

  if(document.readyState==='loading')
    document.addEventListener('DOMContentLoaded',init);
  else init();
})();
