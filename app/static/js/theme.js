/* Theme management — persisted in localStorage('ui-theme').
   <html data-theme> is set by an inline <head> snippet to avoid FOUC;
   this module only wires toggles and helpers. */
(function(){
  const KEY='ui-theme';
  // Cycle order: dark (ClickHouse) is the default; light stays last.
  const THEMES=['dark','composio','apple','bmw','meta','elevenlabs','light'];
  const LABELS={
    dark:'ClickHouse',composio:'Composio',apple:'Apple',bmw:'BMW',
    meta:'Meta',elevenlabs:'ElevenLabs',light:'Light'
  };
  window.UITheme={
    THEMES, LABELS,
    get:()=>document.documentElement.dataset.theme||localStorage.getItem(KEY)||'dark',
    set(t){
      if(!THEMES.includes(t))t='dark';
      document.documentElement.dataset.theme=t;
      localStorage.setItem(KEY,t);
      document.querySelectorAll('[data-theme-ico]').forEach(el=>{
        el.innerHTML=ICONS.palette;
        el.title='Theme: '+(LABELS[t]||t)+' — click to switch';
      });
    },
    toggle(){
      const i=THEMES.indexOf(this.get());
      this.set(THEMES[(i+1)%THEMES.length]);
    },
    bind(btn){
      btn.dataset.themeIco='';
      btn.innerHTML=ICONS.palette;
      btn.title='Theme: '+(LABELS[this.get()]||this.get())+' — click to switch';
      btn.onclick=()=>this.toggle();
    }
  };
  const ICONS={
    palette:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18c-1.5 0-2-1.2-2-2s.5-1.8.5-3-.8-2-2.5-2H6a3 3 0 0 1 0-6h1.5A2.5 2.5 0 0 0 10 5.5C10 4 11 3 12 3z"/><circle cx="7.5" cy="10.5" r="1" fill="currentColor" stroke="none"/><circle cx="9.5" cy="7" r="1" fill="currentColor" stroke="none"/><circle cx="14.5" cy="7.5" r="1" fill="currentColor" stroke="none"/></svg>',
    sun:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4"/></svg>',
    moon:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>'
  };
  // auto-bind any [data-theme-btn] buttons
  document.addEventListener('DOMContentLoaded',()=>{
    document.querySelectorAll('[data-theme-btn]').forEach(b=>UITheme.bind(b));
  });
})();
