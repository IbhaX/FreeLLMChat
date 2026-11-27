/* Theme management — persisted in localStorage('ui-theme').
   Accent overrides persist as resolved CSS vars in
   localStorage('ui-theme-custom') so the inline <head> snippet can
   re-apply them without math (no FOUC). <html data-theme> is set by
   that snippet too; this module wires toggles, helpers and the
   appearance dialog. */
(function(){
  const KEY='ui-theme';
  const CUSTOM_KEY='ui-theme-custom';
  // Cycle order: dark (ClickHouse) is the default; light stays last.
  const registry=Object.freeze([
    {id:'dark',label:'ClickHouse',scheme:'dark',swatch:{bg:'#0a0a0a',surface:'#1a1a1a',accent:'#faff69'}},
    {id:'stage',label:'Stage',scheme:'dark',swatch:{bg:'#0d0b14',surface:'#1b1730',accent:'#8b7cf6'}},
    {id:'composio',label:'Composio',scheme:'dark',swatch:{bg:'#0f0f0f',surface:'#181818',accent:'#1a26ff'}},
    {id:'apple',label:'Apple',scheme:'light',swatch:{bg:'#ffffff',surface:'#f5f5f7',accent:'#0066cc'}},
    {id:'bmw',label:'BMW',scheme:'light',swatch:{bg:'#ffffff',surface:'#fafafa',accent:'#1c69d4'}},
    {id:'meta',label:'Meta',scheme:'light',swatch:{bg:'#ffffff',surface:'#f0f2f5',accent:'#0064e0'}},
    {id:'elevenlabs',label:'ElevenLabs',scheme:'light',swatch:{bg:'#f5f5f5',surface:'#ffffff',accent:'#292524'}},
    {id:'dji',label:'DJI',scheme:'light',swatch:{bg:'#ffffff',surface:'#f5f5f5',accent:'#0070d5'}},
    {id:'expo',label:'Expo',scheme:'light',swatch:{bg:'#ffffff',surface:'#f0f0f3',accent:'#000000'}},
    {id:'figma',label:'Figma',scheme:'light',swatch:{bg:'#ffffff',surface:'#c7b9ff',accent:'#000000'}},
    {id:'slack',label:'Slack',scheme:'light',swatch:{bg:'#ffffff',surface:'#f4ede4',accent:'#4a154b'}},
    {id:'notion',label:'Notion',scheme:'light',swatch:{bg:'#ffffff',surface:'#f1f1ef',accent:'#7c3aed'}},
    {id:'light',label:'Light',scheme:'light',swatch:{bg:'#f4f5f8',surface:'#ffffff',accent:'#4f46e5'}}
  ].map(t=>Object.freeze({...t,swatch:Object.freeze(t.swatch)})));
  const THEMES=Object.freeze(registry.map(theme=>theme.id));
  const LABELS=Object.freeze(Object.fromEntries(registry.map(theme=>[theme.id,theme.label])));
  // CSS vars an accent override touches — clearing must cover them all.
  const CUSTOM_VARS=Object.freeze(['--accent','--accent-2','--on-accent',
    '--accent-soft','--accent-line','--ring']);
  const initialTheme=document.documentElement.dataset.theme;
  const pageDefault=document.documentElement.dataset.defaultTheme||initialTheme;
  const defaultTheme=THEMES.includes(pageDefault)?pageDefault:'dark';
  if(!THEMES.includes(initialTheme))document.documentElement.dataset.theme=defaultTheme;
  function storedTheme(){
    try{return localStorage.getItem(KEY);}catch(error){return null;}
  }
  function readCustom(){
    try{
      const raw=localStorage.getItem(CUSTOM_KEY);
      const parsed=raw&&JSON.parse(raw);
      return parsed&&typeof parsed.accent==='string'&&parsed.vars?parsed:null;
    }catch(error){return null;}
  }
  function hexToRgb(hex){
    const m=/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex||'').trim());
    if(!m)return null;
    let h=m[1];
    if(h.length===3)h=h.split('').map(c=>c+c).join('');
    return [parseInt(h.slice(0,2),16),parseInt(h.slice(2,4),16),parseInt(h.slice(4,6),16)];
  }
  function toHex(r,g,b){
    return '#'+[r,g,b].map(v=>Math.max(0,Math.min(255,Math.round(v)))
      .toString(16).padStart(2,'0')).join('');
  }
  /* Derived palette for a custom accent: a darker companion, a
     contrast-safe ink, and the translucent soft/line/ring variants the
     themes ship. Returns null for invalid input. */
  function accentVars(hex){
    const rgb=hexToRgb(hex);
    if(!rgb)return null;
    const[r,g,b]=rgb;
    const chan=c=>{c/=255;return c<=0.03928?c/12.92:Math.pow((c+0.055)/1.055,2.4);};
    const lum=0.2126*chan(r)+0.7152*chan(g)+0.0722*chan(b);
    const rgba=a=>`rgba(${r},${g},${b},${a})`;
    return{
      '--accent':toHex(r,g,b),
      '--accent-2':toHex(r*0.82,g*0.82,b*0.82),
      '--on-accent':lum>0.45?'#0a0a0a':'#ffffff',
      '--accent-soft':rgba(0.12),
      '--accent-line':rgba(0.45),
      '--ring':`0 0 0 3px ${rgba(0.25)}`
    };
  }
  let custom=readCustom();
  function applyVars(vars){
    const style=document.documentElement.style;
    if(!style||!style.setProperty)return;
    if(vars)for(const[k,v]of Object.entries(vars))style.setProperty(k,v);
    else for(const k of CUSTOM_VARS)style.removeProperty(k);
  }
  function emitCustom(){
    window.dispatchEvent(new CustomEvent('nova:themecustom',{
      detail:{accent:custom?custom.accent:null}
    }));
  }
  let picker=null;
  /* Appearance dialog: theme swatch grid + accent override.
     Previews live; Apply persists, Cancel restores. */
  function appearance(){
    const dialog=document.createElement('dialog');
    dialog.className='nova-dialog ua-dialog';
    const body=document.createElement('form');
    const heading=document.createElement('h2');
    heading.id='ua-title-'+Date.now();
    heading.textContent='Appearance';
    dialog.setAttribute('aria-labelledby',heading.id);
    body.append(heading);
    const grid=document.createElement('div');
    grid.className='ua-grid';
    grid.setAttribute('role','radiogroup');
    grid.setAttribute('aria-label','Theme');
    const tiles=new Map();
    for(const theme of registry){
      const tile=document.createElement('button');
      tile.type='button';
      tile.className='ua-t';
      tile.dataset.t=theme.id;
      tile.setAttribute('role','radio');
      tile.style.setProperty('--swb',theme.swatch.bg);
      tile.style.setProperty('--sws',theme.swatch.surface);
      tile.style.setProperty('--swa',theme.swatch.accent);
      tile.innerHTML='<span class="ua-pv"><i></i><i></i><i></i></span>'
        +'<span class="ua-nm"></span>';
      tile.querySelector('.ua-nm').textContent=theme.label;
      grid.append(tile);
      tiles.set(theme.id,tile);
    }
    body.append(grid);
    const accentLabel=document.createElement('div');
    accentLabel.className='ua-cap';
    accentLabel.textContent='Accent color';
    const accentRow=document.createElement('div');
    accentRow.className='ua-acc';
    const color=document.createElement('input');
    color.type='color';
    color.setAttribute('aria-label','Custom accent color');
    accentRow.append(color);
    const presets=document.createElement('div');
    presets.className='ua-presets';
    for(const hex of ['#faff69','#8b7cf6','#0066cc','#1a26ff',
      '#22c55e','#eab308','#ef4444','#ec4899']){
      const dot=document.createElement('button');
      dot.type='button';
      dot.className='ua-dot';
      dot.style.background=hex;
      dot.dataset.hex=hex;
      dot.title=hex;
      dot.setAttribute('aria-label','Accent '+hex);
      presets.append(dot);
    }
    accentRow.append(presets);
    const reset=document.createElement('button');
    reset.type='button';
    reset.className='ui-btn ui-btn-ghost ua-reset';
    reset.textContent='Use theme default';
    accentRow.append(reset);
    body.append(accentLabel,accentRow);
    const actions=document.createElement('div');
    actions.className='nova-dialog-actions';
    const cancel=document.createElement('button');
    cancel.type='button';
    cancel.className='ui-btn';
    cancel.textContent='Cancel';
    const submit=document.createElement('button');
    submit.type='submit';
    submit.className='ui-btn ui-btn-primary';
    submit.textContent='Apply';
    actions.append(cancel,submit);
    body.append(actions);
    dialog.append(body);
    return{dialog,body,grid,tiles,color,presets,reset,cancel,submit};
  }
  window.UITheme={
    THEMES, LABELS, registry,
    get:()=>document.documentElement.dataset.theme||storedTheme()||defaultTheme,
    accent:()=>custom?custom.accent:null,
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
        el.title='Theme: '+(LABELS[t]||t)+' — customize appearance';
      });
    },
    setAccent(hex,persist=true){
      const vars=accentVars(hex);
      if(!vars)return false;
      custom={accent:vars['--accent'],vars};
      applyVars(custom.vars);
      if(persist){try{localStorage.setItem(CUSTOM_KEY,JSON.stringify(custom));}catch(error){}}
      emitCustom();
      return true;
    },
    clearAccent(persist=true){
      if(!custom&&!persist)return;
      custom=null;
      applyVars(null);
      if(persist){try{localStorage.removeItem(CUSTOM_KEY);}catch(error){}}
      emitCustom();
    },
    toggle(){
      const i=THEMES.indexOf(this.get());
      this.set(THEMES[(i+1)%THEMES.length]);
    },
    async pick(){
      if(picker)return;
      const self=this;
      if(typeof HTMLDialogElement==='undefined'||!HTMLDialogElement.prototype.showModal){
        this.toggle();return;
      }
      const ui=appearance();
      picker=ui.dialog;
      const origTheme=this.get();
      const origAccent=custom?custom.accent:null;
      const syncTiles=()=>{
        const active=self.get();
        for(const[id,tile]of ui.tiles){
          const on=id===active;
          tile.classList.toggle('sel',on);
          tile.setAttribute('aria-checked',String(on));
        }
      };
      const syncAccent=()=>{
        ui.color.value=custom?custom.accent:(accentVars(
          registry.find(t=>t.id===self.get())?.swatch.accent||'#faff69'
        )||{})['--accent']||'#faff69';
      };
      for(const[id,tile]of ui.tiles)tile.onclick=()=>{self.set(id,false);syncTiles();syncAccent();};
      ui.color.oninput=()=>self.setAccent(ui.color.value,false);
      ui.presets.querySelectorAll('.ua-dot').forEach(d=>
        d.onclick=()=>{self.setAccent(d.dataset.hex,false);ui.color.value=d.dataset.hex;});
      ui.reset.onclick=()=>{self.clearAccent(false);syncAccent();};
      let result=null;
      const dismiss=()=>ui.dialog.close();
      ui.cancel.onclick=dismiss;
      ui.body.onsubmit=event=>{
        event.preventDefault();
        result={theme:self.get(),accent:custom?custom.accent:null};
        ui.dialog.close();
      };
      ui.dialog.addEventListener('keydown',event=>{
        if(event.key!=='Tab')return;
        const focusable=Array.from(ui.dialog.querySelectorAll('button,input'))
          .filter(c=>!c.disabled&&c.getClientRects().length>0);
        const first=focusable[0],last=focusable[focusable.length-1];
        if(event.shiftKey&&document.activeElement===first){
          event.preventDefault();last.focus();
        }else if(!event.shiftKey&&document.activeElement===last){
          event.preventDefault();first.focus();
        }
      });
      ui.dialog.addEventListener('close',()=>{
        window.removeEventListener('pagehide',dismiss);
        ui.dialog.remove();
        if(result){
          if(result.theme!==origTheme)self.set(result.theme);
          else self.set(result.theme,false);
          if(result.accent)self.setAccent(result.accent);
          else self.clearAccent();
        }else{
          self.set(origTheme,false);
          if(origAccent)self.setAccent(origAccent,false);else self.clearAccent(false);
        }
        picker=null;
      },{once:true});
      syncTiles();syncAccent();
      window.addEventListener('pagehide',dismiss,{once:true});
      document.body.append(ui.dialog);
      ui.dialog.showModal();
      if(window.NovaMotion)NovaMotion.dialog(ui.dialog);
      (ui.tiles.get(origTheme)||ui.cancel).focus();
    },
    bind(btn){
      btn.dataset.themeIco='';
      btn.innerHTML=ICONS.palette;
      btn.title='Theme: '+(LABELS[this.get()]||this.get())+' — customize appearance';
      btn.setAttribute('aria-label','Customize appearance');
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
    if(event.key===CUSTOM_KEY||event.key===null){
      let next=null;
      if(event.key===null)next=readCustom();
      else if(event.newValue){
        try{
          const parsed=JSON.parse(event.newValue);
          if(parsed&&typeof parsed.accent==='string'&&parsed.vars)next=parsed;
        }catch(error){}
      }
      const changed=(next?next.accent:null)!==(custom?custom.accent:null);
      custom=next;
      applyVars(custom?custom.vars:null);
      if(changed)emitCustom();
    }
  });
  // auto-bind any [data-theme-btn] buttons; re-apply any saved accent
  applyVars(custom?custom.vars:null);
  document.addEventListener('DOMContentLoaded',()=>{
    document.querySelectorAll('[data-theme-btn]').forEach(b=>UITheme.bind(b));
    // Pages without a declared theme button get the floating one so the
    // appearance picker is reachable everywhere.
    if(!document.querySelector('[data-theme-btn]')){
      const fab=document.createElement('button');
      fab.type='button';
      fab.className='ui-theme-fab';
      fab.setAttribute('data-theme-btn','');
      UITheme.bind(fab);
      document.body.append(fab);
    }
  });
})();

/* Phone install banner (loads the shared kit once the page is ready). */
(function(){
  function add(){ if(document.getElementById('ibInstallJs')) return;
    var s=document.createElement('script'); s.id='ibInstallJs'; s.src='/static/install-banner.js?v=1'; s.defer=true;
    s.dataset.name='iChat'; s.dataset.icon='/static/img/ichat-mark-v4-192.png'; s.dataset.acc='#8b3a3a'; s.dataset.css='/static/install-banner.css?v=1';
    document.body.appendChild(s); }
  if(document.body) add(); else document.addEventListener('DOMContentLoaded', add);
})();
