/* Theme management — persisted in localStorage('ui-theme').
   <html data-theme> is set by an inline <head> snippet to avoid FOUC;
   this module only wires toggles and helpers. */
(function(){
  const KEY='ui-theme';
  window.UITheme={
    get:()=>document.documentElement.dataset.theme||localStorage.getItem(KEY)||'dark',
    set(t){
      document.documentElement.dataset.theme=t;
      localStorage.setItem(KEY,t);
      document.querySelectorAll('[data-theme-ico]').forEach(el=>{
        el.innerHTML=t==='light'?ICONS.moon:ICONS.sun;
      });
    },
    toggle(){this.set(this.get()==='light'?'dark':'light');},
    bind(btn){
      btn.dataset.themeIco='';
      btn.innerHTML=this.get()==='light'?ICONS.moon:ICONS.sun;
      btn.onclick=()=>this.toggle();
    }
  };
  const ICONS={
    sun:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4"/></svg>',
    moon:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>'
  };
  // auto-bind any [data-theme-btn] buttons
  document.addEventListener('DOMContentLoaded',()=>{
    document.querySelectorAll('[data-theme-btn]').forEach(b=>UITheme.bind(b));
  });
})();
