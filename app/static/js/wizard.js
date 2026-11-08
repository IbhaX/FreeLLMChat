/* Agent Builder Wizard — 6 steps, live preview, test chat.
   Emits config compatible with backend _compile_system_prompt:
   {basic, personality, instructions, execution, capabilities} */
(function(){
const CATEGORIES=['Research','Coding','Data Analysis','Automation','Customer Support','Business Intelligence','Personal Assistant','Content Generation','Custom'];
const CAT_ICO={'Research':'🔍','Coding':'💻','Data Analysis':'📊','Automation':'⚙️','Customer Support':'💬','Business Intelligence':'📈','Personal Assistant':'🗓','Content Generation':'✍️','Custom':'✨'};
const STYLES=['Professional','Friendly','Technical','Conversational','Formal','Concise','Detailed'];
const LENGTHS=['Very short','Short','Medium','Long','Very long'];
const TECH=['Beginner','Intermediate','Advanced','Expert'];
const PRESETS=[
  {name:'Analytical',ico:'🧠',vals:{communication_style:'Technical',creativity:2,formality:8,assertiveness:8,empathy:3,explain_reasoning:true,cite_sources:true,admit_uncertainty:true}},
  {name:'Friendly Coach',ico:'🤝',vals:{communication_style:'Friendly',creativity:6,formality:3,assertiveness:5,empathy:9,provide_examples:true,ask_clarifying:true}},
  {name:'Concise Assistant',ico:'⚡',vals:{communication_style:'Concise',creativity:3,formality:5,assertiveness:7,empathy:4,response_length:'Short'}},
  {name:'Creative Partner',ico:'🎨',vals:{communication_style:'Conversational',creativity:9,formality:3,assertiveness:6,empathy:6,provide_examples:true}},
];
const STEPS=[
  {id:'basic',t:'Basics',d:'Identity & purpose',ico:'◈'},
  {id:'personality',t:'Personality',d:'Tone & behavior',ico:'◎'},
  {id:'instructions',t:'Instructions',d:'System prompt & rules',ico:'✎'},
  {id:'capabilities',t:'Capabilities',d:'Tools & knowledge',ico:'⚒'},
  {id:'model',t:'Model',d:'Model & execution',ico:'⬡'},
  {id:'review',t:'Review',d:'Confirm & create',ico:'✓'},
];

const blank=()=>({
  name:'',description:'',detailed_description:'',category:'',objective:'',
  problems:'',audience:'',domain:'',tags:'',visibility:'private',status:'active',
  personality_type:'',communication_style:'',tone:'',response_length:'',
  language:'',technical_detail:'',
  creativity:5,formality:5,assertiveness:5,empathy:5,
  ask_clarifying:false,explain_reasoning:false,provide_examples:false,
  cite_sources:false,admit_uncertainty:false,
  system_prompt:'',custom_rules:'',constraints:'',knowledge:'',
  enable_tools:false,tools:[],
  model:'auto',temperature:'',max_tokens:'',output_format:'text',
  decision_making:'',error_handling:'',max_retries:'',timeout:'',
});

let W=blank(),step=1,editId=null,MODEL_CHOICES=[],AVAILABLE_TOOLS=[];

// ---------- helpers ----------
const V=(k)=>W[k]??'';
const inp=(k,ph,extra='')=>`<input id="w_${k}" class="ui-input" placeholder="${ph}" value="${esc(V(k))}" ${extra}>`;
const txa=(k,ph,rows=3)=>`<textarea id="w_${k}" class="ui-textarea" rows="${rows}" placeholder="${ph}">${esc(V(k))}</textarea>`;
const sel=(k,opts,ph='Select…')=>`<select id="w_${k}" class="ui-select"><option value="">${ph}</option>${opts.map(o=>`<option ${V(k)===o?'selected':''}>${o}</option>`).join('')}</select>`;
const seg=(k,opts)=>`<div class="ui-seg" id="w_${k}">${opts.map(([v,l])=>`<button type="button" data-v="${v}" class="${V(k)===v?'sel':''}">${l}</button>`).join('')}</div>`;
const sw=(k,label)=>`<label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-2);cursor:pointer"><span class="ui-switch"><input type="checkbox" id="w_${k}" ${W[k]?'checked':''}><span class="knob"></span></span>${label}</label>`;
const slider=(k,label)=>`
  <div style="margin-bottom:14px">
    <div class="slider-row" style="margin-bottom:6px"><label class="label" style="margin:0;flex:1">${label}</label><span class="slider-val" id="wv_${k}">${W[k]}</span></div>
    <input type="range" class="ui-slider" id="w_${k}" min="0" max="10" value="${W[k]}" style="--fill:${W[k]*10}%">
    <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-3);margin-top:3px"><span>${sliderMin(k)}</span><span>${sliderMax(k)}</span></div>
  </div>`;
const sliderMin=k=>({creativity:'Precise',formality:'Casual',assertiveness:'Gentle',empathy:'Objective'}[k]);
const sliderMax=k=>({creativity:'Creative',formality:'Formal',assertiveness:'Direct',empathy:'Warm'}[k]);
const fld=(label,inner,hint='')=>`<div class="field"><label class="label">${label}</label>${inner}${hint?`<div class="hint">${hint}</div>`:''}</div>`;
const g2=(html)=>`<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">${html}</div>`;
const full=(html)=>`<div style="grid-column:1/-1">${html}</div>`;

// ---------- step bodies ----------
const BODIES={
  basic:()=>`
    <h3 style="margin-bottom:4px">Basics</h3>
    <p class="tx-3 t-xs" style="margin:0 0 18px">Who is this agent and what does it do?</p>
    ${g2(full(fld('Agent name *',inp('name','code-reviewer')))+
      fld('Short description',inp('description','Reviews code for bugs')))}
    ${fld('Detailed description',txa('detailed_description','A thorough code reviewer that checks for bugs, style issues, security vulnerabilities…'),'Shown to the model as its purpose')}
    <div class="label" style="margin-bottom:8px">Category</div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px" id="w_catGrid">
      ${CATEGORIES.map(c=>`<div class="ui-pcard ${W.category===c?'sel':''}" data-cat="${c}" style="padding:10px;gap:4px">
        <div style="font-size:15px">${CAT_ICO[c]}</div><b style="font-size:12px">${c}</b></div>`).join('')}
    </div>
    ${g2(fld('Primary objective',inp('objective','Review code and give actionable feedback'))+
         fld('Domain / industry',inp('domain','Software / FinTech')))+
      full(fld('Problems the agent solves',txa('problems','Catching bugs early, enforcing style…',2))+
           fld('Target audience',inp('audience','Developers, team leads'))+
           fld('Tags (comma-separated)',inp('tags','code, review, security')))+
      fld('Visibility',seg('visibility',[['private','Private'],['team','Team'],['public','Public']]))+
      fld('Status',seg('status',[['draft','Draft'],['active','Active'],['disabled','Disabled']]))}`,
  personality:()=>`
    <h3 style="margin-bottom:4px">Personality & behavior</h3>
    <p class="tx-3 t-xs" style="margin:0 0 18px">How should the agent communicate?</p>
    <div class="label" style="margin-bottom:8px">Presets — click to apply</div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:18px">
      ${PRESETS.map((p,i)=>`<div class="ui-pcard" data-preset="${i}" style="padding:10px;text-align:center"><div style="font-size:18px">${p.ico}</div><b style="font-size:11px">${p.name}</b></div>`).join('')}
    </div>
    ${g2(fld('Personality type',inp('personality_type','Analytical, methodical'))+
         fld('Communication style',sel('communication_style',STYLES,'Default'))+
         fld('Tone of voice',inp('tone','Encouraging but direct'))+
         fld('Response length',sel('response_length',LENGTHS,'Default'))+
         fld('Language',inp('language','English'))+
         fld('Technical detail',sel('technical_detail',TECH,'Default')))}
    <div class="label" style="margin:6px 0 10px">Characteristics</div>
    ${g2(slider('creativity','Creativity')+slider('formality','Formality')+slider('assertiveness','Assertiveness')+slider('empathy','Empathy'))}
    <div class="label" style="margin:4px 0 10px">Behavior</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
      ${sw('ask_clarifying','Ask clarifying questions')+sw('explain_reasoning','Explain reasoning')+sw('provide_examples','Provide examples')+sw('cite_sources','Cite sources')+sw('admit_uncertainty','Admit uncertainty')}
    </div>
    <div class="ui-panel" style="padding:12px 14px">
      <div class="t-xs tx-3" style="margin-bottom:4px">Personality summary</div>
      <div id="w_persSummary" class="t-sm" style="font-style:italic"></div>
    </div>`,
  instructions:()=>`
    <h3 style="margin-bottom:4px">Instructions & system prompt</h3>
    <p class="tx-3 t-xs" style="margin:0 0 18px">The core instructions the model follows.</p>
    ${fld('System prompt',`<textarea id="w_system_prompt" class="ui-editor" rows="6" placeholder="You are an expert code reviewer. Be concise.">${esc(V('system_prompt'))}</textarea><div class="editor-foot"><span id="w_spCount">${(V('system_prompt')||'').length} chars</span></div>`)}
    ${fld('Custom rules',txa('custom_rules','Always start with a summary. Use bullet points.…'),'How the agent structures and formats answers')}
    ${fld('Constraints',txa('constraints','Only analyze provided code. Max 5 issues per review.…',2),'Hard limits — what it must not do')}
    ${fld('Knowledge base',txa('knowledge','Familiar with Python, TypeScript. Uses OWASP Top 10…'),'Domain facts the agent should assume')}`,
  capabilities:()=>`
    <h3 style="margin-bottom:4px">Capabilities</h3>
    <p class="tx-3 t-xs" style="margin:0 0 18px">Tools the agent can use during a conversation.</p>
    <div class="label" style="margin-bottom:8px">Tool plugins — click to enable</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px" id="w_toolGrid">
      <div class="ui-pcard" id="w_toolLoading"><span class="tx-3 t-sm">Loading tools…</span></div>
    </div>
    <div class="ui-panel" style="padding:12px 14px">
      <div class="t-xs tx-3" style="margin-bottom:4px">Knowledge</div>
      <div class="t-sm tx-2">Knowledge base content is configured on the <b>Instructions</b> step and injected into the system prompt.</div>
    </div>`,
  model:()=>`
    <h3 style="margin-bottom:4px">Model & execution</h3>
    <p class="tx-3 t-xs" style="margin:0 0 18px">Which model serves this agent, and how it runs.</p>
    <div class="label">Model</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;max-height:220px;overflow-y:auto;margin-bottom:16px" id="w_modelGrid">
      ${modelGrid()}
    </div>
    ${g2(fld('Temperature (0–2)',`<div class="slider-row"><input type="range" class="ui-slider" id="w_temperature" min="0" max="20" value="${W.temperature===''?7:W.temperature*10}" style="--fill:${(W.temperature===''?7:W.temperature*10)*5}%"><span class="slider-val" id="wv_temperature">${W.temperature===''?'0.7':W.temperature}</span></div>`)+
         fld('Max tokens',`<input id="w_max_tokens" type="number" class="ui-input" placeholder="4096" value="${V('max_tokens')}">`)+
         fld('Output format',sel('output_format',['text','json','markdown','structured'])))}
    <details class="ui-acc" style="margin-top:6px">
      <summary>Advanced execution</summary>
      <div class="acc-body" style="padding-top:12px">
        ${fld('Decision-making approach',txa('decision_making','Analyze → prioritize by severity → recommend…',2))}
        ${fld('Error handling',txa('error_handling','If unsure, say so. Never hallucinate APIs…',2))}
        ${g2(fld('Max retries',`<input id="w_max_retries" type="number" class="ui-input" placeholder="3" value="${V('max_retries')}">`)+
             fld('Timeout (s)',`<input id="w_timeout" type="number" class="ui-input" placeholder="30" value="${V('timeout')}">`))}
      </div>
    </details>`,
  review:()=>`
    <h3 style="margin-bottom:4px">Review & create</h3>
    <p class="tx-3 t-xs" style="margin:0 0 18px">Confirm the configuration and compiled system prompt.</p>
    <div class="ui-panel" style="padding:14px 16px;margin-bottom:14px" id="w_reviewSummary"></div>
    <div class="label">Compiled system prompt</div>
    <pre class="ui-editor" id="w_reviewPrompt" style="max-height:280px;overflow-y:auto;white-space:pre-wrap;margin:0"></pre>`,
};

function modelGrid(){
  const auto=`<div class="ui-pcard ${W.model==='auto'?'sel':''}" data-model="auto"><b style="font-size:12px">auto</b><span class="p-sub">Gateway picks best healthy provider</span></div>`;
  return auto+MODEL_CHOICES.slice(0,14).map(m=>`<div class="ui-pcard ${W.model===m?'sel':''}" data-model="${esc(m)}"><b style="font-size:12px">${esc(m)}</b></div>`).join('');
}

// ---------- collect / compile ----------
function bindInputs(root){
  root.querySelectorAll('[id^="w_"]').forEach(el=>{
    const k=el.id.slice(2);
    if(el.classList.contains('ui-seg')){
      el.querySelectorAll('button').forEach(b=>b.onclick=()=>{
        W[k]=b.dataset.v;el.querySelectorAll('button').forEach(x=>x.classList.toggle('sel',x===b));preview();});
      return;
    }
    if(el.type==='checkbox'){el.onchange=()=>{W[k]=el.checked;preview();};return;}
    if(el.type==='range'){
      el.oninput=()=>{
        const raw=+el.value;
        el.style.setProperty('--fill',(el.max==20?raw*5:raw*10)+'%');
        if(k==='temperature'){W[k]=raw/10;$('#wv_temperature').textContent=(raw/10).toFixed(1);}
        else{W[k]=raw;const v=$('#wv_'+k);if(v)v.textContent=raw;preview();}
      };return;
    }
    el.oninput=()=>{W[k]=el.value;if(k==='system_prompt'){const c=$('#w_spCount');if(c)c.textContent=el.value.length+' chars';}preview();};
  });
  root.querySelectorAll('[data-cat]').forEach(el=>el.onclick=()=>{
    W.category=el.dataset.cat;
    root.querySelectorAll('[data-cat]').forEach(x=>x.classList.toggle('sel',x===el));preview();});
  root.querySelectorAll('[data-preset]').forEach(el=>el.onclick=()=>{
    const p=PRESETS[+el.dataset.preset];
    Object.assign(W,p.vals);render();preview();
    toast(`${p.name} preset applied`);});
  root.querySelectorAll('[data-model]').forEach(el=>el.onclick=()=>{
    W.model=el.dataset.model;
    root.querySelectorAll('[data-model]').forEach(x=>x.classList.toggle('sel',x===el));preview();});
  const tg=root.querySelector('#w_toolGrid');
  if(tg){renderToolGrid(tg);}
  const ps=root.querySelector('#w_persSummary');if(ps)ps.textContent=persSummary();
  if(step===6){$('#w_reviewSummary').innerHTML=reviewSummary();$('#w_reviewPrompt').textContent=compilePrompt();}
}

function persSummary(){
  const t=[];
  if(W.communication_style)t.push(W.communication_style.toLowerCase());
  if(W.creativity>6)t.push('creative');else if(W.creativity<3)t.push('precise');
  if(W.formality>6)t.push('formal');else if(W.formality<3)t.push('casual');
  if(W.assertiveness>6)t.push('assertive');else if(W.assertiveness<3)t.push('gentle');
  if(W.empathy>6)t.push('empathetic');else if(W.empathy<3)t.push('objective');
  return t.length?`A ${t.join(', ')} assistant.`:'Adjust traits to see the personality profile…';
}
function reviewSummary(){
  const rows=[
    ['Name',W.name||'—'],['Category',W.category||'—'],['Model',W.model],
    ['Objective',W.objective||'—'],
    ['Style',[W.communication_style,W.tone].filter(Boolean).join(' · ')||'—'],
    ['Traits',`C${W.creativity} F${W.formality} A${W.assertiveness} E${W.empathy}`],
    ['Tools',W.enable_tools?'enabled':'off'],
    ['Visibility / status',`${W.visibility} · ${W.status}`],
  ];
  return rows.map(([k,v])=>`<div style="display:flex;gap:10px;padding:4px 0;font-size:12.5px"><span class="tx-3" style="width:110px;flex:none">${k}</span><b style="font-weight:500">${esc(v)}</b></div>`).join('');
}
/* Mirror of backend _compile_system_prompt */
function compilePrompt(){
  const parts=[];
  if(W.system_prompt)parts.push(W.system_prompt);
  if(W.detailed_description)parts.push(`## Your Purpose\n${W.detailed_description}`);
  if(W.objective)parts.push(`## Primary Objective\n${W.objective}`);
  if(W.problems)parts.push(`## Problems You Solve\n${W.problems}`);
  if(W.category)parts.push(`## Category\nYou are a ${W.category} agent.`);
  if(W.audience)parts.push(`## Target Audience\n${W.audience}`);
  if(W.domain)parts.push(`## Domain\n${W.domain}`);
  const traits=[];
  if(W.personality_type)traits.push(`Personality: ${W.personality_type}`);
  if(W.communication_style)traits.push(`Communication style: ${W.communication_style}`);
  if(W.tone)traits.push(`Tone: ${W.tone}`);
  if(W.response_length)traits.push(`Response length: ${W.response_length}`);
  if(W.language)traits.push(`Language: ${W.language}`);
  if(W.technical_detail)traits.push(`Technical detail level: ${W.technical_detail}`);
  traits.push(`Creativity: ${W.creativity}/10`,`Formality: ${W.formality}/10`,`Assertiveness: ${W.assertiveness}/10`,`Empathy: ${W.empathy}/10`);
  if(traits.length)parts.push('## Personality & Behavior\n- '+traits.join('\n- '));
  const flags=[];
  if(W.ask_clarifying)flags.push('Ask clarifying questions when the request is ambiguous.');
  if(W.explain_reasoning)flags.push('Explain your reasoning step by step.');
  if(W.provide_examples)flags.push('Provide examples to illustrate your points.');
  if(W.cite_sources)flags.push('Cite sources when making factual claims.');
  if(W.admit_uncertainty)flags.push('Admit uncertainty instead of guessing.');
  if(flags.length)parts.push('## Behavioral Rules\n- '+flags.join('\n- '));
  if(W.custom_rules)parts.push(`## Custom Rules\n${W.custom_rules}`);
  if(W.constraints)parts.push(`## Constraints\n${W.constraints}`);
  if(W.knowledge)parts.push(`## Knowledge Base\n${W.knowledge}`);
  if(W.decision_making)parts.push(`## Decision Making\n${W.decision_making}`);
  if(W.error_handling)parts.push(`## Error Handling\n${W.error_handling}`);
  if(W.max_retries)parts.push(`Max retries: ${W.max_retries}`);
  if(W.enable_tools)parts.push('You may use tool/function calls when helpful.');
  return parts.join('\n\n')||'You are a helpful assistant.';
}
function collectBody(){
  return {
    name:W.name.trim(),description:W.description.trim(),system_prompt:W.system_prompt,
    model:W.model||'auto',
    temperature:W.temperature===''?null:parseFloat(W.temperature),
    max_tokens:W.max_tokens===''?null:+W.max_tokens,
    enable_tools:W.enable_tools,output_format:W.output_format,
    enabled:W.status!=='disabled',
    config:{
      basic:{detailed_description:W.detailed_description,category:W.category,domain:W.domain,
        primary_objective:W.objective,problems:W.problems,target_audience:W.audience,
        tags:W.tags,visibility:W.visibility,status:W.status},
      personality:{personality_type:W.personality_type,communication_style:W.communication_style,
        tone:W.tone,response_length:W.response_length,language:W.language,
        technical_detail:W.technical_detail,creativity:W.creativity,formality:W.formality,
        assertiveness:W.assertiveness,empathy:W.empathy,ask_clarifying:W.ask_clarifying,
        explain_reasoning:W.explain_reasoning,provide_examples:W.provide_examples,
        cite_sources:W.cite_sources,admit_uncertainty:W.admit_uncertainty},
      instructions:{custom_rules:W.custom_rules,constraints:W.constraints,knowledge:W.knowledge},
      execution:{decision_making:W.decision_making,error_handling:W.error_handling,
        max_retries:W.max_retries===''?null:+W.max_retries,timeout:W.timeout===''?null:+W.timeout},
      capabilities:{tools_enabled:W.enable_tools,tools:W.tools},
    },
  };
}

// ---------- render ----------
function render(){
  $('#wizRail').innerHTML=STEPS.map((s,i)=>`
    <div class="wiz-step-item ${i+1===step?'active':i+1<step?'done':''}" data-step="${i+1}">
      <div class="n"><span class="num">${i+1}</span></div>
      <div><div class="sl">${s.ico} ${s.t}</div><div class="sd">${s.d}</div></div>
    </div>`).join('');
  $('#wizRail').querySelectorAll('.wiz-step-item').forEach(el=>el.onclick=()=>{
    const s=+el.dataset.step;if(s<step){step=s;render();}});
  $('#wizBar').style.width=Math.round(step/STEPS.length*100)+'%';
  $('#wizPct').textContent=`Step ${step} of ${STEPS.length} · ${Math.round(step/STEPS.length*100)}%`;
  $('#wizBody').innerHTML=BODIES[STEPS[step-1].id]();
  $('#wizBody').scrollTop=0;
  bindInputs($('#wizBody'));
  $('#wizPrev').disabled=step===1;
  $('#wizNext').style.display=step===STEPS.length?'none':'inline-flex';
  $('#wizSubmit').style.display=step===STEPS.length?'inline-flex':'none';
  $('#wizSubmit').textContent=editId?'✓ Update agent':'✓ Create agent';
  $('#wizTitle').textContent=editId?'Edit Agent':'New Agent';
}
function preview(){
  const av=W.name?W.name.slice(0,2).toUpperCase():'🤖';
  $('#wizPreview').innerHTML=`
    <div style="text-align:center;padding:8px 0 14px">
      <div style="width:56px;height:56px;border-radius:16px;margin:0 auto 10px;display:grid;place-items:center;font-size:20px;font-weight:700;color:#fff;background:linear-gradient(135deg,var(--accent),var(--accent-2));box-shadow:var(--shadow-3)">${esc(av)}</div>
      <div style="font-weight:600;font-size:15px">${esc(W.name||'Unnamed agent')}</div>
      <div class="t-xs tx-3">${esc(W.description||'No description yet')}</div>
    </div>
    <div style="display:flex;gap:5px;flex-wrap:wrap;justify-content:center;margin-bottom:14px">
      ${W.category?`<span class="pill info">${esc(W.category)}</span>`:''}
      <span class="pill mut mono">${esc(W.model)}</span>
      ${W.enable_tools?'<span class="pill ok">tools</span>':''}
      ${W.status!=='active'?`<span class="pill warn">${W.status}</span>`:''}
    </div>
    <div class="ui-panel" style="padding:11px 13px;margin-bottom:10px">
      <div class="t-xs tx-3" style="margin-bottom:5px">Personality</div>
      <div class="t-sm" style="font-style:italic">${esc(persSummary())}</div>
    </div>
    <div id="wizTestOut" style="font-size:12.5px;line-height:1.5"></div>`;
}
async function wizTest(){
  const msg=$('#wizTestInput').value.trim();if(!msg)return;
  const out=$('#wizTestOut');
  out.innerHTML+=`<div style="margin:8px 0;padding:8px 10px;border-radius:9px;background:var(--accent-soft);border:1px solid var(--accent-line)"><b class="t-xs tx-3">you</b><div>${esc(msg)}</div></div>`;
  $('#wizTestInput').value='';
  const load=document.createElement('div');
  load.style.cssText='margin:8px 0;padding:8px 10px;border-radius:9px;background:var(--surface-2);border:1px solid var(--border-1)';
  load.innerHTML='<i class="tx-3 t-xs">thinking…</i>';out.appendChild(load);
  try{
    const r=await fetch(API_BASE+'/v1/chat/completions',{method:'POST',headers:apiHeaders(),
      body:JSON.stringify({model:W.model||'auto',
        messages:[{role:'system',content:compilePrompt()},{role:'user',content:msg}],stream:false})});
    const j=await r.json();
    const txt=j.choices?.[0]?.message?.content||j.error?.message||'…';
    load.innerHTML=`<b class="t-xs tx-3">agent</b><div>${esc(txt.slice(0,600))}</div>`;
  }catch(e){load.innerHTML=`<b class="t-xs" style="color:var(--error)">error</b><div class="t-xs tx-3">${esc(e.message)}</div>`;}
  $('#wizPreview').scrollTop=$('#wizPreview').scrollHeight;
}

async function loadTools(){
  try{
    const r=await fetch(API_BASE+'/admin/tools',{headers:apiHeaders(true)});
    if(r.ok)AVAILABLE_TOOLS=await r.json();
  }catch(_){}
  if(!AVAILABLE_TOOLS.length)AVAILABLE_TOOLS=[
    {name:'calculator',icon:'🧮',description:'Evaluate math expressions'},
    {name:'get_datetime',icon:'🕐',description:'Current date/time in UTC'},
    {name:'web_search',icon:'🌐',description:'Search the web (DuckDuckGo)'},
    {name:'http_get',icon:'📡',description:'Fetch a URL over HTTP GET'},
  ];
}
function renderToolGrid(tg){
  if(!AVAILABLE_TOOLS.length){tg.innerHTML='<span class="tx-3 t-sm">No tools available.</span>';return;}
  tg.innerHTML=AVAILABLE_TOOLS.map(t=>{
    const on=W.tools.includes(t.name);
    return `<div class="ui-pcard ${on?'sel':''}" data-tool="${esc(t.name)}" style="cursor:pointer">
      <div class="p-ic" style="background:${on?'linear-gradient(135deg,#6366f1,#8b5cf6)':'var(--surface-3)'}">${t.icon}</div>
      <b>${esc(t.name)}</b>
      <span class="p-sub">${esc(t.description)}</span>
      <span class="pill ${on?'ok':'mut'}" style="align-self:flex-start">${on?'enabled':'off'}</span>
    </div>`;}).join('');
  tg.querySelectorAll('[data-tool]').forEach(el=>el.onclick=()=>{
    const n=el.dataset.tool;
    W.tools=W.tools.includes(n)?W.tools.filter(x=>x!==n):[...W.tools,n];
    W.enable_tools=W.tools.length>0;
    renderToolGrid(tg);preview();
  });
}

// ---------- open / populate / submit ----------
function openWizard(agent){
  editId=agent?agent.id:null;
  W=blank();
  if(agent){
    const c=agent.config||{},b=c.basic||{},p=c.personality||{},i=c.instructions||{},e=c.execution||{},cap=c.capabilities||{};
    Object.assign(W,{
      name:agent.name,description:agent.description,system_prompt:agent.system_prompt,
      model:agent.model||'auto',temperature:agent.temperature??'',max_tokens:agent.max_tokens??'',
      output_format:agent.output_format||'text',enable_tools:!!agent.enable_tools,
      status:agent.enabled?'active':'disabled',
      detailed_description:b.detailed_description||'',category:b.category||'',domain:b.domain||'',
      objective:b.primary_objective||'',problems:b.problems||'',audience:b.target_audience||'',
      tags:b.tags||'',visibility:b.visibility||'private',
      personality_type:p.personality_type||'',communication_style:p.communication_style||'',
      tone:p.tone||'',response_length:p.response_length||'',language:p.language||'',
      technical_detail:p.technical_detail||'',
      creativity:p.creativity??5,formality:p.formality??5,assertiveness:p.assertiveness??5,empathy:p.empathy??5,
      ask_clarifying:!!p.ask_clarifying,explain_reasoning:!!p.explain_reasoning,
      provide_examples:!!p.provide_examples,cite_sources:!!p.cite_sources,admit_uncertainty:!!p.admit_uncertainty,
      custom_rules:i.custom_rules||'',constraints:i.constraints||'',knowledge:i.knowledge||'',
      decision_making:e.decision_making||'',error_handling:e.error_handling||'',
      max_retries:e.max_retries??'',timeout:e.timeout??'',
      tools:Array.isArray(cap.tools)?cap.tools:[],
    });
  }
  step=1;
  loadModelChoices();loadTools();
  $('#agentModal').style.display='grid';
  render();preview();
}
async function loadModelChoices(){
  try{
    const r=await fetch(API_BASE+'/v1/models',{headers:apiHeaders()});
    if(r.ok){const d=(await r.json()).data||[];MODEL_CHOICES=d.map(m=>m.id).filter(id=>id!=='auto');}
  }catch(_){}
}
async function submit(){
  if(!W.name.trim()){toast('Agent name required','err');step=1;render();return;}
  const body=collectBody();
  const url=editId?API_BASE+'/admin/agents/'+editId:API_BASE+'/admin/agents';
  if(editId)delete body.name;
  let r=await fetch(url,{method:editId?'PATCH':'POST',headers:apiHeaders(true),body:JSON.stringify(body)});
  if(r.status===401){
    const t=prompt('Admin token required (ADMIN_TOKEN):');
    if(t){ADMIN_TOKEN=t;sessionStorage.setItem('admin_token',t);
      r=await fetch(url,{method:editId?'PATCH':'POST',headers:apiHeaders(true),body:JSON.stringify(body)});}
  }
  if(r.ok){
    const a=await r.json();
    $('#agentModal').style.display='none';
    selectAgent(a);
    if(window.refreshAgents)window.refreshAgents();
    toast(editId?`Agent "${a.name}" updated`:`Agent "${a.name}" created`,'ok');
    editId=null;
  }else{
    const t=await r.text();toast('Error: '+t.slice(0,120),'err');
  }
}

// ---------- wire ----------
$('#agentNewBtn').onclick=()=>openWizard(null);
$('#agentEditBtn').onclick=async()=>{
  const st=window.__chatState;
  if(!st||!st.agent)return;
  let r=await fetch(API_BASE+'/admin/agents/'+st.agent.id,{headers:apiHeaders(true)});
  if(r.status===401){
    const t=prompt('Admin token required (ADMIN_TOKEN):');
    if(t){ADMIN_TOKEN=t;sessionStorage.setItem('admin_token',t);
      r=await fetch(API_BASE+'/admin/agents/'+st.agent.id,{headers:apiHeaders(true)});}
  }
  if(r.ok)openWizard(await r.json());else toast('Load failed','err');
};
$('#wizPrev').onclick=()=>{if(step>1){step--;render();}};
$('#wizNext').onclick=()=>{if(step===1&&!W.name.trim()){toast('Give the agent a name first','err');return;}
  if(step<STEPS.length){step++;render();}};
$('#wizCancel').onclick=()=>{$('#agentModal').style.display='none';editId=null;};
$('#wizSubmit').onclick=submit;
$('#wizTestBtn').onclick=wizTest;
$('#wizTestInput').addEventListener('keydown',e=>{if(e.key==='Enter')wizTest();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('#agentModal').style.display!=='none')$('#agentModal').style.display='none';});
})();
