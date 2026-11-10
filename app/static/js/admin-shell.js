/* Shared admin shell — sidebar nav + auth gate for /, /keys, /settings.
   Usage: AdminShell.mount('dashboard'|'keys'|'settings') — injects the
   sidebar into <body> wrapping existing <main class="ui-main"> markup, and
   shows the login card when admin auth fails. */
(function(){
  const NAV=[
    {id:'dashboard',href:'/',label:'Dashboard',ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>'},
    {id:'keys',href:'/keys',label:'API Keys',ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="7.5" cy="15.5" r="4.5"/><path d="M11 12 21 2m-3 3 3 3"/></svg>'},
    {id:'settings',href:'/settings',label:'Settings',ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9c.23.61.84 1 1.51 1H21a2 2 0 1 1 0 4h-.09c-.67 0-1.28.39-1.51 1z"/></svg>'},
  ];

  function sidebarHtml(active){
    return `<div class="sb-head" style="display:flex;align-items:center;gap:10px;padding:2px 6px 10px">
      <div class="avatar" style="width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,var(--accent),var(--accent-2));display:grid;place-items:center;color:#fff;font-weight:700;font-size:14px">F</div>
      <div><div style="font-weight:700;font-size:14px">FreeLLM</div><div style="font-size:10px;color:var(--text-3)">Gateway admin</div></div>
      <button class="ui-btn ui-btn-ghost ui-btn-icon" id="sbClose" style="margin-left:auto;display:none">✕</button>
    </div>
    <div class="nav-sec">Workspace</div>
    ${NAV.map(n=>`<a class="ui-navitem ${n.id===active?'active':''}" href="${n.href}">${n.ico}${n.label}</a>`).join('')}
    <div class="nav-sec">Apps</div>
    <a class="ui-navitem" href="/chat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>Chat<span style="margin-left:auto;font-size:10px;color:var(--text-3)">↗</span></a>
    <div style="flex:1"></div>
    <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-top:1px solid var(--border-1)">
      <span id="adminDot" class="pill mut" style="font-size:9px">●</span>
      <span class="t-xs tx-3" style="flex:1">admin</span>
      <button class="ui-btn ui-btn-ghost ui-btn-icon" data-theme-btn title="Toggle theme"></button>
      <button class="ui-btn ui-btn-ghost ui-btn-icon" id="logoutBtn" title="Sign out"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg></button>
    </div>`;
  }

  window.AdminShell={
    mount(active){
      const nav=document.createElement('aside');
      nav.className='ui-sidenav';nav.id='adminSide';
      nav.innerHTML=sidebarHtml(active);
      const shell=document.createElement('div');
      shell.className='ui-shell';
      const main=document.querySelector('.ui-main');
      document.body.insertBefore(shell,document.body.firstChild);
      shell.appendChild(nav);shell.appendChild(main);
      // login card
      const login=document.createElement('div');
      login.id='loginGate';
      login.innerHTML=`<div class="ui-modal" style="position:fixed"><div class="ui-modal-card" style="width:380px;max-width:calc(100vw - 32px);padding:28px">
        <div class="avatar" style="width:40px;height:40px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent-2));display:grid;place-items:center;color:#fff;font-weight:700;font-size:18px;margin-bottom:14px">F</div>
        <h3 style="margin-bottom:4px">FreeLLM Gateway</h3>
        <p class="tx-2 t-sm" style="margin:0 0 16px">Enter your admin token to continue.</p>
        <form id="loginForm"><input type="password" id="loginToken" class="ui-input" placeholder="ADMIN_TOKEN" autofocus style="margin-bottom:10px">
        <button class="ui-btn ui-btn-primary" style="width:100%">Connect</button></form>
        <p id="loginErr" class="t-xs" style="color:var(--error);margin:8px 0 0"></p></div></div>`;
      document.body.appendChild(login);
      login.querySelector('#loginForm').onsubmit=async e=>{
        e.preventDefault();
        ADMIN_TOKEN=login.querySelector('#loginToken').value;
        sessionStorage.setItem('admin_token',ADMIN_TOKEN);
        try{await adminApi('/admin/stats');AdminShell.unlock();}
        catch(_){login.querySelector('#loginErr').textContent='Invalid token';}
      };
      document.querySelector('#logoutBtn').onclick=()=>{
        sessionStorage.removeItem('admin_token');location.reload();
      };
      // mobile sidebar toggle (admin pages add a hamburger in header)
      const menuBtn=document.getElementById('menuBtn');
      if(menuBtn)menuBtn.onclick=()=>nav.classList.toggle('open');
      document.addEventListener('click',e=>{
        if(innerWidth<=820&&!nav.contains(e.target)&&e.target.id!=='menuBtn')nav.classList.remove('open');
      });
      if(innerWidth<=820)document.getElementById('sbClose').style.display='grid';
      document.getElementById('sbClose').onclick=()=>nav.classList.remove('open');
      // initial auth state
      if(ADMIN_TOKEN){adminApi('/admin/stats').then(()=>AdminShell.unlock()).catch(()=>{});}
    },
    unlock(){
      document.getElementById('loginGate').style.display='none';
      document.getElementById('adminDot').className='pill ok';
      document.getElementById('adminDot').textContent='● connected';
      window.dispatchEvent(new Event('admin-ready'));
    }
  };
  window.addEventListener('admin-auth-fail',()=>{
    const g=document.getElementById('loginGate');if(g)g.style.display='grid';
    sessionStorage.removeItem('admin_token');ADMIN_TOKEN='';
  });
})();
