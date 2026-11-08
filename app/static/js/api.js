/* Shared API helpers — gateway connection + admin auth + toast. */
(function(){
  const $=s=>document.querySelector(s);
  window.$=$;
  window.API_BASE=localStorage.getItem('gateway_url')||'';
  window.API_KEY=localStorage.getItem('gateway_key')||'';
  window.ADMIN_TOKEN=sessionStorage.getItem('admin_token')||'';

  window.apiHeaders=(admin)=>admin
    ?{'X-Admin-Token':ADMIN_TOKEN,'Content-Type':'application/json'}
    :{'Authorization':'Bearer '+API_KEY,'Content-Type':'application/json'};

  window.adminApi=async(path,opts={})=>{
    const r=await fetch(API_BASE+path,{...opts,headers:apiHeaders(true)});
    if(r.status===401){window.dispatchEvent(new Event('admin-auth-fail'));throw new Error('unauthorized');}
    if(r.status===204)return null;
    return r.json();
  };

  window.askGatewayKey=()=>{
    const k=prompt('Enter GATEWAY_API_KEY for this server:');
    if(k){API_KEY=k;localStorage.setItem('gateway_key',k);return true;}
    return false;
  };

  // ---- toast ----
  let toastT;
  window.toast=(msg,kind)=>{
    let t=$('#ui-toast');
    if(!t){t=document.createElement('div');t.id='ui-toast';t.className='ui-toast';document.body.appendChild(t);}
    t.className='ui-toast show'+(kind?' '+kind:'');
    t.textContent=msg;
    clearTimeout(toastT);toastT=setTimeout(()=>t.classList.remove('show'),2000);
  };

  window.esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
})();
