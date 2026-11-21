/* Shared app navigation — single source of truth for destinations.
   AppNav.NAV feeds AdminShell's sidebar; AppNav.attach(btn, activeId)
   gives immersive pages (chat, roleplay) an app-switcher popover so any
   page can reach any other page. Load before admin-shell.js. */
(function(){
  const NAV=[
    {sec:'Product'},
    {id:'dashboard',href:'/dashboard',label:'Dashboard',ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>'},
    {id:'chat',href:'/chat',label:'Chat',ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'},
    {id:'research',href:'/research',label:'Research',ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>'},
    {id:'roleplay',href:'/roleplay',label:'Roleplay',ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>'},
    {id:'agents',href:'/agents',label:'Agents',ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M12 8V4m-4 8h.01M16 12h.01M9 16h6"/></svg>'},
    {id:'skills',href:'/skills',label:'Skills',ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m12 2 3 6 6 .9-4.5 4.2 1 6.4-5.5-3-5.5 3 1-6.4L3 8.9 9 8z"/></svg>'},
    {id:'memory',href:'/memory',label:'Memory',ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.5 2A5.5 5.5 0 0 0 4 7.5c0 .98.25 1.9.7 2.7A5.5 5.5 0 0 0 4 13.5 5.5 5.5 0 0 0 9.5 19c.36 0 .7-.04 1.04-.1A3.5 3.5 0 0 0 14 22a3.5 3.5 0 0 0 3.46-3.1A5.5 5.5 0 0 0 20 13.5a5.5 5.5 0 0 0-.7-3.3c.45-.8.7-1.72.7-2.7A5.5 5.5 0 0 0 14.5 2a5.6 5.6 0 0 0-2.5.6A5.6 5.6 0 0 0 9.5 2z"/><circle cx="9" cy="8" r=".8" fill="currentColor"/><circle cx="15" cy="8" r=".8" fill="currentColor"/><circle cx="12" cy="13" r=".8" fill="currentColor"/><circle cx="8" cy="15" r=".8" fill="currentColor"/><circle cx="16" cy="15" r=".8" fill="currentColor"/></svg>'},
    {sec:'Workspace'},
    {id:'profile',href:'/profile',label:'Profile',ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/></svg>'},
    {id:'library',href:'/library',label:'Library',ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V4H6.5A2.5 2.5 0 0 0 4 6.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/></svg>'},
    {id:'keys',href:'/keys',label:'API Keys',admin:true,ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="7.5" cy="15.5" r="4.5"/><path d="M11 12 21 2m-3 3 3 3"/></svg>'},
    {id:'settings',href:'/settings',label:'Settings',admin:true,ico:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 19.4 9c.23.61.84 1 1.51 1H21a2 2 0 1 1 0 4h-.09c-.67 0-1.28.39-1.51 1z"/></svg>'},
  ];

  const GRID='<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>';
  const BACK='<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5m7-7-7 7 7 7"/></svg>';

  /* History-back with a safe fallback: only goes back when the previous
     entry is same-origin, otherwise lands on the page's fallback route. */
  function goBack(fallback){
    try{
      if(document.referrer){
        const same=new URL(document.referrer).origin===location.origin;
        if(same&&history.length>1){history.back();return;}
      }
    }catch(_){}
    location.href=fallback||'/';
  }

  /* Auto-wire every <button data-app-back="/fallback"> in the page. */
  function initBack(){
    document.querySelectorAll('[data-app-back]').forEach(b=>{
      if(!b.innerHTML.trim())b.innerHTML=BACK;
      b.title=b.title||'Back';
      b.addEventListener('click',()=>goBack(b.getAttribute('data-app-back')||'/'));
    });
  }
  if(document.readyState==='loading')
    document.addEventListener('DOMContentLoaded',initBack);
  else initBack();

  /* Top navbar — horizontal destination strip injected into every page.
     Hidden on desktop where the admin sidebar exists (body.has-shell);
     always visible on immersive pages and on mobile. */
  const PATH_MAP={'/dashboard':'dashboard','/chat':'chat','/research':'research',
    '/roleplay':'roleplay','/agents':'agents','/skills':'skills',
    '/library':'library','/keys':'keys','/settings':'settings',
    '/profile':'profile','/memory':'memory'};
  function initTopnav(){
    if(document.querySelector('.app-topnav'))return;
    const active=PATH_MAP[location.pathname]||null;
    const nav=document.createElement('nav');
    nav.className='app-topnav';
    nav.innerHTML='<a class="tb-brand" href="/">Nova</a>'
      +NAV.filter(n=>n.href).map(n=>
        `<a class="tn-link${n.id===active?' sel':''}" href="${n.href}"${n.admin?' data-admin="1"':''}>${n.ico}${n.label}</a>`).join('')
      +'<button class="tn-burger ui-btn ui-btn-ghost ui-btn-icon" title="Apps — all pages"></button>';
    AppNav.attach(nav.querySelector('.tn-burger'),active);
    nav.addEventListener('pointerover',e=>{
      const a=e.target.closest('a.tn-link');
      if(a)prefetch(a.getAttribute('href'));
    });
    const host=document.querySelector('.ui-main')
      ||document.querySelector('.stage')
      ||document.querySelector('main.main')
      ||document.body;
    host.insertBefore(nav,host.firstChild);
    if(window.NovaMotion)NovaMotion.enter(nav,{y:-12,dur:NovaMotion.D.med});
    if(window.NovaAuth&&NovaAuth.applyRoleNav)NovaAuth.applyRoleNav();
  }
  if(document.readyState==='loading')
    document.addEventListener('DOMContentLoaded',initTopnav);
  else initTopnav();

  const _prefetched=new Set();
  /* Warm the browser cache for a destination so the click feels instant.
     Cheap: each page is a small HTML shell; the JS/CSS are already shared. */
  function prefetch(href){
    if(!href||href.startsWith('#')||_prefetched.has(href))return;
    _prefetched.add(href);
    const l=document.createElement('link');
    l.rel='prefetch';l.href=href;l.as='document';
    document.head.appendChild(l);
  }

  function buildMenu(active){
    const menu=document.createElement('div');
    menu.className='ui-menu';
    menu.innerHTML=NAV.map(n=>n.sec
      ?`<div class="mi-head">${n.sec}</div>`
      :`<a class="mi appnav-mi ${n.id===active?'sel':''}" href="${n.href}"${n.admin?' data-admin="1"':''}>${n.ico}<span class="tb-tx">${n.label}</span></a>`).join('');
    // Hover prefetch: warm each destination as the pointer travels the menu.
    menu.addEventListener('pointerover',e=>{
      const a=e.target.closest('a.mi');
      if(a)prefetch(a.getAttribute('href'));
    });
    return menu;
  }

  window.AppNav={
    NAV,
    GRID,
    BACK,
    prefetch,
    goBack,
    /* Toggle an anchored app-switcher popover on a button click.
       Empty buttons get the grid glyph so any page can wire just an
       <button id="appNavBtn"></button> in the top-left corner. */
    attach(btn,active){
      if(btn&&!btn.innerHTML.trim())btn.innerHTML=GRID;
      let menu=null;
      const close=()=>{
        if(menu){menu.remove();menu=null;}
        document.removeEventListener('click',onDoc,true);
      };
      const onDoc=e=>{
        if(menu&&!menu.contains(e.target)&&!btn.contains(e.target))close();
      };
      btn.addEventListener('click',e=>{
        e.stopPropagation();
        if(menu){close();return;}
        menu=buildMenu(active);
        document.body.appendChild(menu);
        if(window.NovaAuth&&NovaAuth.applyRoleNav)NovaAuth.applyRoleNav();
        const r=btn.getBoundingClientRect();
        const mw=menu.offsetWidth,mh=menu.offsetHeight;
        menu.style.position='fixed';
        menu.style.zIndex='500';
        menu.style.left=Math.max(8,Math.min(r.left,innerWidth-mw-12))+'px';
        menu.style.top=(r.top>mh+60?r.top-mh-8:r.bottom+8)+'px';
        if(window.NovaMotion){
          NovaMotion.menu(menu,true);
          NovaMotion.stagger(menu.querySelectorAll('.mi,.mi-head'),
            {y:6,x:0,dur:NovaMotion.D.fast,stagger:.02});}
        document.addEventListener('click',onDoc,true);
        // Warm every destination once the switcher opens — by the time the
        // user picks one it's usually already in cache.
        NAV.forEach(n=>{if(n.href)prefetch(n.href);});
      });
      return btn;
    },
    /* Create a styled launcher button ready to drop into a header/footer. */
    launcher(active,className){
      const btn=document.createElement('button');
      btn.className=className||'ui-btn ui-btn-ghost ui-btn-icon';
      btn.title='Apps';
      btn.innerHTML=GRID;
      return this.attach(btn,active);
    },
  };
})();
