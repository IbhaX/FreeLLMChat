/* Razorpay subscriptions — plan cards → create-subscription → modal →
   server-side signature verify. Prices always come from the backend. */
(function(){
'use strict';
const $=window.$;
const grid=$('#plans'),
      state=$('#payState'),stateText=$('#payStateText'),ref=$('#payRef');

let busy=false,keyId='',rate=83,currentPlan='free';
const sdkOk=()=>typeof window.Razorpay==='function';

function setState(kind,text){
  state.hidden=false;
  state.className='pay-state'+(kind?' '+kind:'');
  stateText.textContent=text;
}
function setRef(text){
  ref.hidden=!text;ref.textContent=text||'';
}
function setBusy(on){
  busy=on;
  grid.querySelectorAll('button').forEach(b=>{b.disabled=on;});
}
const usd=c=>c?(c/100).toLocaleString('en-US',
  {style:'currency',currency:'USD',maximumFractionDigits:0}):'Free';
const inr=p=>'₹'+Math.round(p/100).toLocaleString('en-IN');

const RANK=['free','basic','plus','pro','ultra'];

function render(plans){
  grid.innerHTML='';
  const curRank=RANK.indexOf(currentPlan);
  plans.forEach((p,i)=>{
    const card=document.createElement('div');
    card.className='plan'+(p.id==='pro'?' best':'');
    card.innerHTML=
      (p.id==='pro'?'<span class="tag">POPULAR</span>':'')+
      `<div class="pname">${esc(p.name)}</div>`+
      `<div class="price"><b>${p.usd_cents?usd(p.usd_cents):'$0'}</b>`+
      `<span>/mo</span></div>`+
      (p.usd_cents?`<div class="inr">≈ ${inr(p.amount_paise)} INR/mo</div>`
        :'<div class="inr"></div>')+
      '<div class="pdiv"></div>'+
      '<ul>'+p.features.map(f=>`<li>${esc(f)}</li>`).join('')+'</ul>';
    if(p.usd_cents){
      if(p.id===currentPlan){
        const n=document.createElement('div');
        n.className='free-note';n.textContent='Current plan';
        card.appendChild(n);
      }else if(curRank>=0&&RANK.indexOf(p.id)<curRank){
        /* Lower tiers are already covered by the current plan — offer
           upgrades only, no downgrade checkout. */
        const n=document.createElement('div');
        n.className='free-note';n.textContent='Included';
        card.appendChild(n);
      }else{
        const b=document.createElement('button');
        b.className='ui-btn ui-btn-primary ui-btn-sm';
        b.textContent='Subscribe';
        b.onclick=()=>subscribe(p);
        card.appendChild(b);
      }
    }else{
      const n=document.createElement('div');
      n.className='free-note';
      n.textContent=p.id===currentPlan?'Current plan':'Included';
      card.appendChild(n);
    }
    grid.appendChild(card);
  });
}

async function verifyPayment(resp){
  setState('busy','Verifying payment with the server…');
  const body={
    razorpay_payment_id:resp.razorpay_payment_id,
    razorpay_signature:resp.razorpay_signature};
  if(resp.razorpay_subscription_id)
    body.razorpay_subscription_id=resp.razorpay_subscription_id;
  else body.razorpay_order_id=resp.razorpay_order_id;
  const out=await v1Api('/api/verify-payment',{method:'POST',
    body:JSON.stringify(body)});
  if(out&&out.success){
    setState('ok','Payment verified — '+
      (out.plan?out.plan[0].toUpperCase()+out.plan.slice(1)+' plan active.'
               :'subscription active.'));
    if(out.plan){currentPlan=out.plan;}
    setRef('payment '+out.payment_id+
      (out.subscription_id?' · '+out.subscription_id:' · '+out.order_id));
    toast('Subscription activated','ok');
  }else{
    setState('err','Verification did not confirm this payment.');
  }
}

async function subscribe(p){
  if(busy)return;
  setRef('');state.hidden=true;
  if(!sdkOk()){
    setState('err','The payment SDK failed to load — check your '+
      'connection and reload.');
    return;
  }
  if(!keyId){
    setState('err','Payments are not configured on this server.');
    return;
  }
  setBusy(true);
  setState('busy','Creating subscription…');
  try{
    const sub=await v1Api('/api/create-subscription',{method:'POST',
      body:JSON.stringify({plan_id:p.id})});
    setState('busy','Waiting for checkout…');
    const rzp=new window.Razorpay({
      key:keyId,
      subscription_id:sub.subscription_id,
      name:'iChat '+p.name,
      description:p.name+' plan — '+usd(p.usd_cents)+'/mo ('+
        inr(sub.amount)+' INR)',
      handler:async function(resp){
        try{await verifyPayment(resp);}
        catch(e){setState('err',e.message||'Verification request failed.');}
        finally{setBusy(false);}
      },
      modal:{ondismiss:function(){
        setState('','Checkout cancelled — nothing was charged.');
        setBusy(false);
      }},
    });
    rzp.on('payment.failed',function(resp){
      const desc=resp&&resp.error&&resp.error.description;
      setState('err','Payment failed'+(desc?': '+desc:'.'));
      setBusy(false);
    });
    rzp.open();
  }catch(e){
    if(!navigator.onLine)
      setState('err','You are offline — reconnect and try again.');
    else
      setState('err',e.message||'Could not start checkout.');
    setBusy(false);
  }
}

Promise.all([
  v1Api('/api/razorpay/config'),
  v1Api('/api/plans'),
  v1Api('/api/entitlements').catch(()=>null),
]).then(([c,pl,ent])=>{
  keyId=(c&&c.key_id)||'';
  rate=(pl&&pl.usd_to_inr)||83;
  if(ent&&ent.plan)currentPlan=ent.plan;
  if(!keyId){
    setState('err','Payments are not configured on this server.');
    grid.innerHTML='';
    return;
  }
  render(pl.plans||[]);
  $('#payNote').hidden=!String(keyId).startsWith('rzp_test_');
}).catch(e=>{
  grid.innerHTML='';
  setState('err',e.message||'Could not load billing configuration.');
});
})();
