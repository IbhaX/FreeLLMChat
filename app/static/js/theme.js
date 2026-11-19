/* Theme management — persisted in localStorage('ui-theme').
   <html data-theme> is set by an inline <head> snippet to avoid FOUC;
   this module only wires toggles and helpers. */
(function(){
  const KEY='ui-theme';
  // Cycle order: dark (ClickHouse) is the default; light stays last.
  const registry=Object.freeze([
    {id:'dark',label:'ClickHouse',scheme:'dark'},
    {id:'stage',label:'Stage',scheme:'dark'},
    {id:'composio',label:'Composio',scheme:'dark'},
    {id:'apple',label:'Apple',scheme:'light'},
    {id:'bmw',label:'BMW',scheme:'light'},
    {id:'meta',label:'Meta',scheme:'light'},
    {id:'elevenlabs',label:'ElevenLabs',scheme:'light'},
    {id:'light',label:'Light',scheme:'light'}
  ].map(Object.freeze));
  const THEMES=Object.freeze(registry.map(theme=>theme.id));
  const LABELS=Object.freeze(Object.fromEntries(registry.map(theme=>[theme.id,theme.label])));
  const initialTheme=document.documentElement.dataset.theme;
  const pageDefault=document.documentElement.dataset.defaultTheme||initialTheme;
  const defaultTheme=THEMES.includes(pageDefault)?pageDefault:'dark';
  if(!THEMES.includes(initialTheme))document.documentElement.dataset.theme=defaultTheme;
  function storedTheme(){
    try{return localStorage.getItem(KEY);}catch(error){return null;}
  }
  let picker=null;
  window.UITheme={
    THEMES, LABELS, registry,
    get:()=>document.documentElement.dataset.theme||storedTheme()||defaultTheme,
    set(t,persist=true){
      if(!THEMES.includes(t))t=defaultTheme;
      const previous=this.get();
      document.documentElement.dataset.theme=t;
      if(persist){try{localStorage.setItem(KEY,t);}catch(error){}}
      if(previous!==t){
        window.dispatchEvent(new CustomEvent('nova:themechange',{
          detail:{theme:t,previous,scheme:registry.find(theme=>theme.id===t).scheme}
        }));
      }
      document.querySelectorAll('[data-theme-ico]').forEach(el=>{
        el.innerHTML=ICONS.palette;
        el.title='Theme: '+(LABELS[t]||t)+' — choose theme';
      });
    },
    toggle(){
      const i=THEMES.indexOf(this.get());
      this.set(THEMES[(i+1)%THEMES.length]);
    },
    async pick(){
      if(!window.NovaUI?.Dialog){this.toggle();return;}
      if(picker)return;
      picker=NovaUI.Dialog.form({title:'Choose theme',confirmLabel:'Apply theme',fields:[
        {name:'theme',label:'Theme',type:'select',value:this.get(),
          options:registry.map(theme=>({value:theme.id,label:theme.label}))}
      ]});
      try{
        const result=await picker;
        if(result)this.set(result.theme);
      }finally{picker=null;}
    },
    bind(btn){
      btn.dataset.themeIco='';
      btn.innerHTML=ICONS.palette;
      btn.title='Theme: '+(LABELS[this.get()]||this.get())+' — choose theme';
      btn.setAttribute('aria-label','Choose theme');
      btn.setAttribute('aria-haspopup','dialog');
      btn.onclick=()=>this.pick(btn);
    }
  };
  const ICONS={
    palette:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18c-1.5 0-2-1.2-2-2s.5-1.8.5-3-.8-2-2.5-2H6a3 3 0 0 1 0-6h1.5A2.5 2.5 0 0 0 10 5.5C10 4 11 3 12 3z"/><circle cx="7.5" cy="10.5" r="1" fill="currentColor" stroke="none"/><circle cx="9.5" cy="7" r="1" fill="currentColor" stroke="none"/><circle cx="14.5" cy="7.5" r="1" fill="currentColor" stroke="none"/></svg>',
    sun:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4"/></svg>',
    moon:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>'
  };
  window.addEventListener('storage',event=>{
    if(event.key===KEY||event.key===null){
      UITheme.set(THEMES.includes(event.newValue)?event.newValue:defaultTheme,false);
    }
  });
  // auto-bind any [data-theme-btn] buttons
  document.addEventListener('DOMContentLoaded',()=>{
    document.querySelectorAll('[data-theme-btn]').forEach(b=>UITheme.bind(b));
  });
})();
