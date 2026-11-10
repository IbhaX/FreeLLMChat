/* Chat page logic — conversations, streaming, models, agents. */
(function(){
// --- Models ---------------------------------------------------------------
const MODELS=[
  {id:'auto',name:'auto',desc:'Gateway routes to any healthy provider',color:'linear-gradient(135deg,#faff69,#e6eb52)',tag:'Au',text:'#0a0a0a'},
];
const PALETTE=['#6366f1','#ef5b6e','#34d399','#f0a04b','#8b5cf6','#059669','#e5534b','#5e6ad2'];
async function loadModels(silent){
  try{
    const r=await fetch(API_BASE+'/v1/models',{headers:apiHeaders()});
    if(r.status===401){
      localStorage.removeItem('gateway_key');API_KEY='';
      if(askGatewayKey())return loadModels();
    }
    if(!r.ok)throw new Error('HTTP '+r.status);
    const data=(await r.json()).data||[];
    if(data.length){
      MODELS.length=0;
      data.forEach((m,i)=>{
        const c=PALETTE[i%PALETTE.length];
        MODELS.push({id:m.id,name:m.id,desc:m.owned_by||'',
          color:`linear-gradient(135deg,${c},${PALETTE[(i+3)%PALETTE.length]})`,
          tag:(m.owned_by||m.id).slice(0,2)});
      });
      const prevId=state.model&&state.model.id;
      state.model=MODELS.find(m=>m.id===prevId)||MODELS.find(m=>m.id==='auto')||MODELS[0];
      $('#modelName').textContent=state.model.name;
      $('#modelIcon').style.background=state.model.color;
      renderModels();
      if(!silent)toast(`Connected — ${data.length} models`,'ok');
    }
  }catch(e){toast('Gateway unreachable — check server','err');}
}

// --- State ----------------------------------------------------------------
const state={model:MODELS[0],conversations:[],current:null,streaming:false,abort:null,agent:null,group:null,groupMode:false,researchMode:false,researchDepth:'auto',responseLength:localStorage.getItem('nova_response_length')||'medium'};
try{state.rpChar=JSON.parse(localStorage.getItem('nova_rp_char')||'null')||null;}catch(_){state.rpChar=null;}
window.__chatState=state; // wizard.js reads agent
const RESPONSE_LENGTHS={
  short:'SHORT: Be concise and focused, normally 1–3 sentences. Keep only the essential answer.',
  medium:'MEDIUM: Give a normal, complete answer of about 2 paragraphs. Include useful context without overexplaining.',
  large:'LARGE: Give a detailed answer of 5–8 substantive paragraphs. Explain the important context, reasoning, examples and caveats without repetition.'
};
function responseLengthInstruction(){
  return 'RESPONSE LENGTH: '+RESPONSE_LENGTHS[state.responseLength||'medium']+
    '\nRICH RESPONSE: Return one valid nova-response fenced JSON envelope with a direct answer in content and at least one relevant component from the supported component catalog. Never return plain text/Markdown alone. Preserve the requested voice, role and task.';
}
function initResponseLength(){
  const el=$('#responseLength');if(!el)return;
  if(!RESPONSE_LENGTHS[state.responseLength])state.responseLength='medium';
  el.value=state.responseLength;
  el.addEventListener('change',()=>{
    state.responseLength=RESPONSE_LENGTHS[el.value]?el.value:'medium';
    localStorage.setItem('nova_response_length',state.responseLength);
  });
}
const STORE_KEY='nova_chat_state_v1';
function saveState(){
  try{
    const cur=state.current?state.current.id:null;
    localStorage.setItem(STORE_KEY,JSON.stringify({
      modelId:state.model.id,conversations:state.conversations,currentId:cur}));
  }catch(_){}
  syncToServer();
}

// --- Server persistence (best-effort; localStorage is the source of truth) --
let _syncT=null;
function syncToServer(){
  clearTimeout(_syncT);
  _syncT=setTimeout(async()=>{
    if(!ADMIN_TOKEN)return; // admin token needed for /admin/conversations
    for(const c of state.conversations){
      try{
        await fetch(API_BASE+'/admin/conversations/'+c.id,{
          method:'PUT',headers:apiHeaders(true),
          body:JSON.stringify({name:c.name,messages:c.msgs})});
      }catch(_){/* offline */}
    }
  },800);
}
async function fetchServerConversations(){
  if(!ADMIN_TOKEN)return;
  try{
    const r=await fetch(API_BASE+'/admin/conversations',{headers:apiHeaders(true)});
    if(!r.ok)return;
    const remote=await r.json();
    const localIds=new Set(state.conversations.map(c=>c.id));
    for(const rc of remote){
      if(localIds.has(rc.client_id))continue;
      try{
        const fr=await fetch(API_BASE+'/admin/conversations/'+rc.client_id,{headers:apiHeaders(true)});
        if(!fr.ok)continue;
        const d=await fr.json();
        if(d.messages&&d.messages.length){
          state.conversations.push({id:d.client_id,name:d.name||'Chat',
            msgs:d.messages.map(m=>({role:m.role,text:m.text||'',html:m.html||'',t:m.t||Date.now(),model:m.model,agentName:m.agentName,person:m.person||null})),
            t:Date.now()});
        }
      }catch(_){}
    }
    if(!state.current&&state.conversations.length)state.current=state.conversations[0];
  }catch(_){}
}
function loadState(){
  try{
    const raw=localStorage.getItem(STORE_KEY);if(!raw)return false;
    const s=JSON.parse(raw);
    if(!s.conversations||!s.conversations.length)return false;
    state.conversations=s.conversations;
    // Always open a fresh chat on load; history stays in the sidebar.
    const fresh=mkConv('New conversation');
    state.conversations.unshift(fresh);
    state.current=fresh;
    const m=MODELS.find(x=>x.id===s.modelId);
    if(m){state.model=m;$('#modelName').textContent=m.name;$('#modelIcon').style.background=m.color;}
    return true;
  }catch(_){return false;}
}
function mkConv(name){return{id:Math.random().toString(36).slice(2,9),name,msgs:[],t:Date.now()};}
function seed(){
  const c=mkConv('Welcome chat');
  c.msgs.push({role:'assistant',t:Date.now(),
    text:"Welcome to **Nova Chat** — powered by your FreeLLM Gateway.\n\nTry:\n- Asking me anything (I stream replies)\n- Typing `/` for commands\n- Creating an agent with **＋** up top\n- Switching models in the top bar"});
  state.conversations.push(c);state.current=c;
}

// --- Markdown + rich blocks ------------------------------------------------
function mdTable(rows){
  const parse=l=>l.trim().replace(/^\||\|$/g,'').split('|').map(c=>c.trim());
  const head=parse(rows[0]);
  const body=rows.slice(2).map(parse).filter(r=>r.length&&r.some(c=>c));
  return `<div style="overflow-x:auto;margin:10px 0"><table class="ui-table" style="font-size:12.5px"><thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${body.map(r=>`<tr>${head.map((_,i)=>`<td>${r[i]||''}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function richBlock(lang,code){
  const txt=code.trim();
  try{
    const j=JSON.parse(txt);
    if(lang==='nova-response'&&j&&typeof j==='object'&&j.response_type){
      // Prefer the full Nova component registry; never show a valid envelope
      // as a raw code block when this legacy Markdown path is used.
      if(window.NovaRender&&NovaRender.ResponseRenderer){
        const host=document.createElement('div');
        NovaRender.ResponseRenderer.renderEnvelope({envelope:j,prose:j.content||''},host,{});
        return host.innerHTML;
      }
      // Safe minimal fallback for deployments where the component engine
      // failed to load: preserve readable content and component summaries.
      const body=esc(j.content||'');
      const cards=(j.components||[]).map(comp=>{
        const d=comp.data||{};
        const message=esc(d.message||d.text||d.title||JSON.stringify(d));
        return '<div class="ui-card ui-card-pad" style="margin:10px 0"><b>'+esc(comp.type||'Component')+'</b><div>'+message+'</div></div>';
      }).join('');
      return (body?'<p>'+body+'</p>':'')+cards;
    }
    const items=Array.isArray(j)?j:(j.items||j.cards||null);
    if(lang==='carousel'&&items){
      const cards=items.map((it,i)=>`
        <div class="ui-card ui-card-pad" style="min-width:200px;flex:none;scroll-snap-align:start">
          ${it.icon?`<div style="font-size:20px;margin-bottom:6px">${it.icon}</div>`:''}
          <b style="font-size:13px;display:block;margin-bottom:4px">${esc(it.title||'')}</b>
          <div class="t-xs tx-2">${esc(it.body||it.description||'')}</div>
          ${it.badge?`<span class="pill info" style="margin-top:6px">${esc(it.badge)}</span>`:''}
        </div>`).join('');
      return `<div class="rich-wrap"><div class="codehead"><span>carousel · ${items.length} items</span><span><button class="car-btn" data-d="-1">‹</button><button class="car-btn" data-d="1">›</button></span></div>
        <div class="carousel" style="display:flex;gap:10px;overflow-x:auto;scroll-snap-type:x mandatory;padding:10px 2px">${cards}</div></div>`;
    }
    if((lang==='cards'||lang==='json')&&items){
      return `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:10px 0">`+
        items.map(it=>`<div class="ui-card ui-card-pad">${it.icon?`<div style="font-size:18px;margin-bottom:5px">${it.icon}</div>`:''}<b style="font-size:12.5px;display:block;margin-bottom:3px">${esc(it.title||'')}</b><div class="t-xs tx-2">${esc(it.body||it.description||'')}</div>${it.badge?`<span class="pill info" style="margin-top:6px">${esc(it.badge)}</span>`:''}</div>`).join('')+`</div>`;
    }
    if(lang==='table'&&items){
      const keys=[...new Set(items.flatMap(o=>Object.keys(o)))];
      return `<div style="overflow-x:auto;margin:10px 0"><table class="ui-table" style="font-size:12.5px"><thead><tr>${keys.map(k=>`<th>${esc(k)}</th>`).join('')}</tr></thead><tbody>${items.map(it=>`<tr>${keys.map(k=>`<td>${esc(it[k]??'')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    }
  }catch(_){}
  if(lang==='table'){ // markdown table text
    const rows=txt.split('\n').filter(l=>l.includes('|'));
    if(rows.length>=2&&/^[\s|:-]+$/.test(rows[1]))return mdTable(rows);
  }
  return null;
}
function md(src){
  // Normalize line-wrapped protocol tags before either Markdown parser sees them.
  src=String(src||'').replace(/```nova[ \\t]*\\r?\\n[ \\t]*-response\\b/gi,'```nova-response');
  // Rich path: marked + DOMPurify when CDN loaded; custom fallback otherwise.
  if(window.marked&&window.DOMPurify){
    try{
      if(window.hljs&&!marked.__hl){marked.setOptions({highlight:(c,l)=>
        window.hljs.getLanguage(l)?window.hljs.highlight(c,{language:l}).value:window.hljs.highlightAuto(c).value});
        marked.__hl=true;}
      const rich=[];
      let pre=src.replace(/```(\w*)\n?([\s\S]*?)```/g,(m,lang,code)=>{
        const r=richBlock(lang,code);
        if(r){rich.push(r);return ` ${rich.length-1} `;}
        return m;
      });
      let html=DOMPurify.sanitize(marked.parse(pre,{breaks:true}),{USE_PROFILES:{html:true}});
      html=html.replace(/ (\d+) /g,(m,i)=>rich[+i]);
      return html;
    }catch(_){/* fall through to custom */}
  }
  let html=esc(src);
  // fenced blocks: rich renderers first, else code
  html=html.replace(/```(\w*)\n?([\s\S]*?)```/g,(m,lang,code)=>{
    const rich=richBlock(lang,code);
    if(rich)return rich;
    return `<div class="codeblock"><div class="codehead"><span>${lang||'code'}</span><button class="copy-code">copy</button></div><pre><code>${code.trimEnd()}</code></pre></div>`;
  });
  // inline markdown tables | a | b |\n|---|---|
  html=html.replace(/((?:^\|[^\n]+\|\s*$\n?){2,})/gm,block=>{
    const rows=block.trim().split('\n');
    if(rows.length>=2&&/^\|[\s|:-]+\|$/.test(rows[1]))return mdTable(rows);
    return block;
  });
  html=html.replace(/`([^`\n]+)`/g,'<code class="ic">$1</code>');
  html=html.replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>');
  html=html.replace(/_([^_\n]+)_/g,'<i>$1</i>');
  html=html.replace(/(?:^|\n)- ([^\n]+)/g,'\n<ul><li>$1</li></ul>');
  html=html.replace(/(?:^|\n)(\d+)\. ([^\n]+)/g,'\n<ul><li>$1. $2</li></ul>');
  html=html.replace(/<\/ul>\n<ul>/g,'');
  html=html.split(/\n{2,}/).map(p=>{
    const t=p.trim();
    return /^<div class="codeblock"|^<ul>/.test(t)?t:`<p>${p.replace(/\n/g,'<br>')}</p>`;
  }).join('');
  return html;
}
const timeStr=t=>new Date(t).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
function errText(e){
  let s=typeof e==='string'?e:(e.message||JSON.stringify(e));
  try{const j=JSON.parse(s);return j.message||j.error?.message||s;}catch(_){return s.slice(0,300);}
}
function reasoningEl(r){
  return `<details class="ui-acc" style="margin-bottom:8px;opacity:.8"><summary class="t-xs tx-3" style="cursor:pointer">🧠 Reasoning</summary><div class="acc-body t-xs tx-2" style="padding-top:6px;white-space:pre-wrap">${esc(r.slice(0,3000))}${r.length>3000?'…':''}</div></details>`;
}

// --- Rendering -------------------------------------------------------------
function renderSidebar(filter=''){
  const list=$('#convoList');list.innerHTML='';
  const convs=state.conversations.filter(c=>!filter||c.name.toLowerCase().includes(filter));
  if(!convs.length){list.innerHTML='<div class="conv-label">No chats</div>';return;}
  list.innerHTML='<div class="conv-label">Chats</div>';
  for(const c of convs){
    const el=document.createElement('div');
    el.className='conv'+(c===state.current?' active':'');
    const last=c.msgs[c.msgs.length-1];
    el.innerHTML=`<div class="conv-dot"></div>
      <div style="min-width:0;flex:1">
        <div class="conv-name">${esc(c.name)}</div>
        <div class="conv-prev">${esc(last?last.text.slice(0,60):'Empty')}</div>
      </div>
      <span class="conv-time">${timeStr(c.t)}</span>
      <button class="conv-x" title="Delete">✕</button>`;
    el.querySelector('.conv-x').onclick=e=>{e.stopPropagation();delConv(c);};
    el.onclick=()=>{state.current=c;renderAll();if(innerWidth<1024)closeSidebar();};
    list.appendChild(el);
  }
}
function delConv(c){
  state.conversations=state.conversations.filter(x=>x!==c);
  if(ADMIN_TOKEN)fetch(API_BASE+'/admin/conversations/'+c.id,{method:'DELETE',headers:apiHeaders(true)}).catch(()=>{});
  if(state.current===c)state.current=state.conversations[0]||null;
  if(!state.conversations.length){const n=mkConv('New conversation');state.conversations.push(n);state.current=n;}
  renderAll();
}
function renderChat(){
  const inner=$('#chatInner');inner.innerHTML='';
  const c=state.current;
  $('#chatName').textContent=c.name;
  if(!c.msgs.length){inner.appendChild(heroEl());return;}
  for(const m of c.msgs)inner.appendChild(msgEl(m));
  scrollBottom(false);
}
function heroEl(){
  const d=document.createElement('div');
  const agentNote=state.agent?`<p style="color:var(--accent)">Chatting as agent <b>${esc(state.agent.name)}</b></p>`:'';
  d.innerHTML=`<div class="hero"><div class="logo">N</div>
    <h1>How can I help today?</h1>
    <p>Streaming replies · markdown · code blocks · slash commands · attachments</p>
    ${agentNote}
    <div class="hero-cards">
      <div class="ui-card ui-card-pad ui-card-hover" data-q="Explain how streaming APIs work" style="cursor:pointer;text-align:left"><b class="t-sm">Explain a concept</b><div class="t-xs tx-2" style="margin-top:4px">How do streaming APIs work under the hood?</div></div>
      <div class="ui-card ui-card-pad ui-card-hover" data-q="Write a python function to chunk text" style="cursor:pointer;text-align:left"><b class="t-sm">Write some code</b><div class="t-xs tx-2" style="margin-top:4px">A Python function to chunk text for embeddings</div></div>
      <div class="ui-card ui-card-pad ui-card-hover" data-q="Design a rate limiter for my API" style="cursor:pointer;text-align:left"><b class="t-sm">Design a system</b><div class="t-xs tx-2" style="margin-top:4px">Sketch a rate limiter for a public API</div></div>
      <div class="ui-card ui-card-pad ui-card-hover" data-q="Compare REST vs gRPC" style="cursor:pointer;text-align:left"><b class="t-sm">Compare options</b><div class="t-xs tx-2" style="margin-top:4px">REST vs gRPC — tradeoffs and when to pick each</div></div>
    </div></div>`;
  d.querySelectorAll('[data-q]').forEach(el=>el.onclick=()=>{$('#input').value=el.dataset.q;send();});
  return d;
}
// --- Person cards ---------------------------------------------------------
function appendPersonCard(bubble,person){
  try{
    bubble.querySelectorAll('.nova-person').forEach(n=>n.closest('.nova-comp-slot')?.remove()||n.remove());
    const slot=document.createElement('div');
    bubble.insertBefore(slot,bubble.querySelector('.nova-msg-actions')||null);
    NovaRender.ResponseRenderer.renderComponent({type:'person_card',data:person},slot);
  }catch(_){}
}
function personQueryName(text){
  const t=String(text||'').trim();
  if(t.length>140)return null;
  const m=t.match(/^(?:who\s+(?:is|was|are|were)\b|who's\b|whos\b|tell me about\b|what do you know about\b|introduce\s+me\s+to\b)\s+(.+)$/i);
  if(!m)return null;
  let name=m[1].trim().replace(/[?.!]+$/,'').replace(/^the\s+/i,'');
  name=name.split(/\s+(?:and|who|what|when|where|why|how|because)\b/i)[0].trim();
  if(name.split(/\s+/).length>5)return null;
  if(/\b(?:of|the|a|an|in|on|for|to|and|or|is|was|are|were|capital|city|country|meaning|definition|difference|between)\b/i.test(name))return null;
  if(!/[A-Za-z]/.test(name)||name.length<3||name.length>80)return null;
  return name;
}
function maybeAttachPerson(c,msg,bubble){
  if(state.rpChar||state.researchMode||msg.failed)return;
  if(!msg.text||msg.text==='_(empty response)_')return;
  const users=c.msgs.filter(m=>m.role==='user'&&m.text);
  const last=users[users.length-1];
  if(!last)return;
  const name=personQueryName(last.text);
  if(!name)return;
  fetch(API_BASE+'/v1/person/lookup?name='+encodeURIComponent(name),{headers:apiHeaders()})
    .then(r=>r.ok?r.json():null)
    .then(d=>{
      if(!d||!d.found||!d.person)return;
      if(state.current!==c)return;
      msg.person=d.person;
      if(window.NovaRender&&NovaRender.ResponseRenderer){
        NovaRender.ResponseRenderer.renderMessage(msg,bubble,{showActions:true});
        appendPersonCard(bubble,msg.person);
        msg.html=bubble.innerHTML;
      }
      saveState();
    })
    .catch(()=>{});
}
function msgEl(m){
  const row=document.createElement('div');
  row.className='msg-row '+(m.role==='user'?'row-user':'row-ai');
  const name=m.role==='user'?'You':(m.agentName||state.agent?.name||'Nova');
  row.innerHTML=`<div class="msg-av ${m.role==='user'?'me':'ai'}">${m.role==='user'?'A':((m.agentName||state.agent)?'🤖':'N')}</div>
    <div class="msg-col">
      <div class="msg-meta"><b>${esc(name)}</b><time>${timeStr(m.t)}</time>${m.model?`<time>· ${esc(m.model)}</time>`:''}</div>
      ${m.reasoning?reasoningEl(m.reasoning):''}
      <div class="bubble"></div>
      ${m.regen?`<div class="regen-note">Regenerated ×${m.regen}</div>`:''}
    </div>`;
  const bubble=row.querySelector('.bubble');
  // Use the Nova Render Engine when available; fall back to legacy md().
  if(window.NovaRender&&NovaRender.ResponseRenderer){
    NovaRender.ResponseRenderer.renderMessage(m,bubble,{showActions:true});
    if(m.person)appendPersonCard(bubble,m.person);
    // Wire Nova action events to existing chat handlers.
    bubble.addEventListener('nova:action',e=>{
      const{action,message}=e.detail;
      if(action==='regen'||action==='retry')regenerate(m,row);
      else if(action==='edit')editUserMessage(m);
    });
    bubble.addEventListener('nova:suggest',e=>{
      $('#input').value=e.detail.prompt;send();
    });
    bubble.addEventListener('nova:interactive',e=>{
      const{action,values}=e.detail;
      const summary=Object.entries(values).map(([k,v])=>`${k}: ${v}`).join(', ');
      $('#input').value=`[${action}] ${summary}`;send();
    });
  }else{
    bubble.innerHTML=m.html||md(m.text);
    const acts=document.createElement('div');
    acts.className='msg-actions';
    acts.innerHTML=`<button data-a="copy">⧉ copy</button>
      ${m.role==='user'?'<button data-a="edit">✏️ edit</button>':''}
      <button data-a="like">👍</button>
      <button data-a="dislike">👎</button>
      ${m.role==='assistant'?'<button data-a="regen">↻ regenerate</button>':''}
      ${m.failed?'<button data-a="retry" style="color:var(--error)">↻ retry</button>':''}`;
    bubble.append(acts);
    acts.querySelector('[data-a=copy]').onclick=()=>{navigator.clipboard.writeText(m.text);toast('Copied');};
    const ed=acts.querySelector('[data-a=edit]');
    if(ed)ed.onclick=()=>editUserMessage(m);
    for(const a of ['like','dislike'])
      acts.querySelector(`[data-a=${a}]`).onclick=e=>e.currentTarget.classList.toggle('reacted');
    const rg=acts.querySelector('[data-a=regen]');
    if(rg)rg.onclick=()=>regenerate(m,row);
    const rt=acts.querySelector('[data-a=retry]');
    if(rt)rt.onclick=()=>regenerate(m,row);
  }
  return row;
}
function scrollBottom(smooth=true){
  $('#chatScroll').scrollTo({top:$('#chatScroll').scrollHeight,behavior:smooth?'smooth':'auto'});
}
function renderAll(){renderSidebar($('#convSearch').value.toLowerCase());renderChat();saveState();}

// --- Send / stream -----------------------------------------------------------
async function send(){
  const q=$('#input').value.trim();
  if(!q||state.streaming)return;
  const c=state.current;
  if(c.msgs.length===0)c.name=q.slice(0,34)+(q.length>34?'…':'');
  const chips=[...$('#chipRow').children];
  const atts=chips.map(x=>x.dataset.name);
  const imgs=chips.map(x=>x._file).filter(f=>f&&f.type.startsWith('image/'));
  const txts=chips.map(x=>x._file).filter(f=>f&&!f.type.startsWith('image/'));
  let text=q;
  if(atts.length)text+=`\n\n📎 ${atts.join(', ')}`;
  // Inline readable file contents (text-like, < 200KB each, max 3 files).
  for(const f of txts.slice(0,3)){
    if(f.size>200*1024)continue;
    try{
      const body=await f.text();
      text+=`\n\n--- file: ${f.name} ---\n${body.slice(0,20000)}${body.length>20000?'\n…(truncated)':''}`;
    }catch(_){}
  }
  const userMsg={role:'user',text,t:Date.now()};
  if(imgs.length){
    try{
      userMsg.images=await Promise.all(imgs.map(fileToDataURL));
    }catch(_){/* keep text only */}
  }
  c.msgs.push(userMsg);
  $('#input').value='';autogrow();$('#chipRow').innerHTML='';$('#slashMenu').classList.remove('open');
  if(q.startsWith('/')){handleSlash(q);renderAll();return;}
  renderAll();
  if(state.researchMode&&!state.groupMode){streamResearchReply(c);return;}
  if(state.groupMode&&state.group&&state.group.agents.length){streamGroupReply(c);return;}
  streamAPIReply(c);
}
function handleSlash(q){
  const c=state.current;
  if(q==='/clear'){c.msgs=[];toast('Chat cleared');return;}
  if(q==='/summary'){
    c.msgs.push({role:'assistant',t:Date.now(),
      text:`This chat has ${c.msgs.length} messages. Recent topics: ${c.msgs.slice(-4).map(m=>m.text.split(' ').slice(0,5).join(' ')).join(' · ')||'none yet'}.`});
    return;
  }
  if(q.startsWith('/remember')){
    const fact=q.slice(9).trim();
    if(!fact){c.msgs.push({role:'assistant',t:Date.now(),text:'Usage: `/remember <fact>`'});return;}
    adminFetch('/admin/memories',{method:'POST',body:JSON.stringify({content:fact})})
      .then(()=>{c.msgs.push({role:'assistant',t:Date.now(),text:`🧠 Saved to memory: "${fact}"`});renderAll();})
      .catch(()=>toast('Memory save failed','err'));
    return;
  }
  if(q==='/memories'){
    adminFetch('/admin/memories').then(async r=>{
      const list=await r.json();
      const text=list.length
        ?'🧠 **Saved memories:**\n'+list.map(m=>`- [${m.id}] ${m.content}`).join('\n')
        :'No memories saved yet. Use `/remember <fact>`.';
      c.msgs.push({role:'assistant',t:Date.now(),text});renderAll();
    });
    return;
  }
  if(q.startsWith('/forget')){
    const id=q.slice(7).trim();
    if(!id){c.msgs.push({role:'assistant',t:Date.now(),text:'Usage: `/forget <id>` — see `/memories`'});return;}
    adminFetch('/admin/memories/'+id,{method:'DELETE'})
      .then(()=>{c.msgs.push({role:'assistant',t:Date.now(),text:`Memory ${id} deleted.`});renderAll();})
      .catch(()=>toast('Delete failed','err'));
    return;
  }
  if(q==='/export'){
    const blob=new Blob([c.msgs.map(m=>`[${m.role}] ${m.text}`).join('\n\n---\n\n')],{type:'text/markdown'});
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='chat.md';a.click();
    return;
  }
  c.msgs.push({role:'assistant',t:Date.now(),text:"Unknown command. Try `/summary`, `/export`, or `/clear`."});
}
async function streamAPIReply(c){
  state.streaming=true;setSend(true);
  const inner=$('#chatInner');
  const scroller=$('#chatScroll');
  const trow=document.createElement('div');
  trow.className='msg-row row-ai';
  trow.innerHTML=`<div class="msg-av ai">${state.agent?'🤖':'N'}</div><div class="msg-col"><div class="typing"><i></i><i></i><i></i></div><details class="act-steps" open><summary><span class="act-ico">⚡</span><span class="act-lbl">Agent activity</span></summary><div class="act-list"></div></details></div>`;
  inner.appendChild(trow);scrollBottom();
  const actList=trow.querySelector('.act-list'),actLbl=trow.querySelector('.act-lbl'),t0=Date.now();
  const acts=[];
  const addAct=text=>{acts.push(text);actLbl.textContent=`${acts.length} step${acts.length!==1?'s':''}…`;
    const it=document.createElement('div');it.className='act-item';
    it.innerHTML=`<span class="act-dot"></span><span>${esc(text)}</span>`;
    actList.appendChild(it);scrollBottom();};
  addAct('routing request…');
  const actTimer=setInterval(()=>{if(acts.length===1)actLbl.textContent=`routing · ${Math.round((Date.now()-t0)/1000)}s`;},500);
  const ctrl=new AbortController();
  const ab={get stop(){return ctrl.signal.aborted},set stop(v){if(v)ctrl.abort()}};
  state.abort=ab;
  const finish=(msg,bubble)=>{
    // Use the render engine for the final structured render, keep html cached.
    if(window.NovaRender&&NovaRender.ResponseRenderer){
      NovaRender.ResponseRenderer.renderMessage(msg,bubble,{showActions:true});
      msg.html=bubble.innerHTML;
    }else{
      msg.html=md(msg.text);bubble.innerHTML=msg.html;
    }
    state.streaming=false;state.abort=null;setSend(false);
    renderSidebar($('#convSearch').value.toLowerCase());saveState();
  };
  const fail=(note)=>{
    trow.remove();
    const text=`_${note}_`;
    const msg={role:'assistant',text,html:md(text),t:Date.now(),model:state.model.name,agentName:state.agent?.name,failed:true};
    c.msgs.push(msg);
    renderAll();
    state.streaming=false;state.abort=null;setSend(false);
  };
  const messages=c.msgs
    .filter(m=>m.role==='user'||m.role==='assistant')
    .map(m=>{
      if(m.role==='user'&&m.images&&m.images.length){
        return {role:'user',content:[
          {type:'text',text:m.text},
          ...m.images.map(url=>({type:'image_url',image_url:{url}})),
        ]};
      }
      return {role:m.role,content:m.text};
    });
  const isAgent=!!state.agent;
  if(state.rpChar&&!isAgent)messages.unshift({role:'system',content:rpPersona(state.rpChar)});
  const url=isAgent?API_BASE+'/v1/agents/'+state.agent.id+'/chat':API_BASE+'/v1/chat/completions';
  const lastContent=messages[messages.length-1].content;
  const lengthInstruction=responseLengthInstruction();
  const payload=isAgent
    ?{message:lastContent+'\n\n'+lengthInstruction,history:messages.slice(0,-1),stream:true}
    :{model:state.model.id,messages:[{role:'system',content:lengthInstruction},...messages],stream:true,builtin_tools:['*'],conversation_id:c.id};
  const doFetch=()=>fetch(url,{method:'POST',headers:apiHeaders(),signal:ctrl.signal,body:JSON.stringify(payload)});
  try{
    let resp=await doFetch();
    if(resp.status===401){
      localStorage.removeItem('gateway_key');API_KEY='';
      if(askGatewayKey())resp=await doFetch();
    }
    if(!resp.ok||!resp.body){
      const errText=await resp.text().catch(()=>'');
      throw new Error(`HTTP ${resp.status} ${errText.slice(0,200)}`);
    }
    clearInterval(actTimer);
    const msg={role:'assistant',text:'',html:'',t:Date.now(),model:state.model.name,agentName:state.agent?.name||(state.rpChar?state.rpChar.name:null)};
    c.msgs.push(msg);
    // Row is created lazily on the first token so the typing placeholder
    // is the ONLY avatar row while we wait for the provider.
    let row=null,bubble=null,sr=null;
    const reader=resp.body.getReader(),dec=new TextDecoder();
    let acc='',buf='',reasoning='',firstTok=false;
    while(true){
      const{done,value}=await reader.read();
      if(done)break;
      buf+=dec.decode(value,{stream:true});
      const parts=buf.split('\n\n');buf=parts.pop();
      for(const part of parts){
        const line=part.trim();
        if(!line.startsWith('data:'))continue;
        const data=line.slice(5).trim();
        if(data==='[DONE]')continue;
        try{
          const j=JSON.parse(data);
          if(j.freellm){const ev=j.freellm;
            if(ev.event==='replace_content'){acc=ev.content||'';msg.text=collapseRepeats(acc);
              if(sr)sr.setText(acc);else if(bubble){bubble.innerHTML=md(acc)+'<span class="cursor"></span>';scrollBottom();}
              continue;}
            addAct(ev.text||ev.event);continue;}
          if(j.error){acc+=`\n\n_${errText(j.error)}_`;continue;}
          const delta=j.choices?.[0]?.delta||{};
          if(delta.reasoning||delta.reasoning_content)reasoning+=(delta.reasoning||delta.reasoning_content);
          if(delta.content)acc+=delta.content;
        }catch(_){}
      }
      if(!firstTok){firstTok=true;
        row=msgEl(msg);inner.appendChild(row);
        bubble=row.querySelector('.bubble');
        sr=(window.NovaRender&&NovaRender.StreamingRenderer)
          ?NovaRender.StreamingRenderer.create(bubble,scroller):null;
        const steps=trow.querySelector('.act-steps');
        if(acts.length){
          steps.removeAttribute('open');
          actLbl.textContent=`⚡ ${acts.length} step${acts.length!==1?'s':''} — click to expand`;
          steps.querySelectorAll('.act-item').forEach(i=>i.classList.add('done'));
          row.querySelector('.msg-col').prepend(steps);
        }
        trow.remove();}
      if(sr){sr.setText(acc);}else if(bubble){bubble.innerHTML=md(acc)+'<span class="cursor"></span>';scrollBottom();}
    }
    msg.text=collapseRepeats(acc)||'_(empty response)_';
    msg.reasoning=reasoning||null;
    if(!row){row=msgEl(msg);inner.appendChild(row);bubble=row.querySelector('.bubble');trow.remove();}
    if(sr){sr.finish(msg.text);msg.html=bubble.innerHTML;}
    finish(msg,bubble);
    maybeAttachPerson(c,msg,bubble);
  }catch(e){
    clearInterval(actTimer);
    if(ctrl.signal.aborted)fail('⏹ Stopped by user.');
    else fail(`⚠ ${e.message}`);
  }
}
function editUserMessage(m){
  const c=state.current;
  const idx=c.msgs.indexOf(m);
  if(idx<0||state.streaming)return;
  const text=m.text;
  c.msgs.splice(idx); // rollback: drop this message and everything after
  $('#input').value=text;autogrow();ta.focus();
  renderAll();
  toast('Edit and resend — history rolled back');
}
function regenerate(m,row){
  if(state.streaming)return;
  const c=state.current;
  const idx=c.msgs.indexOf(m);
  if(idx>=0)c.msgs.splice(idx,1);
  row.remove();
  streamAPIReply(c);
}
function setSend(streaming){
  const b=$('#sendBtn');
  b.classList.toggle('stop',streaming);
  b.title=streaming?'Stop':'Send';
  b.innerHTML=streaming
    ?'<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>'
    :'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
}

// --- Slash menu --------------------------------------------------------------
const SLASH=[
  {cmd:'/summary',desc:'Summarize this conversation'},
  {cmd:'/export',desc:'Download the chat as Markdown'},
  {cmd:'/remember',desc:'Save a fact to long-term memory (e.g. /remember I prefer Python)'},
  {cmd:'/memories',desc:'List saved memories'},
  {cmd:'/forget',desc:'Delete a memory by id (e.g. /forget 3)'},
  {cmd:'/clear',desc:'Clear all messages'},
];
async function adminFetch(path,opts={}){
  let r=await fetch(API_BASE+path,{...opts,headers:apiHeaders(true)});
  if(r.status===401){
    const t=prompt('Admin token required (ADMIN_TOKEN):');
    if(t){ADMIN_TOKEN=t;sessionStorage.setItem('admin_token',t);
      r=await fetch(API_BASE+path,{...opts,headers:apiHeaders(true)});}
  }
  return r;
}
function renderSlash(q){
  const menu=$('#slashMenu');
  const opts=SLASH.filter(s=>s.cmd.startsWith(q));
  if(!q.startsWith('/')||!opts.length){menu.classList.remove('open');return;}
  menu.innerHTML=opts.map(o=>`<div class="slash-opt" data-c="${o.cmd}"><code>${o.cmd}</code><span>${o.desc}</span></div>`).join('');
  menu.classList.add('open');
  menu.querySelectorAll('.slash-opt').forEach(el=>el.onclick=()=>{
    $('#input').value=el.dataset.c;menu.classList.remove('open');send();
  });
}

// --- Model menu --------------------------------------------------------------
let modelQuery='';
function renderModels(){
  const menu=$('#modelMenu');
  const q=modelQuery.trim().toLowerCase();
  const list=MODELS.filter(m=>!q||(m.name+' '+m.desc+' '+m.id).toLowerCase().includes(q));
  menu.innerHTML='<input class="mi-search" type="search" placeholder="Search '+MODELS.length+' models…" value="'+esc(modelQuery)+'">'
    +'<div class="mi-head">'+(q?list.length+' of '+MODELS.length:'Model')+'</div>'
    +(list.length?list.map(m=>`
    <div class="mi ${m===state.model?'sel':''}" data-id="${m.id}">
      <div class="mi-ic" style="background:${m.color}${m.text?`;color:${m.text}`:''}">${m.tag}</div>
      <div><b style="font-size:12.5px;display:block">${esc(m.name)}</b><span class="t-xs tx-3">${esc(m.desc)}</span></div>
      ${m===state.model?'<span style="margin-left:auto;color:var(--accent)">✓</span>':''}
    </div>`).join(''):'<div class="mi-empty">No models match “'+esc(modelQuery)+'”</div>');
  const search=menu.querySelector('.mi-search');
  search.addEventListener('input',()=>{modelQuery=search.value;renderModels();
    const s=menu.querySelector('.mi-search');s.focus();s.setSelectionRange(s.value.length,s.value.length);});
  menu.querySelectorAll('.mi').forEach(el=>el.onclick=e=>{
    e.stopPropagation();
    const m=MODELS.find(x=>x.id===el.dataset.id);
    if(m){state.model=m;$('#modelName').textContent=m.name;$('#modelIcon').style.background=m.color;renderModels();saveState();}
    menu.classList.remove('open');
  });
}

// --- Agents ------------------------------------------------------------------
async function loadAgents(){
  try{
    const r=await fetch(API_BASE+'/v1/agents',{headers:apiHeaders()});
    if(!r.ok)return;
    const data=(await r.json()).data||[];
    state._agents=data;
    renderAgentMenu(data);
  }catch(_){}
}
function renderAgentMenu(agents){
  const list=$('#agentList');
  const gm=state.groupMode;
  if(!agents.length){list.innerHTML='<div style="padding:10px;color:var(--text-3);font-size:12.5px">No agents yet. Click ＋ to create one.</div>';return;}
  const head=gm?`<div class="mi-head">Pick the group's agents — click to add/remove</div>`:'';
  list.innerHTML=head+agents.map(a=>{
    const inGroup=state.group&&state.group.agents.some(x=>x.id===a.id);
    return `<div class="mi agent-opt ${!gm&&state.agent&&state.agent.id===a.id?'sel':''}" data-id="${a.id}">
      <div class="mi-ic" style="background:var(--accent)">🤖</div>
      <div><b style="font-size:12.5px;display:block">${esc(a.name)}</b><span class="t-xs tx-3">${esc(a.description||a.model)}</span></div>
      ${gm?(inGroup?'<span style="margin-left:auto;color:var(--accent)">✓ in group</span>':''):(state.agent&&state.agent.id===a.id?'<span style="margin-left:auto;color:var(--accent)">✓</span>':'')}
    </div>`}).join('');
  list.querySelectorAll('.agent-opt').forEach(el=>el.onclick=()=>{
    const a=agents.find(x=>x.id==el.dataset.id);
    if(state.groupMode){toggleGroupAgent(a);renderAgentMenu(agents);renderGroupBar();}
    else{selectAgent(a);$('#agentMenu').style.display='none';renderAgentMenu(agents);}
  });
}

// --- Group chat -------------------------------------------------------------
const GCOLORS=['#8b5cf6','#0ea5e9','#10b981','#f59e0b','#ef4444','#ec4899','#14b8a6','#6366f1'];
function agentColor(name){
  if(name==='Orchestrator')return '#eab308';
  const i=(state.group?.agents||[]).findIndex(a=>a.name===name);
  return GCOLORS[(i>=0?i:0)%GCOLORS.length];
}
function toggleGroupAgent(a){
  if(!state.group)state.group={agents:[]};
  const i=state.group.agents.findIndex(x=>x.id===a.id);
  if(i>=0)state.group.agents.splice(i,1);else state.group.agents.push(a);
}
function renderGroupBar(){
  const bar=$('#groupStage'),g=state.group;
  if(!state.groupMode||!g||!g.agents.length){bar.style.display='none';return;}
  bar.style.display='flex';
  bar.innerHTML='<span id="gNow" class="gnow"></span>'+
    `<details class="gdrop" id="gDrop"><summary>👥 ${g.agents.length} agent${g.agents.length>1?'s':''} ▾</summary>`+
    '<div class="glist">'+
    g.agents.map((a,i)=>`<span class="gagent" data-name="${esc(a.name)}" style="--gc:${GCOLORS[i%GCOLORS.length]}">
      <span class="gav">${esc(a.name[0].toUpperCase())}</span><span class="gname">${esc(a.name)}</span>
      <button class="grm" title="Remove from group">✕</button></span>`).join('')+
    '</div></details>'+
    `<span class="gctl t-xs tx-3">
       <button class="ui-btn ui-btn-ghost t-xs" id="gExit">exit</button></span>`;
  bar.querySelectorAll('.grm').forEach(b=>b.onclick=e=>{
    e.stopPropagation();
    const nm=e.target.closest('.gagent').dataset.name;
    g.agents=g.agents.filter(a=>a.name!==nm);
    renderGroupBar();renderAgentMenu(state._agents||[]);
  });
  bar.querySelector('#gExit').onclick=()=>{exitGroupMode();};
}
function exitGroupMode(){
  state.groupMode=false;state.group=null;
  $('#groupStage').style.display='none';
  renderAgentMenu(state._agents||[]);
  selectAgent(null);
}
function setStageLive(name,done){
  const now=document.querySelector('#groupStage .gnow');
  document.querySelectorAll('#groupStage .gagent').forEach(el=>{
    const elName=el.dataset.name;
    el.classList.toggle('live',elName===name);
    if(done&&elName===name)el.classList.add('done');
    if(!done&&elName===name)el.classList.remove('done');
    if(now&&elName===name)now.innerHTML=name?`<span class="gagent live" style="--gc:${el.style.getPropertyValue('--gc')}"><span class="gav">${el.querySelector('.gav').textContent}</span><span class="gname">${esc(elName)}</span></span>`:'';
  });
  if(now&&!name)now.innerHTML='';
}
async function streamGroupReply(c){
  state.streaming=true;setSend(true);
  const inner=$('#chatInner'),scroller=$('#chatScroll');
  const messages=c.msgs
    .filter(m=>m.role==='user'||m.role==='assistant')
    .map(m=>({role:m.role,name:m.role==='user'?'You':(m.agentName||'Nova'),content:m.text}));
  const last=messages[messages.length-1];
  const payload={message:last.content+'\n\n'+responseLengthInstruction(),history:messages.slice(0,-1),
    agent_ids:state.group.agents.map(a=>a.id),conversation_id:c.id};
  const ctrl=new AbortController();
  state.abort={get stop(){return ctrl.signal.aborted},set stop(v){if(v)ctrl.abort()}};
  // per-speaker typing row + activity list
  let trow=null,acts=null,actLbl=null,actList=null;
  const endTurn=()=>{
    if(trow){const steps=trow.querySelector('.act-steps');
      steps.removeAttribute('open');
      steps.querySelectorAll('.act-item').forEach(i=>i.classList.add('done'));
      actLbl.textContent=`⚡ ${acts.length} step${acts.length!==1?'s':''}`;
      const c2=trow.querySelector('.msg-col');
      trow.remove();/* keep it simple: drop typing row; the real bubble is appended */
    }
    trow=null;
  };
  const startTurn=name=>{
    endTurn();
    trow=document.createElement('div');trow.className='msg-row row-ai';
    trow.innerHTML=`<div class="msg-av ai" style="background:${agentColor(name)}">🤖</div><div class="msg-col"><div class="msg-meta"><b>${esc(name)}</b></div><div class="typing"><i></i><i></i><i></i></div><details class="act-steps" open><summary><span class="act-ico">⚡</span><span class="act-lbl">working…</span></summary><div class="act-list"></div></details></div>`;
    inner.appendChild(trow);scrollBottom();
    acts=[];actLbl=trow.querySelector('.act-lbl');actList=trow.querySelector('.act-list');
    setStageLive(name,false);
  };
  const addAct=text=>{if(!actList)return;acts.push(text);
    actLbl.textContent=`${acts.length} step${acts.length!==1?'s':''}…`;
    const it=document.createElement('div');it.className='act-item';
    it.innerHTML=`<span class="act-dot"></span><span>${esc(text)}</span>`;
    actList.appendChild(it);scrollBottom();};
  const fail=note=>{
    if(trow)trow.remove();
    c.msgs.push({role:'assistant',text:`_${note}_`,t:Date.now(),failed:true});
    renderAll();state.streaming=false;state.abort=null;setSend(false);setStageLive(null);
  };
  try{
    const doFetch=()=>fetch(API_BASE+'/v1/groupchat/stream',{method:'POST',headers:apiHeaders(),signal:ctrl.signal,body:JSON.stringify(payload)});
    let resp=await doFetch();
    if(resp.status===401){localStorage.removeItem('gateway_key');API_KEY='';
      if(askGatewayKey())resp=await doFetch();}
    if(!resp.ok||!resp.body){
      const et=await resp.text().catch(()=>'');throw new Error(`HTTP ${resp.status} ${et.slice(0,200)}`);}
    const reader=resp.body.getReader(),dec=new TextDecoder();
    let buf='';
    while(true){
      const{done,value}=await reader.read();
      if(done)break;
      buf+=dec.decode(value,{stream:true});
      const parts=buf.split('\n\n');buf=parts.pop();
      for(const part of parts){
        const line=part.trim();
        if(!line.startsWith('data:'))continue;
        const data=line.slice(5).trim();
        if(data==='[DONE]')continue;
        try{
          const j=JSON.parse(data);
          const ev=j.freellm;if(!ev)continue;
          if(ev.event==='agent_start')startTurn(ev.agent);
          else if(ev.event==='agent_message'){
            setStageLive(ev.agent,true);
            const steps=trow?trow.querySelector('.act-steps'):null;
            if(trow)trow.remove();
            const msg={role:'assistant',text:ev.content,t:Date.now(),agentName:ev.agent,model:'group'};
            c.msgs.push(msg);
            const row=msgEl(msg);
            if(steps&&acts&&acts.length)row.querySelector('.msg-col').prepend(steps);
            inner.appendChild(row);scrollBottom();
            trow=null;
          }
          else if(ev.event==='error'){addAct('⚠ '+ev.text);}
          else addAct(ev.text||ev.event);
        }catch(_){}
      }
    }
    endTurn();setStageLive(null);
    state.streaming=false;state.abort=null;setSend(false);
    renderSidebar($('#convSearch').value.toLowerCase());saveState();
  }catch(e){
    if(ctrl.signal.aborted){endTurn();setStageLive(null);fail('⏹ Stopped by user.');}
    else fail(`⚠ ${e.message}`);
  }
}

// --- Research mode ----------------------------------------------------------
// Full pipeline: route -> plan -> parallel researchers -> gap check ->
// verifier -> synthesizer. Stage events stream into the activity list; the
// terminal `research_answer` chunk carries the final markdown.
const RESEARCH_DEPTHS=['auto','quick','standard','deep'];
function cycleResearchDepth(){
  const i=RESEARCH_DEPTHS.indexOf(state.researchDepth);
  state.researchDepth=RESEARCH_DEPTHS[(i+1)%RESEARCH_DEPTHS.length];
  paintResearchBtn();toast('Research depth: '+state.researchDepth);
}
function paintResearchBtn(){
  const b=$('#researchBtn');if(!b)return;
  b.classList.toggle('on',!!state.researchMode);
  b.innerHTML='🔬<span class="tb-tx"> Research</span> <span style="opacity:.7;font-size:10px">'+esc(state.researchDepth)+'</span>';
}
function toggleResearchMode(){
  state.researchMode=!state.researchMode;
  if(state.researchMode){exitGroupModeKeepAgent();selectAgent(null);}
  paintResearchBtn();
  $('#chatStatus').textContent=state.researchMode
    ?`research mode · depth ${state.researchDepth} · multi-step, slower`
    :'connected · replies stream in real time';
  toast(state.researchMode?'Research mode on':'Research mode off');
}
function exitGroupModeKeepAgent(){state.groupMode=false;state.group=null;$('#groupStage').style.display='none';}
async function streamResearchReply(c){
  state.streaming=true;setSend(true);
  const inner=$('#chatInner'),scroller=$('#chatScroll');
  const messages=c.msgs
    .filter(m=>m.role==='user'||m.role==='assistant')
    .map(m=>({role:m.role,name:m.role==='user'?'You':(m.agentName||'Nova'),content:m.text}));
  const last=messages[messages.length-1];
  const payload={message:last.content+'\n\n'+responseLengthInstruction(),history:messages.slice(0,-1),model:state.model.id,
    depth:state.researchDepth==='auto'?null:state.researchDepth,conversation_id:c.id,save:true};
  const ctrl=new AbortController();
  state.abort={get stop(){return ctrl.signal.aborted},set stop(v){if(v)ctrl.abort()}};
  let trow=null,acts=null,actLbl=null,actList=null,answer=null,meta=null;
  const endTurn=()=>{if(trow){const steps=trow.querySelector('.act-steps');
    if(steps){steps.removeAttribute('open');steps.querySelectorAll('.act-item').forEach(i=>i.classList.add('done'));
      actLbl.textContent=`⚡ ${acts.length} step${acts.length!==1?'s':''}`;}
    trow.remove();}trow=null;};
  const startTurn=()=>{
    endTurn();
    trow=document.createElement('div');trow.className='msg-row row-ai';
    trow.innerHTML=`<div class="msg-av ai" style="background:var(--accent);color:var(--on-accent)">🔬</div><div class="msg-col"><div class="msg-meta"><b>Research</b></div>
      <details class="act-steps" open><summary class="act-lbl">planning…</summary><div class="act-list"></div></details>
      <div class="bubble typing"><span class="dots"><i></i><i></i><i></i></span></div></div>`;
    inner.appendChild(trow);scrollBottom();
    acts=[];actLbl=trow.querySelector('.act-lbl');actList=trow.querySelector('.act-list');
  };
  const addAct=text=>{if(!actList)return;acts.push(text);
    actLbl.textContent=`${acts.length} step${acts.length!==1?'s':''}…`;
    const it=document.createElement('div');it.className='act-item';
    it.innerHTML=`<span class="act-dot"></span><span>${esc(text)}</span>`;
    actList.appendChild(it);scrollBottom();};
  const finish=(msg,bubble)=>{
    if(window.NovaRender&&NovaRender.ResponseRenderer){
      NovaRender.ResponseRenderer.renderMessage(msg,bubble,{showActions:true});
      msg.html=bubble.innerHTML;
    }else{msg.html=md(msg.text);bubble.innerHTML=msg.html;}
    state.streaming=false;state.abort=null;setSend(false);
    renderSidebar($('#convSearch').value.toLowerCase());saveState();
  };
  const fail=note=>{
    if(trow)trow.remove();
    c.msgs.push({role:'assistant',text:`_${note}_`,t:Date.now(),failed:true});
    renderAll();state.streaming=false;state.abort=null;setSend(false);
  };
  try{
    const doFetch=()=>fetch(API_BASE+'/v1/research/stream',{method:'POST',headers:apiHeaders(),signal:ctrl.signal,body:JSON.stringify(payload)});
    let resp=await doFetch();
    if(resp.status===401){localStorage.removeItem('gateway_key');API_KEY='';
      if(askGatewayKey())resp=await doFetch();}
    if(!resp.ok||!resp.body){
      const et=await resp.text().catch(()=>'');throw new Error(`HTTP ${resp.status} ${et.slice(0,200)}`);}
    startTurn();
    const reader=resp.body.getReader(),dec=new TextDecoder();
    let buf='';
    while(true){
      const{done,value}=await reader.read();
      if(done)break;
      buf+=dec.decode(value,{stream:true});
      const parts=buf.split('\n\n');buf=parts.pop();
      for(const part of parts){
        const line=part.trim();
        if(!line.startsWith('data:'))continue;
        const data=line.slice(5).trim();
        if(data==='[DONE]')continue;
        try{
          const j=JSON.parse(data);
          const ev=j.freellm;
          if(ev&&ev.event==='research_answer'){answer=ev.answer;meta=ev;continue;}
          if(ev&&ev.event==='error'){addAct('⚠ '+ev.text);continue;}
          if(ev)addAct(ev.text||ev.event);
          else if(j.choices&&j.choices[0]&&j.choices[0].delta&&j.choices[0].delta.content){answer=j.choices[0].delta.content;}
        }catch(_){}
      }
    }
    if(!answer)throw new Error('research returned no answer');
    endTurn();
    const msg={role:'assistant',text:answer,t:Date.now(),model:'research',agentName:'Research'};
    if(meta){msg.research={depth:meta.depth,sources:meta.sources,findings:meta.findings,verified:meta.verified,seconds:meta.seconds,rounds:meta.rounds,run_id:meta.run_id};}
    c.msgs.push(msg);
    const row=msgEl(msg);inner.appendChild(row);
    const bubble=row.querySelector('.bubble');
    finish(msg,bubble);
  }catch(e){
    if(ctrl.signal.aborted)fail('⏹ Stopped by user.');
    else fail(`⚠ ${e.message}`);
  }
}

window.selectAgent=function(a){
  if(!state.groupMode){state.group=null;$('#groupStage').style.display='none';}
  state.agent=a;
  if(a){
    $('#agentBtn').innerHTML='🤖<span class="tb-tx"> '+esc(a.name)+'</span>';
    $('#agentBtn').classList.add('on');
    $('#chatStatus').textContent=`Agent: ${a.name} · ${a.description||a.model}`;
    $('#agentEditBtn').style.display='inline-flex';
    $('#agentDelBtn').style.display='inline-flex';
    toast(`Agent: ${a.name}`);
  }else{
    $('#agentBtn').innerHTML='🤖<span class="tb-tx"> Agents</span>';
    $('#agentBtn').classList.remove('on');
    $('#chatStatus').textContent='connected · replies stream in real time';
    $('#agentEditBtn').style.display='none';
    $('#agentDelBtn').style.display='none';
  }
};
$('#agentBtn').onclick=e=>{e.stopPropagation();
  $('#agentMenu').style.display=$('#agentMenu').style.display==='block'?'none':'block';};
$('#agentNoneBtn').onclick=()=>{
  exitGroupMode();
  $('#agentMenu').style.display='none';
  loadAgents();
  toast('Raw model mode');
};
$('#agentGroupBtn').onclick=()=>{
  state.groupMode=true;
  if(!state.group)state.group={agents:[]};
  renderAgentMenu(state._agents||[]);
  renderGroupBar();
  toast('Group mode — click agents to add them');
};
// --- Roleplay --------------------------------------------------------------
const RP_CHARS_KEY='nova_rp_chars',RP_CHAR_KEY='nova_rp_char';
function rpChars(){try{return JSON.parse(localStorage.getItem(RP_CHARS_KEY)||'[]');}catch(_){return[];}}
function rpSaveLocal(cs){try{localStorage.setItem(RP_CHARS_KEY,JSON.stringify(cs));}catch(_){}}
function rpSaveChars(cs){rpSaveLocal(cs);rpPushServer();}
// --- Server persistence: characters live in the gateway DB, localStorage is cache ---
function rpToServer(cs){return (cs||[]).map(c=>({id:c.id||null,name:c.name,emoji:c.emoji||'',tagline:c.tagline||'',description:c.desc||'',personality:c.personality||'',style:c.style||'',greeting:c.greeting||''}));}
function rpFromServer(rows){return (rows||[]).map(r=>({id:r.id,name:r.name,emoji:r.emoji||'',tagline:r.tagline||'',desc:r.description||'',personality:r.personality||'',style:r.style||'',greeting:r.greeting||''}));}
let _rpSyncT=null;
function rpPushServer(){
  clearTimeout(_rpSyncT);
  _rpSyncT=setTimeout(async()=>{
    try{
      const r=await fetch(API_BASE+'/v1/characters',{method:'PUT',headers:apiHeaders(),body:JSON.stringify({characters:rpToServer(rpChars())})});
      if(!r.ok)return;
      const rows=await r.json();
      rpSaveLocal(rpFromServer(rows));
      if($('#rpMenu')&&$('#rpMenu').style.display!=='none')renderRpMenu();
    }catch(_){}
  },600);
}
async function rpServerSync(){
  try{
    const r=await fetch(API_BASE+'/v1/characters',{headers:apiHeaders()});
    if(!r.ok)return;
    const remote=rpFromServer(await r.json());
    const local=rpChars();
    if(remote.length||!local.length){rpSaveLocal(remote);}
    else{rpSaveChars(local);} // one-time migration of local-only characters
    if($('#rpMenu')&&$('#rpMenu').style.display!=='none')renderRpMenu();
  }catch(_){}
}
function rpPersona(ch){
  return `You are ${ch.name}${ch.tagline?' — '+ch.tagline:''}. ${ch.desc||''} `
    +`Personality: ${ch.personality||'warm, engaging'}. `
    +(ch.style?`Speaking style: ${ch.style}. `:'')
    +'Stay in character at all times: never mention being an AI or that this is roleplay, never break character. '
    +`Write ${ch.name}'s replies in first person, in ${ch.name}'s voice. Keep replies concise unless the scene calls for more. `
    +'If the user writes OOC ((...)), acknowledge briefly and continue the scene.';
}
function paintRpBtn(){const b=$('#rpBtn');if(!b)return;
  if(state.rpChar){b.innerHTML=`🎭<span class="tb-tx"> ${esc(state.rpChar.name)}</span>`;b.classList.add('on');}
  else{b.innerHTML='🎭<span class="tb-tx"> Roleplay</span>';b.classList.remove('on');}}
function renderRpMenu(){
  const list=$('#rpList');if(!list)return;list.innerHTML='';
  const cs=rpChars();
  if(!cs.length){const d=document.createElement('div');d.className='mi';d.style.color='var(--text-3)';d.textContent='No characters yet — create one';list.appendChild(d);}
  cs.forEach((ch,i)=>{
    const d=document.createElement('div');d.className='mi';
    d.style.display='flex';d.style.alignItems='center';d.style.gap='6px';
    d.innerHTML=`<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${ch.emoji||'🎭'} ${esc(ch.name)}${ch.tagline?` <span style="opacity:.6">— ${esc(ch.tagline)}</span>`:''}</span>`
      +`<span class="rp-edit" data-i="${i}" title="Edit" style="opacity:.5;cursor:pointer">✏️</span>`
      +`<span class="rp-del" data-i="${i}" title="Delete" style="opacity:.5;cursor:pointer;color:var(--error)">🗑</span>`;
    d.querySelector('.rp-edit').onclick=e=>{e.stopPropagation();rpOpenModal(i);$('#rpMenu').style.display='none';};
    d.querySelector('.rp-del').onclick=e=>{e.stopPropagation();
      const cs2=rpChars();cs2.splice(i,1);rpSaveChars(cs2);
      if(state.rpChar&&state.rpChar.name===ch.name)rpExit();
      renderRpMenu();toast('Character deleted');};
    d.onclick=()=>{rpActivate(ch);$('#rpMenu').style.display='none';};
    list.appendChild(d);
  });
}
function rpActivate(ch){
  state.rpChar=ch;localStorage.setItem(RP_CHAR_KEY,JSON.stringify(ch));paintRpBtn();
  $('#chatStatus').textContent=`roleplay · ${ch.name} · in character`;
  const c=state.current;
  if(c&&c.msgs.length===0&&ch.greeting){c.msgs.push({role:'assistant',text:ch.greeting,t:Date.now(),agentName:ch.name});renderAll();saveState();}
  toast(`Roleplay: ${ch.name}`);
}
function rpExit(){
  state.rpChar=null;try{localStorage.removeItem(RP_CHAR_KEY);}catch(_){}paintRpBtn();
  $('#chatStatus').textContent='connected · replies stream in real time';toast('Roleplay off');
}
function rpOpenModal(i){
  const ch=(i==null||i<0)?null:rpChars()[i];
  $('#rpModalTitle').textContent=ch?'Edit character':'New character';
  $('#rpEmoji').value=ch?ch.emoji:'🎭';$('#rpName').value=ch?ch.name:'';
  $('#rpTagline').value=ch?ch.tagline:'';$('#rpDesc').value=ch?ch.desc:'';
  $('#rpPersonality').value=ch?ch.personality:'';$('#rpStyle').value=ch?ch.style:'';
  $('#rpGreeting').value=ch?ch.greeting:'';
  $('#rpModal').dataset.edit=String((i==null||i<0)?-1:i);
  $('#rpModal').style.display='flex';
}
$('#rpBtn').onclick=()=>{renderRpMenu();const m=$('#rpMenu');m.style.display=(m.style.display==='none')?'block':'none';};
$('#rpStarterBtn').onclick=async()=>{
  try{
    const r=await fetch(API_BASE+'/v1/characters/starter',{method:'POST',headers:apiHeaders()});
    if(!r.ok)throw new Error('HTTP '+r.status);
    rpSaveLocal(rpFromServer(await r.json()));
    renderRpMenu();toast('Starter characters added');
  }catch(_){toast('Could not load starter pack','err');}
};
$('#rpNewBtn').onclick=()=>{rpOpenModal(-1);$('#rpMenu').style.display='none';};
$('#rpNoneBtn').onclick=()=>{$('#rpMenu').style.display='none';if(state.rpChar)rpExit();};
$('#rpCancel').onclick=()=>{$('#rpModal').style.display='none';};
document.addEventListener('click',e=>{const m=$('#rpMenu');
  if(m&&m.style.display!=='none'&&!m.contains(e.target)&&e.target.id!=='rpBtn')m.style.display='none';});
$('#rpSave').onclick=()=>{
  const name=$('#rpName').value.trim();if(!name)return toast('Name required','err');
  const ch={name,emoji:$('#rpEmoji').value.trim()||'🎭',tagline:$('#rpTagline').value.trim(),
    desc:$('#rpDesc').value.trim(),personality:$('#rpPersonality').value.trim(),
    style:$('#rpStyle').value.trim(),greeting:$('#rpGreeting').value.trim()};
  const cs=rpChars();const i=parseInt($('#rpModal').dataset.edit,10);
  if(i>=0&&cs[i])cs[i]=ch;else cs.push(ch);
  rpSaveChars(cs);$('#rpModal').style.display='none';renderRpMenu();
  if(state.rpChar&&state.rpChar.name===ch.name){state.rpChar=ch;localStorage.setItem(RP_CHAR_KEY,JSON.stringify(ch));paintRpBtn();}
  toast((i>=0)?'Character updated':'Character saved — pick it from 🎭 Roleplay');
};
paintRpBtn();
initResponseLength();
rpServerSync();
$('#researchBtn').onclick=()=>{toggleResearchMode();};
$('#researchBtn').ondblclick=e=>{e.preventDefault();cycleResearchDepth();};
paintResearchBtn();
$('#shareBtn').onclick=async()=>{
  const c=state.current;
  if(!c||!c.msgs.length)return toast('Nothing to share','err');
  const msgs=c.msgs
    .filter(m=>m.role==='user'||m.role==='assistant')
    .map(m=>({role:m.role,name:m.role==='user'?'You':(m.agentName||state.agent?.name||'Nova'),agentName:m.agentName||null,text:m.text}));
  try{
    const r=await fetch(API_BASE+'/v1/share',{method:'POST',headers:apiHeaders(),
      body:JSON.stringify({title:c.name,messages:msgs})});
    if(!r.ok)throw new Error('HTTP '+r.status);
    const d=await r.json();
    const url=location.origin+d.url;
    try{await navigator.clipboard.writeText(url);toast('Link copied — opening…');}
    catch(_){toast('Shared: '+url);}
    window.open(url,'_blank');
  }catch(e){toast('Share failed: '+e.message,'err');}
};
document.addEventListener('click',e=>{
  if(!e.target.closest('#agentMenu')&&!e.target.closest('#agentBtn'))
    $('#agentMenu').style.display='none';
});
document.addEventListener('click',e=>{
  const d=$('#gDrop');
  if(d&&d.open&&!e.target.closest('#gDrop'))d.open=false;
});
$('#agentDelBtn').onclick=async()=>{
  if(!state.agent||!confirm(`Delete agent "${state.agent.name}"?`))return;
  const r=await fetch(API_BASE+'/admin/agents/'+state.agent.id,{method:'DELETE',headers:apiHeaders(true)});
  if(r.ok||r.status===204){
    selectAgent(null);loadAgents();toast('Agent deleted');
  }else toast('Delete failed','err');
};

// --- Input events ------------------------------------------------------------
const ta=$('#input');
function autogrow(){ta.style.height='auto';ta.style.height=Math.min(ta.scrollHeight,180)+'px';}
ta.addEventListener('input',()=>{autogrow();renderSlash(ta.value);});
ta.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}
  if(e.key==='Escape')$('#slashMenu').classList.remove('open');
});
$('#sendBtn').onclick=()=>{if(state.streaming&&state.abort)state.abort.stop=true;else send();};
$('#newChat').onclick=()=>{const c=mkConv('New conversation');state.conversations.unshift(c);state.current=c;renderAll();ta.focus();};
$('#convSearch').addEventListener('input',e=>renderSidebar(e.target.value.toLowerCase()));
// Mobile-first sidebar: slide-in drawer with backdrop on small screens.
function openSidebar(){$('#sidebar').classList.add('open');$('#sidebar').classList.remove('hidden');$('#sbBackdrop').classList.add('show');}
function closeSidebar(){$('#sidebar').classList.remove('open');$('#sidebar').classList.add('hidden');$('#sbBackdrop').classList.remove('show');}
function toggleSidebar(){
  if($('#sidebar').classList.contains('open'))closeSidebar();
  else openSidebar();
}
$('#hideSidebar').onclick=closeSidebar;
$('#showSidebar').onclick=toggleSidebar;
$('#sbBackdrop').onclick=closeSidebar;
// On mobile, start with sidebar closed. On desktop, sidebar is always visible.
if(innerWidth<1024)$('#sidebar').classList.add('hidden');
// Close sidebar when selecting a conversation on mobile.
window.addEventListener('resize',()=>{
  if(innerWidth>=1024){$('#sbBackdrop').classList.remove('show');$('#sidebar').classList.remove('hidden');}
  else if(!$('#sidebar').classList.contains('open'))$('#sidebar').classList.add('hidden');
});
let lastModelFetch=0;
$('#modelSel').onclick=e=>{
  e.stopPropagation();
  const menu=$('#modelMenu');const opening=!menu.classList.contains('open');
  menu.classList.toggle('open');
  if(opening&&Date.now()-lastModelFetch>60000){lastModelFetch=Date.now();loadModels(true);}
};
document.addEventListener('click',e=>{
  if(!e.target.closest('#modelSel'))$('#modelMenu').classList.remove('open');
});
$('#attachBtn').onclick=()=>$('#fileInput').click();
$('#fileInput').addEventListener('change',e=>{
  for(const f of e.target.files){
    const chip=document.createElement('span');chip.className='ui-chip';chip.dataset.name=f.name;
    chip._file=f;
    const isImg=f.type.startsWith('image/');
    chip.innerHTML=`${isImg?'�':'�📎'} ${esc(f.name)}<button>✕</button>`;
    chip.querySelector('button').onclick=()=>{chip.remove();maybeSwitchVision();};
    $('#chipRow').appendChild(chip);
  }
  e.target.value='';maybeSwitchVision();ta.focus();
});

// --- Vision model auto-select -----------------------------------------------
const VISION_RE=/vision|vl-|vlm|gemma|pixtral|llava|llama-4|llama-4-scout|llama-4-maverick|gemini-|qwen.*vl|moondream/i;
const VISION_BAD_RE=/embed|whisper|tts|guard/i;
function imageFiles(){
  return [...$('#chipRow').children].map(c=>c._file).filter(f=>f&&f.type.startsWith('image/'));
}
function pickVisionModel(){
  return MODELS.find(m=>VISION_RE.test(m.id)&&!VISION_BAD_RE.test(m.id))||null;
}
function maybeSwitchVision(){
  const imgs=imageFiles();
  if(imgs.length){
    const vm=pickVisionModel();
    if(vm&&vm!==state.model){
      state.model=vm;$('#modelName').textContent=vm.name;$('#modelIcon').style.background=vm.color;
      renderModels();saveState();
      toast(`Switched to ${vm.id} (vision)`);
    }else if(!vm){
      toast('No vision-capable model found in /v1/models — image may be ignored','err');
    }
  }
}
function fileToDataURL(f){
  return new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(r.result);r.onerror=rej;r.readAsDataURL(f);});
}
$('#micBtn').onclick=()=>{
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!SR){toast('Voice input not supported','err');return;}
  const r=new SR();r.lang='en-US';
  $('#micBtn').style.color='var(--error)';
  r.onresult=e=>{ta.value+=e.results[0][0].transcript;autogrow();};
  r.onend=()=>$('#micBtn').style.color='';
  r.onerror=()=>{$('#micBtn').style.color='';toast('Mic error','err');};
  r.start();toast('Listening…');
};
document.addEventListener('click',e=>{
  if(e.target.classList.contains('copy-code')){
    navigator.clipboard.writeText(e.target.closest('.codeblock').querySelector('code').textContent);
    e.target.textContent='copied!';setTimeout(()=>e.target.textContent='copy',1200);
  }
  if(e.target.classList.contains('car-btn')){
    const car=e.target.closest('.rich-wrap').querySelector('.carousel');
    car.scrollBy({left:220*(+e.target.dataset.d),behavior:'smooth'});
  }
});


// --- Token budget meter ------------------------------------------------------
async function refreshTokenMeter(){
  const el=$('#tokenMeter');if(!el)return;
  if(!ADMIN_TOKEN){el.style.display='none';return;}
  try{
    const r=await fetch(API_BASE+'/admin/stats',{headers:apiHeaders(true)});
    if(!r.ok)return;
    const s=await r.json();
    const used=(s.tokens_in_24h||0)+(s.tokens_out_24h||0);
    const f=n=>n==null?'\u2014':n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'k':''+n;
    el.textContent='tokens today: '+f(used)+(s.est_tokens_total?' \u00b7 ~'+f(s.est_tokens_total)+' est. free/day':'');
    el.style.display='block';
  }catch(e){}
}
setInterval(refreshTokenMeter,60000);

// Weak free-tier models sometimes degenerate and repeat one line dozens of
// times. Collapse runs of >=4 identical consecutive lines in the FINAL text
// (never inside a nova-response envelope, where lines are JSON structure).
function collapseRepeats(text){
  if(!text||text.includes('response_type'))return text;
  const lines=text.split('\n'),out=[];let i=0;
  while(i<lines.length){
    const ln=lines[i],key=ln.trim();
    const structural=key===''||key.startsWith('```')||key.startsWith('{')||key.startsWith('"')||key.startsWith('#')||key.startsWith('-');
    if(structural){out.push(ln);i++;continue;}
    let j=i;while(j<lines.length&&lines[j]===ln)j++;
    const n=j-i;
    if(n>=4){out.push(ln,' _(repeated '+n+' times)_');}
    else for(let k=i;k<j;k++)out.push(lines[k]);
    i=j;
  }
  return out.join('\n');
}
// --- Init --------------------------------------------------------------------
if(!loadState())seed();
renderModels();renderAll();loadModels();loadAgents();refreshTokenMeter();
fetchServerConversations().then(()=>renderAll());
window.refreshAgents=loadAgents;
})();
