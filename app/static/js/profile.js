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
    if(u.github_username)
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
      const kinds=usage.by_kind||{};
      const tokens=Object.values(kinds)
        .reduce((s,k)=>s+(k.tokens_in||0)+(k.tokens_out||0),0);
      tiles.push(statTile(usage.total_events,'Events · 30d'));
      if(tokens)tiles.push(statTile(tokens,'Tokens · 30d'));
    }
    if(tiles.length){stats.innerHTML=tiles.join('');stats.hidden=false;}
    else stats.hidden=true;
  }

  function statTile(n,l){
    return `<div class="pf-stat"><div class="n">${Number(n||0).toLocaleString()}</div><div class="l">${l}</div></div>`;
  }

  function paintGithub(gh){
    const bits=[];
    bits.push(`<div class="gh-head">
      <div class="gh-avatar">${gh.avatar_url?`<img src="${esc(gh.avatar_url)}" alt="">`:'?'}</div>
      <div style="flex:1;min-width:180px">
        <div style="font-weight:700;font-size:15px">${esc(gh.name||gh.login)}
          <a class="t-xs" style="color:var(--accent);margin-left:8px" href="${esc(gh.html_url)}" target="_blank" rel="noopener">@${esc(gh.login)} ↗</a></div>
        ${gh.bio?`<div class="t-sm tx-2" style="margin-top:2px">${esc(gh.bio)}</div>`:''}
      </div></div>`);
    const meta=[];
    if(gh.company)meta.push(`<span>🏢 ${esc(gh.company)}</span>`);
    if(gh.location)meta.push(`<span>📍 ${esc(gh.location)}</span>`);
    if(gh.blog){
      const href=/^https?:\/\//.test(gh.blog)?gh.blog:'https://'+gh.blog;
      meta.push(`<span>🔗 <a href="${esc(href)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(gh.blog)}</a></span>`);
    }
    if(gh.created_at)meta.push(`<span>On GitHub since <b>${fmtDate(gh.created_at)}</b></span>`);
    if(meta.length)bits.push(`<div class="gh-meta">${meta.join('')}</div>`);
    bits.push(`<div class="pf-stats">`+
      statTile(gh.public_repos,'Repositories')+
      statTile(gh.followers,'Followers')+
      statTile(gh.following,'Following')+
      statTile(gh.public_gists,'Gists')+`</div>`);
    if(gh.repos&&gh.repos.length){
      bits.push(`<div class="gh-repos">`+gh.repos.map(r=>{
        const lang=r.language
          ?`<span><i class="dot" style="background:${LANG_COLORS[r.language]||'var(--text-3)'}"></i>${esc(r.language)}</span>`:'';
        return `<a class="gh-repo" href="${esc(r.html_url)}" target="_blank" rel="noopener">
          <div class="rname">${esc(r.name)}</div>
          ${r.description?`<div class="rdesc">${esc(r.description)}</div>`:''}
          <div class="rmeta">${lang}<span>★ ${r.stars}</span><span>⑂ ${r.forks}</span></div>
        </a>`;}).join('')+`</div>`);
    }
    $('#ghBody').innerHTML=bits.join('');
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

  const FIELDS=['display_name','pronouns','company','location','website',
    'github_username','avatar_url','bio'];
  const INPUTS={display_name:'#pfNameIn',pronouns:'#pfProIn',company:'#pfCoIn',
    location:'#pfLocIn',website:'#pfWebIn',github_username:'#pfGhIn',
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
    fillForm(u);
    paintHero(u,me.limits);

    // 30-day usage stats — optional; card still renders without them
    let usage=null;
    try{usage=await v1Api('/v1/auth/usage');}catch(e){}
    if(usage)paintHero(u,me.limits,null,usage);

    let gh=null;
    if(u.github_username)gh=await loadGithub(u.github_username);
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
        u=nu;
        localStorage.setItem('nova_user',JSON.stringify(nu));
        paintHero(nu,me.limits,null,usage);
        if(nu.github_username){
          const g=await loadGithub(nu.github_username);
          if(g&&g.avatar_url&&!nu.avatar_url)
            setAvatar($('#pfAvatar'),g.avatar_url,initials(nu).toUpperCase());
        }else{
          $('#ghBody').innerHTML='<div class="t-sm tx-3">Link a GitHub username below to show your public profile and top repositories.</div>';
        }
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
