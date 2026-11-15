/* Skills page — built-in catalog + custom skill CRUD. */
(function(){
  const $=s=>document.querySelector(s);
  let editId=null;
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
          <td class="tx-3" style="font-size:var(--t-xs)">${(s.requires_tools||s.tools||[]).join(', ')}</td>`;
        tb.appendChild(tr);
      });
      const c=await v1Api('/v1/skills/manage');
      const ct=$('#custom tbody');ct.innerHTML='';
      (c.skills||[]).forEach(s=>{
        const tr=document.createElement('tr');
        tr.innerHTML=`<td><b>${esc(s.name)}</b></td><td>${s.version}</td>
          <td class="tx-2">${esc(s.description)}</td>
          <td class="tx-3" style="font-size:var(--t-xs)">${(s.tools||[]).join(', ')}</td>
          <td>${s.enabled?'<span class="pill ok">on</span>':'<span class="pill mut">off</span>'}</td>
          <td style="text-align:right"><button class="ui-btn ui-btn-sm" data-e>Edit</button>
          <button class="ui-btn ui-btn-sm ui-btn-danger" data-d>Del</button></td>`;
        tr.querySelector('[data-e]').onclick=()=>openModal(s);
        tr.querySelector('[data-d]').onclick=async()=>{if(confirm('Delete '+s.name+'?')){await v1Api('/v1/skills/manage/'+s.id,{method:'DELETE'});load();}};
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
    $('#modal').style.display='grid';
  }

  async function save(){
    const body={name:$('#sk_name').value.trim(),description:$('#sk_desc').value.trim(),
      instructions:$('#sk_ins').value,
      tools:$('#sk_tools').value.split(',').map(x=>x.trim()).filter(Boolean)};
    try{
      if(editId)await v1Api('/v1/skills/manage/'+editId,{method:'PATCH',body:JSON.stringify(body)});
      else await v1Api('/v1/skills/manage',{method:'POST',body:JSON.stringify(body)});
      $('#modal').style.display='none';toast('Saved');load();
    }catch(e){toast(e.message,'err');}
  }
})();
