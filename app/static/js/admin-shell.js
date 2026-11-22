/* Shared admin shell — sidebar nav + admin-rights status for
   /, /keys, /settings, /agents, /research, /skills, /library.
   Usage: AdminShell.mount('dashboard'|'keys'|…) — injects the
   sidebar into <body> wrapping existing <main class="ui-main"> markup.
   Pages are already gated by user auth (auth.js → /login); there is no
   second password gate. A signed-in admin-role user satisfies /admin/*
   directly — the ADMIN_TOKEN remains only for external tooling.
   Destinations come from app-nav.js (AppNav.NAV) — single source of truth. */
(function(){
  const NAV=window.AppNav.NAV;

  function sidebarHtml(active){
    const u=window.NovaAuth&&NovaAuth.user();
    const name=u?(u.display_name||u.username):'';
    const role=u?u.role:'';
    return `<div class="sb-head" style="display:flex;align-items:center;gap:10px;padding:2px 6px 10px">
      <button class="avatar" id="appNavBtn" title="Apps — all pages" style="width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,var(--accent),var(--accent-2));display:grid;place-items:center;color:#fff;font-weight:700;font-size:14px;border:0;cursor:pointer">IX</button>
      <div><div style="font-weight:700;font-size:14px">IbhaX</div><div style="font-size:10px;color:var(--text-3)">AI workspace</div></div>
      <button class="ui-btn ui-btn-ghost ui-btn-icon" id="sbClose" style="margin-left:auto;display:none">✕</button>
    </div>
    ${NAV.map(n=>n.sec?`<div class="nav-sec">${n.sec}</div>`:`<a class="ui-navitem ${n.id===active?'active':''}" href="${n.href}"${n.admin?' data-admin="1"':''}${n.cap?` data-cap="${n.cap}"`:''}>${n.ico}${n.label}${n.cap?'<span class="tn-lock">🔒</span>':''}</a>`).join('')}
    <div style="flex:1"></div>
    <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-top:1px solid var(--border-1)">
      <span id="adminDot" class="pill mut" style="font-size:9px;display:none">●</span>
      <a href="/profile" class="t-xs tx-3 js-user-name" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-decoration:none;color:inherit" title="${name}${role?' · '+role:''}">${name||'admin'}</a>
      <button class="ui-btn ui-btn-ghost ui-btn-icon" data-theme-btn title="Toggle theme"></button>
      <button class="ui-btn ui-btn-ghost ui-btn-icon js-logout" id="logoutBtn" title="Sign out"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg></button>
    </div>`;
  }

  window.AdminShell={
    /* Friendly denied state for pages whose data is admin-only — replaces
       the page body with a card instead of bouncing the user elsewhere. */
    restricted(host){
      const el_=host||document.querySelector('.ui-page');
      if(!el_||el_.dataset.restricted)return;
      el_.dataset.restricted='1';
      el_.innerHTML=`<div class="ui-card ui-card-pad" style="text-align:center;padding:44px 22px;max-width:520px;margin:24px auto">
        <div style="font-size:30px;opacity:.55">🔒</div>
        <h2 style="margin:10px 0 4px;font-size:17px">Admin access required</h2>
        <div class="t-sm tx-3" style="max-width:400px;margin:0 auto 16px">
          Your account doesn't have the admin role, so this page's data is off-limits.
          An administrator can promote your account.</div>
        <div style="display:flex;gap:8px;justify-content:center">
          <a class="ui-btn ui-btn-primary ui-btn-sm" href="/chat">Go to Chat</a>
          <a class="ui-btn ui-btn-sm" href="/profile">My profile</a>
        </div></div>`;
    },
    mount(active){
      document.body.classList.add('has-shell');
      const nav=document.createElement('aside');
      nav.className='ui-sidenav';nav.id='adminSide';
      nav.innerHTML=sidebarHtml(active);
      if(window.AppNav)AppNav.attach(nav.querySelector('#appNavBtn'),active);
      const shell=document.createElement('div');
      shell.className='ui-shell';
      const main=document.querySelector('.ui-main');
      document.body.insertBefore(shell,document.body.firstChild);
      shell.appendChild(nav);shell.appendChild(main);
      if(window.NovaMotion){NovaMotion.sidebar(nav);NovaMotion.pageIn(main);}
      // Warm every nav destination — sidebar links feel instant on click.
      if(window.AppNav&&AppNav.prefetch)NAV.forEach(n=>{if(n.href)AppNav.prefetch(n.href);});
      // Hide admin-only items immediately when the cached role says so;
      // NovaAuth re-applies once /v1/auth/me resolves.
      if(window.NovaAuth&&NovaAuth.applyRoleNav)NovaAuth.applyRoleNav();
      if(window.AppNav&&AppNav.applyCapLocks)AppNav.applyCapLocks(nav);
      document.querySelector('#logoutBtn').onclick=()=>{
        if(window.NovaAuth)NovaAuth.logout();
        else location.href='/login';
      };
      // mobile sidebar toggle (admin pages add a hamburger in header)
      const menuBtn=document.getElementById('menuBtn');
      if(menuBtn)menuBtn.onclick=()=>nav.classList.toggle('open');
      document.addEventListener('click',e=>{
        if(innerWidth<=820&&!nav.contains(e.target)&&e.target.id!=='menuBtn')nav.classList.remove('open');
      });
      if(innerWidth<=820)document.getElementById('sbClose').style.display='grid';
      document.getElementById('sbClose').onclick=()=>nav.classList.remove('open');
      // The role pill labels the ACCOUNT, not session reachability — a
      // stored ADMIN_TOKEN can satisfy /admin/* for any signed-in user,
      // so the probe result must not drive what this shows.
      const dot=nav.querySelector('#adminDot');
      const paintRole=()=>{
        const u=window.NovaAuth&&NovaAuth.user();
        if(!u){dot.style.display='none';return;}
        dot.style.display='';
        if(u.role==='admin'){dot.className='pill ok';dot.textContent='● admin';dot.title='Admin role';}
        else{dot.className='pill mut';dot.textContent='● '+(u.role||'user');dot.title='Standard user role';}
      };
      paintRole();
      // One probe: admin-role users satisfy /admin/* via their session.
      // Pages init on 'admin-ready' regardless — user-level surfaces work
      // for non-admin users; admin calls surface 401s individually.
      adminApi('/admin/stats',{noRedirect:true})
        .catch(()=>{})
        .finally(()=>{paintRole();window.dispatchEvent(new Event('admin-ready'));});
    }
  };
  // adminApi 401s land here instead of a password gate — a signed-in
  // non-admin user sees a status, not a login wall.
  window.addEventListener('admin-auth-fail',()=>{
    if(window.toast)toast('Admin role required for that action','err');
  });
})();
