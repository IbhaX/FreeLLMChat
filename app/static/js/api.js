/* Shared API helpers — gateway connection + admin auth + toast. */
(function(){
  const $=s=>document.querySelector(s);
  window.$=$;
  window.API_BASE=localStorage.getItem('gateway_url')||'';
  window.ADMIN_TOKEN=sessionStorage.getItem('admin_token')||'';

  window.USER_TOKEN=localStorage.getItem('nova_user_token')||'';
  window.apiHeaders=(admin)=>admin
    ?{'X-Admin-Token':ADMIN_TOKEN,'X-User-Token':USER_TOKEN,'Content-Type':'application/json'}
    :{'X-User-Token':USER_TOKEN,'Content-Type':'application/json'};

  window.v1Api=async(path,opts={})=>{
    const r=await fetch(API_BASE+path,{...opts,headers:{...apiHeaders(false),...(opts.headers||{})}});
    if(r.status===204)return null;
    const j=await r.json().catch(()=>({}));
    if(r.status===401&&window.NovaAuth&&NovaAuth.on401(j))throw Object.assign(new Error('Authentication required'),{status:401,body:j});
    if(!r.ok)throw Object.assign(new Error(j.detail?.error?.message||j.detail||j.error?.message||('HTTP '+r.status)),{status:r.status,body:j});
    return j;
  };

  window.adminApi=async(path,opts={})=>{
    const r=await fetch(API_BASE+path,{...opts,headers:{...apiHeaders(true),...(opts.headers||{})}});
    if(r.status===204)return null;
    const j=await r.json().catch(()=>({}));
    const type=(j.detail&&j.detail.error&&j.detail.error.type)||'';
    if(r.status===401&&type==='authentication_required'){
      // No user session → the login gate owns the redirect.
      if(!(window.NovaAuth&&NovaAuth.on401(j)))
        location.href='/login?next='+encodeURIComponent(location.pathname+location.search);
      throw Object.assign(new Error('Authentication required'),{status:401,body:j});
    }
    if(r.status===403&&type==='admin_required'){
      // Signed in but not admin — pages render a restricted state instead
      // of bouncing to /chat; background probes pass noRedirect anyway.
      window.dispatchEvent(new Event('admin-auth-fail'));
      throw Object.assign(new Error('Admin role required'),{status:403,body:j});
    }
    if(r.status===401){window.dispatchEvent(new Event('admin-auth-fail'));throw new Error('unauthorized');}
    if(!r.ok)throw Object.assign(new Error(j.detail?.error?.message||j.detail||('HTTP '+r.status)),{status:r.status,body:j});
    return j;
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
