/* Memory Explorer — living 3D neural brain over Astra DB memory.
   Three.js WebGL: anatomical two-hemisphere volume, instanced glowing
   neurons (real Astra docs = bright nodes, faint ambient fill for
   density), stored conversation links (gold) vs computed similarity
   links (slate), traveling synaptic pulses, periodic firing bursts.
   All controls/data preserved: search, category chips, similarity
   threshold, labels/edges toggles, focus, graph/list, detail card. */
(function(){
  const $=s=>document.querySelector(s);
  AdminShell.mount('memory');
  window.addEventListener('admin-ready',init);
  if(ADMIN_TOKEN)init();

  const THREE_URL='https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js';
  const CATS={
    personal: {c:'#4EA5FF',label:'Personal'},
    assistant:{c:'#A78BFA',label:'Assistant'},
    knowledge:{c:'#34D399',label:'Knowledge'},
    system:   {c:'#F5D547',label:'System'},
  };
  const EDGE_COLORS={conversation:'#F5D547',similarity:'#8CA0C8'};
  const AMBIENT=650;          // decorative filler neurons (not data)
  const MAX_PULSES=56;
  const rm=()=>window.NovaMotion&&NovaMotion.reduced();
  const G=()=>window.gsap;

  const S={
    nodes:[],edges:[],adj:new Map(),byId:new Map(),
    hover:null,sel:null,cats:new Set(Object.keys(CATS)),
    thr:.55,labels:false,showEdges:true,focus:false,matches:new Set(),
    THREE:null,renderer:null,scene:null,camera:null,brain:null,
    ptsReal:null,ptsAmb:null,linesConv:null,linesSim:null,fibers:null,
    pulses:[],flash:null,bursts:[],shellL:null,shellR:null,stem:null,
    rot:{x:0,y:0},rotT:{x:0,y:0},dist:3.4,distT:3.4,
    camGoal:null,idle:0,ray:null,ptr:null,labelEls:[],ring:null,
    W:0,H:0,frame:0,
  };
  let stage,raf=null,t0=0;

  /* ---------- data ---------- */
  async function init(){
    stage=$('#memStage');bind();
    try{
      const g=await adminApi('/admin/memory/graph',{noRedirect:true});
      if(!g||g.enabled===false)return empty(
        'Memory is offline','Astra DB is not configured — set ASTRA_DB_APPLICATION_TOKEN and ASTRA_DB_API_ENDPOINT to grow memories.');
      try{S.THREE=await import(THREE_URL);}catch(e){
        return empty('3D unavailable','Could not load the WebGL engine for the neural view.');
      }
      build(g);
      $('#memStatus').className='pill ok';$('#memStatus').textContent='● connected';
    }catch(e){
      if(window.isUpgradeRequired&&isUpgradeRequired(e)){
        stage.innerHTML='';stage.appendChild(upgradeWall(e));return;
      }
      if($('#memStatus')){$('#memStatus').className='pill err';$('#memStatus').textContent='● error';}
      empty('Could not load memory',e&&e.message||'fetch failed');
    }
  }

  function empty(title,msg){
    stage.innerHTML='<div style="display:grid;place-items:center;height:100%;min-height:540px;text-align:center;padding:30px">'
      +'<div><div style="font-size:34px;opacity:.5">🧠</div><h3 style="margin:10px 0 6px">'+title+'</h3>'
      +'<div class="t-sm tx-3" style="max-width:380px">'+msg+'</div></div></div>';
  }

  function hash(s){let h=5381;for(let i=0;i<s.length;i++)h=(h*33^s.charCodeAt(i))>>>0;return h;}
  function rnd(seed){let t=seed;return()=>((t=(t*1103515245+12345)>>>0)/4294967296);}

  /* ---------- brain volume ----------
     Anatomical region in normalized space: two hemisphere ellipsoids
     (long front→back on z), a brainstem bulge below-back, and the
     longitudinal fissure excluded on top. Coordinates ~[-1,1]. */
  function insideBrain(x,y,z){
    const L=((x+.40)/.56)**2+((y+.02)/.72)**2+((z-.02)/.92)**2;
    const R=((x-.40)/.56)**2+((y+.02)/.72)**2+((z-.02)/.92)**2;
    const stem=(x/.15)**2+((y+.82)/.30)**2+((z-.45)/.24)**2;
    if(Math.min(L,R,stem)>1)return false;
    if(Math.abs(x)<.05&&y>-.45&&y<.62)return false;   // fissure
    return true;
  }
  function sampleBrain(rand){
    for(let i=0;i<400;i++){
      const x=(rand()*2-1)*.98,y=rand()*1.9-1.05,z=rand()*2.1-1.05;
      if(insideBrain(x,y,z))return[x,y,z];
    }
    return[rand()<.5?-.4:.4,0,0];
  }

  /* ---------- scene build ---------- */
  function build(g){
    const T=S.THREE;
    S.nodes=g.nodes.map(n=>{
      const cat=CATS[n.category]?n.category:'personal';
      const r=rnd(hash(n.id));
      const[x,y,z]=sampleBrain(r);
      return {id:n.id,label:n.label,text:n.text,category:cat,deg:n.degree||0,
        ts:n.ts,source:n.source,role:n.role,conv:n.conversation_id,col:n.collection,
        x,y,z,r:.030+Math.min(.05,Math.sqrt(n.degree||0)*.011),
        phase:r()*6.28,flash:0,alpha:1,in:0};
    });
    S.edges=g.edges.map(e=>({a:e.a,b:e.b,w:e.weight,kind:e.kind}));
    S.byId=new Map(S.nodes.map(n=>[n.id,n]));
    S.adj=new Map();
    S.edges.forEach(e=>{
      (S.adj.get(e.a)||S.adj.set(e.a,[]).get(e.a)).push({to:e.b,w:e.w,kind:e.kind});
      (S.adj.get(e.b)||S.adj.set(e.b,[]).get(e.b)).push({to:e.a,w:e.w,kind:e.kind});
    });
    stats(g.stats);legend();catChips();listRows();scene3d();
  }

  function scene3d(){
    const T=S.THREE,cv=$('#memCanvas');
    S.renderer=new T.WebGLRenderer({canvas:cv,antialias:true,alpha:true});
    S.renderer.setPixelRatio(Math.min(2,devicePixelRatio||1));
    S.scene=new T.Scene();
    S.camera=new T.PerspectiveCamera(45,1,.05,60);
    S.camera.position.set(0,0,S.dist);
    S.brain=new T.Group();S.scene.add(S.brain);
    S.ray=new T.Raycaster();S.ray.params.Points.threshold=.045;
    S.ptr=new T.Vector2();
    shell();ambientFibers();nodesPoints();ambientPoints();edgesLines();
    pulsesPoints();ringSprite();
    sizeCanvas();startLoop();entrance();
  }

  /* Translucent hemisphere shells with displaced folds — gives the
     recognizable cortical surface the point cloud lives inside. */
  function shell(){
    const T=S.THREE;
    const mk=(side)=>{
      const geo=new T.SphereGeometry(1,56,40);
      const pos=geo.attributes.position,v=new T.Vector3();
      for(let i=0;i<pos.count;i++){
        v.fromBufferAttribute(pos,i);
        // cortical folds — layered sines give gyri/sulci waviness
        const n=Math.sin(v.x*9+side)*Math.sin(v.y*13+v.z*7)
               +.5*Math.sin(v.z*17-v.x*11)+.35*Math.sin(v.y*23);
        const s=1+.045*n;
        v.multiplyScalar(s);
        v.x*=.56;v.y*=.72;v.z*=.92;
        v.x+=side*.40;
        pos.setXYZ(i,v.x,v.y,v.z);
      }
      geo.computeVertexNormals();
      const mesh=new T.Mesh(geo,new T.MeshBasicMaterial({
        color:0x3a5ea8,transparent:true,opacity:.045,
        blending:T.AdditiveBlending,side:T.DoubleSide,depthWrite:false}));
      const wire=new T.Mesh(geo.clone(),new T.MeshBasicMaterial({
        color:0x6f9fff,transparent:true,opacity:.028,wireframe:true,
        blending:T.AdditiveBlending,depthWrite:false}));
      S.brain.add(mesh,wire);return mesh;
    };
    S.shellL=mk(-1);S.shellR=mk(1);
    // brainstem
    const sg=new T.SphereGeometry(1,24,18),p=sg.attributes.position,v=new T.Vector3();
    for(let i=0;i<p.count;i++){v.fromBufferAttribute(p,i);
      v.x*=.15;v.y*=.30;v.z*=.24;v.y-=.82;v.z+=.45;p.setXYZ(i,v.x,v.y,v.z);}
    S.stem=new T.Mesh(sg,new T.MeshBasicMaterial({color:0x3a5ea8,
      transparent:true,opacity:.05,blending:T.AdditiveBlending,depthWrite:false}));
    S.brain.add(S.stem);
  }

  /* Faint ambient fibers — atmospheric connectome texture between
     spatially close neurons (decorative, not memory data). */
  function ambientFibers(){
    const T=S.THREE,pts=S.nodes.map(n=>[n.x,n.y,n.z]);
    const amb=[];
    const r0=rnd(9917);
    for(let i=0;i<AMBIENT;i++)amb.push(sampleBrain(r0));
    S.ambPts=amb;
    const all=pts.concat(amb),pos=[];
    // sparse neighbor links — grid-bucketed for speed
    const cell=.22,grid=new Map();
    all.forEach((p,i)=>{
      const k=`${p[0]/cell|0},${p[1]/cell|0},${p[2]/cell|0}`;
      (grid.get(k)||grid.set(k,[]).get(k)).push(i);
    });
    all.forEach((p,i)=>{
      const bx=p[0]/cell|0,by=p[1]/cell|0,bz=p[2]/cell|0;
      for(let dx=-1;dx<=1;dx++)for(let dy=-1;dy<=1;dy++)for(let dz=-1;dz<=1;dz++){
        const b=grid.get(`${bx+dx},${by+dy},${bz+dz}`);if(!b)continue;
        for(const j of b){
          if(j<=i)continue;
          const q=all[j],d2=(p[0]-q[0])**2+(p[1]-q[1])**2+(p[2]-q[2])**2;
          if(d2<.024&&Math.random()<.5)pos.push(p[0],p[1],p[2],q[0],q[1],q[2]);
        }
      }
    });
    const geo=new T.BufferGeometry();
    geo.setAttribute('position',new T.Float32BufferAttribute(pos,3));
    S.fibers=new T.LineSegments(geo,new T.LineBasicMaterial({
      color:0x4d6db4,transparent:true,opacity:.05,depthWrite:false}));
    S.brain.add(S.fibers);
  }

  const nodeVert=`
    attribute float aSize;attribute vec3 aColor;attribute float aAlpha;
    attribute float aPhase;attribute float aFlash;
    uniform float uTime;uniform float uScale;
    varying vec3 vC;varying float vA;
    void main(){
      vec4 mv=modelViewMatrix*vec4(position,1.);
      float tw=.72+.28*sin(uTime*1.8+aPhase);
      vA=clamp(aAlpha*(tw+aFlash*1.6),0.,1.);
      vC=aColor;
      gl_PointSize=(aSize*(1.+aFlash*2.2))*uScale/-mv.z;
      gl_Position=projectionMatrix*mv;}`;
  const nodeFrag=`
    varying vec3 vC;varying float vA;
    void main(){
      float d=length(gl_PointCoord-.5)*2.;
      float a=pow(smoothstep(1.,0.,d),2.2);
      float core=smoothstep(.34,0.,d);
      gl_FragColor=vec4(vC+core*.85,a*vA);
      if(gl_FragColor.a<.01)discard;}`;
  function pointsMaterial(){
    const T=S.THREE;
    return new T.ShaderMaterial({vertexShader:nodeVert,fragmentShader:nodeFrag,
      uniforms:{uTime:{value:0},uScale:{value:600}},
      transparent:true,depthWrite:false,blending:T.AdditiveBlending});
  }

  function nodesPoints(){
    const T=S.THREE,n=S.nodes.length;
    const pos=new Float32Array(n*3),col=new Float32Array(n*3),
      siz=new Float32Array(n),alp=new Float32Array(n),
      pha=new Float32Array(n),fla=new Float32Array(n);
    S.nodes.forEach((nd,i)=>{
      const c=new T.Color(CATS[nd.category].c);
      pos.set([nd.x,nd.y,nd.z],i*3);col.set([c.r,c.g,c.b],i*3);
      siz[i]=nd.r;pha[i]=nd.phase;alp[i]=1;
    });
    S.flash=fla;
    const geo=new T.BufferGeometry();
    geo.setAttribute('position',new T.BufferAttribute(pos,3));
    geo.setAttribute('aColor',new T.BufferAttribute(col,3));
    geo.setAttribute('aSize',new T.BufferAttribute(siz,1));
    geo.setAttribute('aAlpha',new T.BufferAttribute(alp,1));
    geo.setAttribute('aPhase',new T.BufferAttribute(pha,1));
    geo.setAttribute('aFlash',new T.BufferAttribute(fla,1));
    S.ptsReal=new T.Points(geo,pointsMaterial());S.brain.add(S.ptsReal);
  }

  function ambientPoints(){
    const T=S.THREE,amb=S.ambPts,n=amb.length;
    const pos=new Float32Array(n*3),col=new Float32Array(n*3),
      siz=new Float32Array(n),alp=new Float32Array(n),pha=new Float32Array(n),
      fla=new Float32Array(n);
    const pal=[new T.Color('#4EA5FF'),new T.Color('#32E6E6'),new T.Color('#A78BFA')];
    const r0=rnd(3131);
    amb.forEach((p,i)=>{
      const c=pal[i%3];pos.set(p,i*3);col.set([c.r,c.g,c.b],i*3);
      siz[i]=.012+r0()*.02;pha[i]=r0()*6.28;alp[i]=.5;
    });
    const geo=new T.BufferGeometry();
    geo.setAttribute('position',new T.BufferAttribute(pos,3));
    geo.setAttribute('aColor',new T.BufferAttribute(col,3));
    geo.setAttribute('aSize',new T.BufferAttribute(siz,1));
    geo.setAttribute('aAlpha',new T.BufferAttribute(alp,1));
    geo.setAttribute('aPhase',new T.BufferAttribute(pha,1));
    geo.setAttribute('aFlash',new T.BufferAttribute(fla,1));
    S.ptsAmb=new T.Points(geo,pointsMaterial());S.brain.add(S.ptsAmb);
  }

  function edgesLines(){
    const T=S.THREE;
    const build=(kind)=>{
      const es=S.edges.filter(e=>e.kind===kind),pos=[],col=[];
      es.forEach(e=>{
        const A=S.byId.get(e.a),B=S.byId.get(e.b);if(!A||!B)return;
        const c=new T.Color(EDGE_COLORS[kind]);
        const f=kind==='conversation'?1:.25+e.w*.75;
        pos.push(A.x,A.y,A.z,B.x,B.y,B.z);
        col.push(c.r*f,c.g*f,c.b*f,c.r*f,c.g*f,c.b*f);
      });
      const geo=new T.BufferGeometry();
      geo.setAttribute('position',new T.Float32BufferAttribute(pos,3));
      geo.setAttribute('color',new T.Float32BufferAttribute(col,3));
      const m=new T.LineSegments(geo,new T.LineBasicMaterial({
        vertexColors:true,transparent:true,
        opacity:kind==='conversation'?.55:.30,depthWrite:false}));
      m.userData.kind=kind;S.brain.add(m);return m;
    };
    S.linesConv=build('conversation');
    S.linesSim=build('similarity');
  }
  function rebuildSim(){
    if(!S.linesSim)return;
    S.brain.remove(S.linesSim);S.linesSim.geometry.dispose();S.linesSim.material.dispose();
    const T=S.THREE,pos=[],col=[];
    S.edges.filter(e=>e.kind==='similarity'&&e.w>=S.thr).forEach(e=>{
      const A=S.byId.get(e.a),B=S.byId.get(e.b);if(!A||!B)return;
      if(!S.cats.has(A.category)||!S.cats.has(B.category))return;
      const c=new T.Color(EDGE_COLORS.similarity),f=.25+e.w*.75;
      pos.push(A.x,A.y,A.z,B.x,B.y,B.z);
      col.push(c.r*f,c.g*f,c.b*f,c.r*f,c.g*f,c.b*f);
    });
    const geo=new T.BufferGeometry();
    geo.setAttribute('position',new T.Float32BufferAttribute(pos,3));
    geo.setAttribute('color',new T.Float32BufferAttribute(col,3));
    S.linesSim=new T.LineSegments(geo,new T.LineBasicMaterial({
      vertexColors:true,transparent:true,opacity:.30,depthWrite:false}));
    S.brain.add(S.linesSim);
  }

  /* Synaptic pulses — small bright packets travelling real edges. */
  function pulsesPoints(){
    const T=S.THREE;
    const pos=new Float32Array(MAX_PULSES*3),col=new Float32Array(MAX_PULSES*3),
      siz=new Float32Array(MAX_PULSES),alp=new Float32Array(MAX_PULSES),
      pha=new Float32Array(MAX_PULSES),fla=new Float32Array(MAX_PULSES);
    siz.fill(.05);alp.fill(1);
    const geo=new T.BufferGeometry();
    geo.setAttribute('position',new T.BufferAttribute(pos,3));
    geo.setAttribute('aColor',new T.BufferAttribute(col,3));
    geo.setAttribute('aSize',new T.BufferAttribute(siz,1));
    geo.setAttribute('aAlpha',new T.BufferAttribute(alp,1));
    geo.setAttribute('aPhase',new T.BufferAttribute(pha,1));
    geo.setAttribute('aFlash',new T.BufferAttribute(fla,1));
    S.ptsPulse=new T.Points(geo,pointsMaterial());S.brain.add(S.ptsPulse);
  }

  function ringSprite(){
    const T=S.THREE,c=document.createElement('canvas');c.width=c.height=64;
    const g=c.getContext('2d');
    g.strokeStyle='#fff';g.lineWidth=4;
    g.beginPath();g.arc(32,32,26,0,7);g.stroke();
    const tex=new T.CanvasTexture(c);
    S.ring=new T.Sprite(new T.SpriteMaterial({map:tex,transparent:true,
      depthWrite:false,depthTest:false,opacity:.95}));
    S.ring.scale.set(.16,.16,1);S.ring.visible=false;
    S.brain.add(S.ring);
  }

  /* ---------- loop ---------- */
  function sizeCanvas(){
    const r=stage.getBoundingClientRect();
    S.W=r.width;S.H=Math.max(420,r.height);
    if(!S.renderer)return;
    S.renderer.setSize(S.W,S.H,false);
    S.camera.aspect=S.W/S.H;S.camera.updateProjectionMatrix();
    const fov=S.camera.fov*Math.PI/360;
    [S.ptsReal,S.ptsAmb,S.ptsPulse].forEach(p=>{
      if(p)p.material.uniforms.uScale.value=S.H/(2*Math.tan(fov));
    });
  }

  function loop(t){
    raf=requestAnimationFrame(loop);
    if(!S.renderer)return;
    const dt=Math.min(.05,(t-t0)/1000||0);t0=t;S.frame++;
    const alive=!rm();
    S.idle+=dt;
    // gentle auto-rotate + breathing; user drag pauses rotation briefly
    if(alive&&S.idle>4)S.rotT.y+=dt*.10;
    S.rot.x+=(S.rotT.x-S.rot.x)*(alive?.12:1);
    S.rot.y+=(S.rotT.y-S.rot.y)*(alive?.12:1);
    S.dist+=(S.distT-S.dist)*(alive?.14:1);
    S.brain.rotation.set(S.rot.x,S.rot.y,0);
    const br=alive?1+Math.sin(t*.0009)*.012:1;
    S.brain.scale.set(br,br,br);
    S.camera.position.z=S.dist;
    S.camera.lookAt(0,-.05,0);
    // camera fly goal
    if(S.camGoal){S.brain.position.lerp(S.camGoal,.09);
      if(S.brain.position.distanceTo(S.camGoal)<.01){S.brain.position.copy(S.camGoal);S.camGoal=null;}}
    const tm=t/1000;
    [S.ptsReal,S.ptsAmb,S.ptsPulse].forEach(p=>{if(p)p.material.uniforms.uTime.value=alive?tm:0;});
    if(alive){firing(dt);pulses(dt);}
    syncAlpha();labels();
    S.renderer.render(S.scene,S.camera);
  }

  /* Periodic firing: random neuron flashes, then its stored+similar
     neighbours cascade — visible "thought" propagation. */
  function firing(dt){
    const fla=S.flash;
    for(let i=0;i<fla.length;i++)fla[i]*=Math.exp(-dt*3.2);
    if(Math.random()<.012&&S.nodes.length){
      const n=S.nodes[Math.random()*S.nodes.length|0],i=S.nodes.indexOf(n);
      fla[i]=1;
      (S.adj.get(n.id)||[]).slice(0,6).forEach((r,k)=>{
        S.bursts.push({at:performance.now()/1000+.14*(k+1),id:r.to});});
      if(Math.random()<.3){ // occasional regional wave
        (S.adj.get(n.id)||[]).forEach(r=>{
          (S.adj.get(r.to)||[]).slice(0,3).forEach(r2=>
            S.bursts.push({at:performance.now()/1000+.5+Math.random()*.4,id:r2.to}));});
      }
    }
    const now=performance.now()/1000;
    S.bursts=S.bursts.filter(b=>{
      if(b.at>now)return true;
      const i=S.nodes.findIndex(n=>n.id===b.id);
      if(i>=0)fla[i]=Math.max(fla[i],.85);
      return false;
    });
    S.ptsReal.geometry.attributes.aFlash.needsUpdate=true;
  }

  function pulses(dt){
    const vis=S.edges.filter(e=>{
      if(e.kind==='similarity'&&e.w<S.thr)return false;
      const A=S.byId.get(e.a),B=S.byId.get(e.b);
      return A&&B&&S.cats.has(A.category)&&S.cats.has(B.category);
    });
    if(S.pulses.length<MAX_PULSES&&vis.length&&Math.random()<.16){
      const e=vis[Math.random()*vis.length|0];
      S.pulses.push({e,t:0,sp:.5+Math.random()*.9});
    }
    const pos=S.ptsPulse.geometry.attributes.position.array,
      col=S.ptsPulse.geometry.attributes.aColor.array,
      T=S.THREE;
    let k=0;
    S.pulses=S.pulses.filter(p=>{
      p.t+=dt*p.sp;if(p.t>=1){
        const i=S.nodes.findIndex(n=>n.id===p.e.b);
        if(i>=0)S.flash[i]=1;return false;}
      const A=S.byId.get(p.e.a),B=S.byId.get(p.e.b);if(!A||!B)return false;
      pos[k*3]=A.x+(B.x-A.x)*p.t;pos[k*3+1]=A.y+(B.y-A.y)*p.t;pos[k*3+2]=A.z+(B.z-A.z)*p.t;
      const c=new T.Color(p.e.kind==='conversation'?EDGE_COLORS.conversation:'#cfe4ff');
      col[k*3]=c.r;col[k*3+1]=c.g;col[k*3+2]=c.b;k++;return true;
    });
    for(let i=k;i<MAX_PULSES;i++){pos[i*3]=0;pos[i*3+1]=999;pos[i*3+2]=999;}
    S.ptsPulse.geometry.attributes.position.needsUpdate=true;
    S.ptsPulse.geometry.attributes.aColor.needsUpdate=true;
  }

  /* Category/search/focus/hover alpha — applied to the shader attr. */
  function syncAlpha(){
    const alp=S.ptsReal.geometry.attributes.aAlpha.array;
    const nb=(S.sel||S.hover)?neighbours((S.sel||S.hover).id):null;
    S.nodes.forEach((n,i)=>{
      let a=S.cats.has(n.category)?1:0;
      if(nb&&n.id!==(S.sel||S.hover).id&&!nb.has(n.id))a*=.10;
      if(S.matches.size&&!S.matches.has(n.id))a*=.30;
      if(S.focus&&S.sel){const fb=neighbours(S.sel.id);if(!fb.has(n.id))a*=.06;}
      alp[i]=a;
    });
    S.ptsReal.geometry.attributes.aAlpha.needsUpdate=true;
  }

  /* DOM labels projected from 3D — hover/selected always, top-degree
     nodes when the Labels toggle is on. */
  function labels(){
    const T=S.THREE,v=new T.Vector3();
    let shown=[];
    if(S.hover)shown.push(S.hover);
    if(S.sel&&S.sel!==S.hover)shown.push(S.sel);
    if(S.labels)shown=shown.concat(S.nodes.filter(n=>n.deg>2&&!shown.includes(n)).slice(0,10));
    while(S.labelEls.length<shown.length){
      const d=document.createElement('div');
      d.style.cssText='position:absolute;pointer-events:none;font-size:10px;color:rgba(220,228,244,.9);text-shadow:0 1px 4px #000;transform:translate(-50%,-140%);white-space:nowrap;z-index:3';
      stage.appendChild(d);S.labelEls.push(d);
    }
    S.labelEls.forEach((el,i)=>{
      const n=shown[i];
      if(!n||!S.cats.has(n.category)){el.style.display='none';return;}
      v.set(n.x,n.y,n.z).applyMatrix4(S.brain.matrixWorld).project(S.camera);
      el.style.display='block';
      el.style.left=((v.x*.5+.5)*S.W)+'px';
      el.style.top=((-v.y*.5+.5)*S.H)+'px';
      el.textContent=n.label.slice(0,26);
    });
  }

  function neighbours(id){
    const s=new Set([id]);
    (S.adj.get(id)||[]).forEach(r=>{if(r.kind==='conversation'||r.w>=S.thr)s.add(r.to);});
    return s;
  }

  function entrance(){
    if(rm()){S.nodes.forEach(n=>n.in=1);return;}
    const g=G();if(!g)return;
    g.fromTo('#memStage',{opacity:0},{opacity:1,duration:.5});
    // nodes are full-alpha in shader — the stage fade covers the reveal
    g.fromTo(S.brain.scale,{x:.82,y:.82,z:.82},{x:1,y:1,z:1,duration:1.4,ease:'power3.out'});
    g.fromTo(S.rotT,{y:-.9},{y:0,duration:1.6,ease:'power3.out'});
  }

  /* ---------- picking / camera ---------- */
  function pick(px,py){
    if(!S.ptsReal)return null;
    S.ptr.set((px/S.W)*2-1,-(py/S.H)*2+1);
    S.ray.setFromCamera(S.ptr,S.camera);
    const hits=S.ray.intersectObject(S.ptsReal);
    if(!hits.length)return null;
    const n=S.nodes[hits[0].index];
    return n&&S.cats.has(n.category)?n:null;
  }

  function bind(){
    stage=$('#memStage');const cv=$('#memCanvas');
    new ResizeObserver(sizeCanvas).observe(stage);
    let down=null,moved=false;
    cv.addEventListener('pointerdown',e=>{down={x:e.clientX,y:e.clientY,rx:S.rotT.x,ry:S.rotT.y};moved=false;S.idle=0;try{cv.setPointerCapture(e.pointerId);}catch(_){}cv.classList.add('dragging');});
    cv.addEventListener('pointermove',e=>{
      const r=cv.getBoundingClientRect(),px=e.clientX-r.left,py=e.clientY-r.top;
      S.idle=0;
      if(down){const dx=e.clientX-down.x,dy=e.clientY-down.y;
        if(Math.abs(dx)+Math.abs(dy)>4)moved=true;
        S.rotT.y=down.ry+dx*.005;
        S.rotT.x=Math.max(-.9,Math.min(.9,down.rx+dy*.005));return;}
      const n=S.renderer?pick(px,py):null;
      if(n!==S.hover){S.hover=n;cv.classList.toggle('point',!!n);tip(n,px,py);}
      else if(n)tip(n,px,py);
      if(S.ring){S.ring.visible=!!(n||S.sel);
        const h=n||S.sel;if(h)S.ring.position.set(h.x,h.y,h.z);}
    });
    cv.addEventListener('pointerup',e=>{
      cv.classList.remove('dragging');
      if(down&&!moved){
        const r=cv.getBoundingClientRect(),n=pick(e.clientX-r.left,e.clientY-r.top);
        n?select(n,false):deselect();
      }
      down=null;
    });
    cv.addEventListener('dblclick',e=>{
      const r=cv.getBoundingClientRect(),n=pick(e.clientX-r.left,e.clientY-r.top);
      if(n)select(n,true);
    });
    cv.addEventListener('pointerleave',()=>{S.hover=null;$('#memTip').style.display='none';});
    cv.addEventListener('wheel',e=>{e.preventDefault();S.idle=0;
      const f=e.deltaY<0?.86:1/.86;
      S.distT=Math.max(1.1,Math.min(6,S.distT*f));
    },{passive:false});
    $('#zoomIn').onclick=()=>{S.distT=Math.max(1.1,S.distT*.8);};
    $('#zoomOut').onclick=()=>{S.distT=Math.min(6,S.distT*1.25);};
    $('#zoomFit').onclick=()=>{S.rotT.x=0;S.rotT.y=0;S.distT=3.4;S.camGoal=null;
      if(G()&&!rm())G().to(S.brain.position,{x:0,y:0,z:0,duration:.8,ease:'power3.inOut'});
      else S.brain.position.set(0,0,0);};
    $('#memLbl').onclick=e=>{S.labels=!S.labels;e.currentTarget.classList.toggle('sel',S.labels);};
    $('#memEdg').onclick=e=>{S.showEdges=!S.showEdges;e.currentTarget.classList.toggle('sel',S.showEdges);
      [S.linesConv,S.linesSim,S.fibers].forEach(m=>{if(m)m.visible=S.showEdges;});};
    $('#memFocus').onclick=e=>{S.focus=!S.focus;e.currentTarget.classList.toggle('sel',S.focus);};
    $('#memThr').oninput=e=>{S.thr=e.target.value/100;$('#memThrV').textContent=S.thr.toFixed(2);rebuildSim();};
    $('#vGraph').onclick=()=>view('graph');$('#vList').onclick=()=>view('list');
    const sq=$('#memSearch');
    sq.addEventListener('input',()=>{
      const q=sq.value.trim().toLowerCase();S.matches=new Set();
      if(q)S.nodes.forEach(n=>{if((n.label+n.text).toLowerCase().includes(q))S.matches.add(n.id);});
    });
    sq.addEventListener('keydown',e=>{if(e.key==='Enter'&&S.matches.size){
      const n=S.byId.get([...S.matches][0]);if(n)select(n,true);}});
    document.addEventListener('keydown',e=>{if(e.key==='Escape')deselect();});
  }

  function tip(n,px,py){
    const t=$('#memTip');if(!n){t.style.display='none';return;}
    t.innerHTML=esc(n.label)+'<div class="c">'+CATS[n.category].label+' · '+n.deg+' links</div>';
    t.style.display='block';
    t.style.left=Math.min(S.W-150,px+14)+'px';t.style.top=(py+14)+'px';
  }

  function view(v){
    $('#memPage').classList.toggle('list',v==='list');
    $('#vGraph').classList.toggle('sel',v==='graph');$('#vList').classList.toggle('sel',v==='list');
    if(v==='graph')requestAnimationFrame(sizeCanvas);else listRows();
  }

  function deselect(){S.sel=null;S.focus=false;
    $('#memDetail').classList.remove('open');$('#memFocus').disabled=true;
    $('#memFocus').classList.remove('sel');if(S.ring&&!S.hover)S.ring.visible=false;}

  function select(n,fly){
    S.sel=n;$('#memFocus').disabled=false;
    if(S.ring){S.ring.visible=true;S.ring.position.set(n.x,n.y,n.z);
      if(G()&&!rm())G().fromTo(S.ring.scale,{x:.3,y:.3},{x:.16,y:.16,duration:.5,ease:'power2.out'});}
    if(fly&&G()&&!rm()){
      // push the brain so the node lands near screen centre
      const T=S.THREE,v=new T.Vector3(n.x,n.y,n.z);
      v.applyMatrix4(S.brain.matrixWorld);
      S.camGoal=v.clone().multiplyScalar(-.8);
      S.distT=Math.min(S.distT,2.0);
    }
    renderDetail(n);
  }

  /* ---------- stats/legend/list/detail (unchanged data layer) ---------- */
  function stats(st){
    st=st||{};
    const set=(id,v)=>{const el=$(id);
      if(window.NovaMotion&&NovaMotion.count&&typeof v==='number')NovaMotion.count(el,v);
      else el.textContent=typeof v==='number'?v.toLocaleString():(v??'—');};
    set('#stMem',st.memories||0);set('#stRel',st.relationships||0);
    set('#stCat',st.categories?Object.keys(st.categories).length:0);
    $('#stSim').textContent=st.avg_similarity!=null?st.avg_similarity.toFixed(2):'—';
  }

  function catChips(){
    const host=$('#memCats');host.innerHTML='';
    Object.entries(CATS).forEach(([k,v])=>{
      const b=document.createElement('button');
      b.className='ui-chip sel';b.dataset.cat=k;
      b.innerHTML='<span class="dot" style="display:inline-block;width:7px;height:7px;border-radius:50%;background:'+v.c+';margin-right:5px"></span>'+v.label;
      b.onclick=()=>{S.cats.has(k)?S.cats.delete(k):S.cats.add(k);b.classList.toggle('sel',S.cats.has(k));rebuildSim();};
      host.appendChild(b);
    });
  }

  function legend(){
    $('#memLegend').innerHTML=
      Object.values(CATS).map(v=>'<span class="lg"><span class="dot" style="background:'+v.c+'"></span>'+v.label+'</span>').join('')
      +'<span class="lg"><span class="ln" style="border-color:'+EDGE_COLORS.conversation+'"></span>conversation · stored</span>'
      +'<span class="lg"><span class="ln" style="border-color:'+EDGE_COLORS.similarity+'"></span>similarity · computed</span>';
  }

  function listRows(){
    const tb=$('#memRows');tb.innerHTML='';
    S.nodes.slice().sort((a,b)=>(b.ts||0)-(a.ts||0)).forEach(n=>{
      const tr=document.createElement('tr');tr.style.cursor='pointer';
      tr.innerHTML='<td style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(n.label)+'</td>'
        +'<td><span class="pill mut" style="font-size:10px">'+CATS[n.category].label+'</span></td>'
        +'<td>'+n.deg+'</td><td class="tx-3">'+(n.ts?new Date(n.ts*1000).toLocaleDateString():'—')+'</td>'
        +'<td style="text-align:right"><button class="ui-btn ui-btn-ghost ui-btn-sm">Open</button></td>';
      tr.onclick=()=>{view('graph');select(n,true);};
      tb.appendChild(tr);
    });
    if(window.NovaMotion)NovaMotion.stagger([...tb.children],{y:8,dur:.3,stagger:.015});
  }

  function renderDetail(n){
    const d=$('#memDetail');
    const rel=(S.adj.get(n.id)||[]).slice().sort((a,b)=>b.w-a.w);
    const date=n.ts?new Date(n.ts*1000).toLocaleString():'—';
    d.innerHTML=
      '<div class="hd"><h3>'+esc(n.label)+'</h3>'
      +'<button class="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" id="mdX" title="Close">✕</button></div>'
      +'<div class="bd">'
      +'<span class="pill mut" style="font-size:10px">'+CATS[n.category].label+'</span>'
      +(n.role?' <span class="pill mut" style="font-size:10px">'+esc(n.role)+'</span>':'')
      +'<div class="txt">'+esc(n.text||'(empty)')+'</div>'
      +'<div class="mem-kv">'
      +'<span class="k">Memory ID</span><span class="v" style="font-family:monospace;font-size:11px">'+esc(n.id)+'</span>'
      +'<span class="k">Indexed</span><span class="v">'+date+'</span>'
      +'<span class="k">Store</span><span class="v">'+esc(n.col==='mem_long'?'long-term (mem_long)':'short-term (mem_short)')+'</span>'
      +(n.source?'<span class="k">Source</span><span class="v">'+esc(n.source)+'</span>':'')
      +(n.conv?'<span class="k">Conversation</span><span class="v" style="font-family:monospace;font-size:11px">'+esc(n.conv)+'</span>':'')
      +'<span class="k">Connections</span><span class="v">'+rel.length+'</span>'
      +'</div>'
      +'<div class="sec-title" style="margin:4px 0 6px">Related memories</div>'
      +(rel.length?rel.slice(0,14).map(r=>{
        const m=S.byId.get(r.to);if(!m)return'';
        const w=r.kind==='conversation'?'stored':r.w.toFixed(2);
        return '<div class="mem-rel" data-to="'+r.to+'"><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:'+CATS[m.category].c+'"></span>'
          +'<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:170px">'+esc(m.label)+'</span>'
          +'<span class="kind '+(r.kind==='conversation'?'stored':'sim')+'">'+(r.kind==='conversation'?'stored':'similarity')+'</span>'
          +'<span class="w">'+w+'</span></div>';
      }).join(''):'<div class="t-xs tx-3">No connections — an isolated memory.</div>')
      +'</div>'
      +'<div class="mem-acts">'
      +'<button class="ui-btn ui-btn-sm" id="mdCopy">Copy</button>'
      +'<button class="ui-btn ui-btn-ghost ui-btn-sm" id="mdFoc" style="margin-left:auto">Isolate neighbourhood</button>'
      +'</div>';
    d.classList.add('open');
    d.querySelector('#mdX').onclick=deselect;
    d.querySelector('#mdCopy').onclick=()=>{navigator.clipboard&&navigator.clipboard.writeText(n.text||'');if(window.toast)toast('Memory copied');};
    d.querySelector('#mdFoc').onclick=()=>{S.focus=true;$('#memFocus').classList.add('sel');};
    d.querySelectorAll('.mem-rel').forEach(el=>el.onclick=()=>{
      const m=S.byId.get(el.dataset.to);if(m)select(m,true);});
    if(window.NovaMotion&&G())G().fromTo(d,{x:18,opacity:0},{x:0,opacity:1,duration:.35,ease:'power3.out'});
  }

  function startLoop(){if(!raf)raf=requestAnimationFrame(ts=>{t0=ts;loop(ts);});
    document.addEventListener('visibilitychange',()=>{
      if(document.hidden&&raf){cancelAnimationFrame(raf);raf=null;}
      else if(!document.hidden&&!raf)raf=requestAnimationFrame(ts=>{t0=ts;loop(ts);});
    });
  }
})();
