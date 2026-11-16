/* Skills page — built-in catalog + custom skill CRUD. */
(function(){
  const $=s=>document.querySelector(s);
  let editId=null,customSkills=[],availTools=[];
  AdminShell.mount('skills');
  window.addEventListener('admin-ready',init);
  if(ADMIN_TOKEN)init();

  async function init(){
    $('#newBtn').onclick=()=>openModal(null);
    $('#saveBtn').onclick=save;
    try{availTools=await adminApi('/admin/tools')||[];}catch(e){availTools=[];}
    renderToolPicker([]);
    load();
  }

  function renderToolPicker(checked){
    const host=$('#sk_tools_list');host.innerHTML='';
    const sel=new Set(checked||[]);
    availTools.forEach(t=>{
      const lbl=document.createElement('label');
      lbl.title=t.description||'';
      lbl.style.cssText='display:flex;gap:5px;align-items:center;padding:3px 10px;'+
        'border:1px solid var(--border);border-radius:20px;cursor:pointer;'+
        'font-size:var(--t-xs);user-select:none';
      lbl.innerHTML=`<input type="checkbox" value="${esc(t.name)}" ${sel.has(t.name)?'checked':''} style="margin:0">
        <span>${esc(t.icon||'')} ${esc(t.name)}</span>`;
      host.appendChild(lbl);
    });
    if(!availTools.length)
      host.innerHTML='<span class="tx-3" style="font-size:var(--t-xs)">tool registry unavailable — type names below</span>';
  }

  async function load(){
    try{
      const d=await v1Api('/v1/skills');
      const tb=$('#builtin tbody');tb.innerHTML='';
      (d.skills||d||[]).forEach(s=>{
        if(s.custom)return;
        const tr=document.createElement('tr');
        tr.innerHTML=`<td><b>${esc(s.name)}</b></td><td>${esc(s.version||'')}</td>
          <td class="tx-2">${esc(s.description||'')}</td>
          <td class="tx-3" style="font-size:var(--t-xs)">${(s.requires_tools||s.tools||[]).join(', ')}</td>
          <td style="text-align:right"><button class="ui-btn ui-btn-ghost ui-btn-sm" data-c>customize</button></td>`;
        tr.querySelector('[data-c]').onclick=()=>{
          const existing=customSkills.find(x=>x.name===s.name);
          openModal(existing||{
            id:null,name:s.name,description:s.description,
            instructions:s.instructions||'',
            tools:s.requires_tools||s.tools||[]});};
        tb.appendChild(tr);
      });
      const c=await v1Api('/v1/skills/manage');
      customSkills=c.skills||[];
      const ct=$('#custom tbody');ct.innerHTML='';
      const builtins=new Set((d.skills||d||[])
        .filter(x=>!x.custom||x.overrides_builtin).map(x=>x.name));
      (c.skills||[]).forEach(s=>{
        const isOverride=builtins.has(s.name);
        const tr=document.createElement('tr');
        tr.innerHTML=`<td><b>${esc(s.name)}</b>${isOverride?' <span class="pill" style="font-size:9px">overrides built-in</span>':''}</td><td>${s.version}</td>
          <td class="tx-2">${esc(s.description)}</td>
          <td class="tx-3" style="font-size:var(--t-xs)">${(s.tools||[]).join(', ')}</td>
          <td><button class="ui-btn ui-btn-ghost ui-btn-sm" data-t>${s.enabled?'on':'off'}</button></td>
          <td style="text-align:right"><button class="ui-btn ui-btn-sm" data-e>Edit</button>
          <button class="ui-btn ui-btn-sm ui-btn-danger" data-d>${isOverride?'Restore':'Del'}</button></td>`;
        tr.querySelector('[data-e]').onclick=()=>openModal(s);
        tr.querySelector('[data-t]').onclick=async()=>{
          await v1Api('/v1/skills/manage/'+s.id,{method:'PATCH',
            body:JSON.stringify({name:s.name,description:s.description,
              instructions:s.instructions,tools:s.tools,enabled:!s.enabled})});
          load();};
        tr.querySelector('[data-d]').onclick=async()=>{
          const msg=isOverride
            ?'Remove your override and restore the built-in '+s.name+'?'
            :'Delete '+s.name+'?';
          if(confirm(msg)){await v1Api('/v1/skills/manage/'+s.id,{method:'DELETE'});load();}};
        ct.appendChild(tr);
      });
      if(!(c.skills||[]).length)ct.innerHTML='<tr><td colspan="6" class="tx-3" style="text-align:center;padding:18px">No custom skills yet.</td></tr>';
    }catch(e){toast(e.message,'err');}
  }

  function openModal(s){
    editId=s?s.id:null;
    $('#mtitle').textContent=s?('Edit '+s.name):'New skill';
    $('#sk_name').value=s?s.name:'';
    $('#sk_desc').value=s?s.description:'';
    $('#sk_ins').value=s?s.instructions:'';
    const known=new Set(availTools.map(t=>t.name));
    const saved=s?s.tools||[]:[];
    renderToolPicker(saved.filter(t=>known.has(t)));
    $('#sk_tools').value=saved.filter(t=>!known.has(t)).join(', ');
    $('#sk_enabled').checked=s?s.enabled!==false:true;
    $('#modal').style.display='grid';
  }

  async function save(){
    const picked=[...document.querySelectorAll('#sk_tools_list input:checked')].map(c=>c.value);
    const extras=$('#sk_tools').value.split(',').map(x=>x.trim()).filter(Boolean);
    const body={name:$('#sk_name').value.trim(),description:$('#sk_desc').value.trim(),
      instructions:$('#sk_ins').value,enabled:$('#sk_enabled').checked,
      tools:[...new Set([...picked,...extras])]};
    try{
      if(editId)await v1Api('/v1/skills/manage/'+editId,{method:'PATCH',body:JSON.stringify(body)});
      else await v1Api('/v1/skills/manage',{method:'POST',body:JSON.stringify(body)});
      $('#modal').style.display='none';toast('Saved');load();
    }catch(e){toast(e.message,'err');}
  }
})();
