/* Knowledge Library — collections, uploads, hybrid retrieval. */
(function(){
  const $=s=>document.querySelector(s);
  AdminShell.mount('library');
  window.addEventListener('admin-ready',init);
  if(ADMIN_TOKEN)init();

  function init(){
    $('#addCol').onclick=addCollection;
    $('#upBtn').onclick=upload;
    $('#searchBtn').onclick=search;
    $('#sq').addEventListener('keydown',e=>{if(e.key==='Enter')search();});
    loadCols();loadDocs();
  }

  async function loadCols(){
    try{
      const d=await v1Api('/v1/knowledge/collections');
      const host=$('#cols');host.innerHTML='';
      const sel1=$('#scol'),sel2=$('#dcol');
      sel1.innerHTML='<option value="">All collections</option>';
      sel2.innerHTML='<option value="">No collection</option>';
      (d.collections||[]).forEach(c=>{
        const o1=document.createElement('option');o1.value=c.id;o1.textContent=c.name;
        const o2=o1.cloneNode(true);sel1.appendChild(o1);sel2.appendChild(o2);
        const el=document.createElement('div');
        el.className='ui-navitem';el.style.justifyContent='space-between';
        el.innerHTML=`<span>${esc(c.name)} <span class="tx-3" style="font-size:10px">${c.documents} docs</span></span>
          <button class="ui-btn ui-btn-ghost ui-btn-icon" style="padding:2px 6px">✕</button>`;
        el.querySelector('button').onclick=async()=>{if(confirm('Delete collection?')){await v1Api('/v1/knowledge/collections/'+c.id,{method:'DELETE'});loadCols();loadDocs();}};
        host.appendChild(el);
      });
    }catch(e){toast(e.message,'err');}
  }

  async function addCollection(){
    const name=$('#cname').value.trim();if(!name)return;
    try{await v1Api('/v1/knowledge/collections',{method:'POST',body:JSON.stringify({name})});
      $('#cname').value='';loadCols();toast('Collection created');}catch(e){toast(e.message,'err');}
  }

  async function loadDocs(){
    try{
      const d=await v1Api('/v1/knowledge/documents');
      const tb=$('#docs tbody');tb.innerHTML='';
      (d.documents||[]).forEach(doc=>{
        const tr=document.createElement('tr');
        const st=doc.status==='ready'?'<span class="pill ok">ready</span>'
          :doc.status==='error'?`<span class="pill" style="color:var(--error)">error</span>`
          :'<span class="pill mut">processing</span>';
        tr.innerHTML=`<td>${esc(doc.name)}</td><td class="tx-3">${esc(doc.mime)}</td>
          <td class="tx-3">${(doc.size/1024).toFixed(0)} KB</td>
          <td class="tx-3">${doc.meta?.chunks??'—'}</td><td>${st}</td>
          <td style="text-align:right"><button class="ui-btn ui-btn-sm ui-btn-danger">Del</button></td>`;
        tr.querySelector('button').onclick=async()=>{await v1Api('/v1/knowledge/documents/'+doc.id,{method:'DELETE'});loadDocs();};
        tb.appendChild(tr);
      });
      if(!(d.documents||[]).length)tb.innerHTML='<tr><td colspan="6" class="tx-3" style="text-align:center;padding:18px">No documents yet — upload one above.</td></tr>';
    }catch(e){toast(e.message,'err');}
  }

  async function upload(){
    const f=$('#file').files[0];if(!f)return;
    const fd=new FormData();fd.append('file',f);
    const col=$('#dcol').value;if(col)fd.append('collection_id',col);
    try{
      const r=await fetch(API_BASE+'/v1/knowledge/documents',{method:'POST',
        headers:{'Authorization':'Bearer '+API_KEY,'X-User-Token':USER_TOKEN},body:fd});
      const j=await r.json();
      if(!r.ok)throw new Error(j.detail||'upload failed');
      $('#file').value='';loadDocs();toast(j.status==='ready'?'Ingested':'Uploaded ('+j.status+')');
      setTimeout(loadDocs,1500);
    }catch(e){toast(e.message,'err');}
  }

  async function search(){
    const q=$('#sq').value.trim();if(!q)return;
    try{
      const col=$('#scol').value;
      const d=await v1Api('/v1/knowledge/search?q='+encodeURIComponent(q)+(col?'&collection_id='+col:''));
      const host=$('#hits');host.innerHTML='';
      (d.results||[]).forEach(r=>{
        const el=document.createElement('div');
        el.className='lb-hit';
        el.innerHTML=`<div class="tx-3" style="font-size:10px;margin-bottom:4px">${esc(r.document)} · chunk ${r.ord} · score ${r.score}</div>
          <div style="font-size:var(--t-sm);line-height:1.5">${esc(r.text.slice(0,500))}${r.text.length>500?'…':''}</div>`;
        host.appendChild(el);
      });
      if(!(d.results||[]).length)host.innerHTML='<div class="tx-3 t-sm" style="padding:8px 0 16px">No matches.</div>';
    }catch(e){toast(e.message,'err');}
  }
})();
