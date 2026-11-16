/* Skills page — built-in catalog + custom skill CRUD. */
(function(){
  const $=s=>document.querySelector(s);
  let editId=null,customSkills=[];
  AdminShell.mount('skills');
  window.addEventListener('admin-ready',init);
  if(ADMIN_TOKEN)init();

  function init(){
    $('#newBtn').onclick=()=>openModal(null);
    $('#saveBtn').onclick=save;
    load();
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
          <td class="tx-3" style="font-size:var(--t-xs)">${(s.requires_tools||s.tools||[]).join(', ')}
            <button class="ui-btn ui-btn-ghost ui-btn-sm" data-c style="margin-left:8px">customize</button></td>`;
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
    $('#sk_tools').value=(s?s.tools:[]).join(', ');
    $('#sk_enabled').checked=s?s.enabled!==false:true;
    $('#modal').style.display='grid';
  }

  async function save(){
    const body={name:$('#sk_name').value.trim(),description:$('#sk_desc').value.trim(),
      instructions:$('#sk_ins').value,enabled:$('#sk_enabled').checked,
      tools:$('#sk_tools').value.split(',').map(x=>x.trim()).filter(Boolean)};
    try{
      if(editId)await v1Api('/v1/skills/manage/'+editId,{method:'PATCH',body:JSON.stringify(body)});
      else await v1Api('/v1/skills/manage',{method:'POST',body:JSON.stringify(body)});
      $('#modal').style.display='none';toast('Saved');load();
    }catch(e){toast(e.message,'err');}
  }
})();
