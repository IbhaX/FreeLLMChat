/* NovaAuth — app-wide sign-in gate.
   Loaded on every page via partials/ui.html. When NOVA_AUTH_REQUIRED is
   on, unauthenticated visitors are redirected to /login; a valid session
   injects the account chip (name + sign out) into the shared topnav.
   Exempt paths: /login and /share/* (share links are capabilities). */
(function(){
  const EXEMPT=/^\/(login|share)(\/|$)/;
  if(EXEMPT.test(location.pathname))return;

  const API_BASE=localStorage.getItem('gateway_url')||'';
  const headers=()=>({
    'Authorization':'Bearer '+(localStorage.getItem('gateway_key')||''),
    'X-User-Token':localStorage.getItem('nova_user_token')||'',
    'Content-Type':'application/json'});

  const toLogin=()=>{
    const next=encodeURIComponent(location.pathname+location.search);
    location.replace('/login?next='+next);
  };

  const state={required:false,user:null};
  const NS=window.NovaAuth={
    user:()=>state.user,
    required:()=>state.required,
    toLogin,
    on401(body){
      const t=body&&(body.detail&&body.detail.error&&body.detail.error.type);
      if(t==='authentication_required'||body&&body.detail==='Authentication required'){
        localStorage.removeItem('nova_user_token');localStorage.removeItem('nova_user');
        toLogin();return true;
      }
      return false;
    },
    async logout(){
      try{await fetch(API_BASE+'/v1/auth/logout',{method:'POST',headers:headers()});}catch(_){}
      localStorage.removeItem('nova_user_token');localStorage.removeItem('nova_user');
      toLogin();
    },
  };

  // With no stored token we can't know whether auth is required without
  // asking the server — hide the page for that one round-trip so a gated
  // install never flashes content.
  const hadToken=!!localStorage.getItem('nova_user_token');
  if(!hadToken)document.documentElement.style.visibility='hidden';
  const reveal=()=>{document.documentElement.style.visibility='';};

  function paintChip(){
    const nav=document.querySelector('.app-topnav');
    if(!nav||!state.user||nav.querySelector('.tn-user'))return;
    const box=document.createElement('span');
    box.className='tn-user';
    box.style.cssText='margin-left:auto;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-3)';
    const name=state.user.display_name||state.user.username||'';
    const nm=document.createElement('span');
    nm.style.cssText='max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
    nm.textContent=name;
    box.appendChild(nm);
    const out=document.createElement('button');
    out.className='ui-btn ui-btn-ghost ui-btn-icon';out.title='Sign out ('+name+')';
    out.innerHTML='<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>';
    out.onclick=()=>NS.logout();
    box.appendChild(out);
    nav.appendChild(box);
  }

  fetch(API_BASE+'/v1/auth/me',{headers:headers()})
    .then(r=>r.ok?r.json():null)
    .then(d=>{
      if(!d){reveal();return;}
      state.required=!!d.auth_required;
      if(d.user){
        state.user=d.user;
        localStorage.setItem('nova_user',JSON.stringify(d.user));
        reveal();
        // The shared topnav is injected by app-nav.js on DOMContentLoaded —
        // it may not exist yet on slow parses; poll briefly then give up.
        let n=0;const t=setInterval(()=>{paintChip();if(++n>30)clearInterval(t);},100);
        paintChip();
      }else if(state.required){
        localStorage.removeItem('nova_user_token');localStorage.removeItem('nova_user');
        toLogin();
      }else{
        reveal();
      }
    })
    .catch(reveal);
})();
