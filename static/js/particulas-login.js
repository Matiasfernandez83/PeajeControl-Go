/* PeajeControl Go - fondo de particulas del login (canvas #net, pantalla completa).
   Misma idea que el de la app pero mas denso y siempre en modo oscuro. */
(function(){
  const cv=document.getElementById('net'), ctx=cv.getContext('2d');
  let W,H,DPR, nodes=[], trail=[];
  const reduce=matchMedia('(prefers-reduced-motion:reduce)').matches;
  const COL={base:[150,170,200], rojo:[227,6,19], lima:[223,255,0], azul:[59,130,246]};
  const mouse={x:-999,y:-999,on:false};

  function resize(){
    DPR=Math.min(2,window.devicePixelRatio||1);
    W=cv.width=Math.floor(innerWidth*DPR);
    H=cv.height=Math.floor(innerHeight*DPR);
    cv.style.width=innerWidth+'px'; cv.style.height=innerHeight+'px';
    const area=innerWidth*innerHeight;
    const target=Math.min(140, Math.max(46, Math.round(area/13000)));
    nodes=[];
    for(let i=0;i<target;i++){
      nodes.push({x:Math.random()*W,y:Math.random()*H,
        vx:(Math.random()-.5)*0.22*DPR, vy:(Math.random()-.5)*0.22*DPR});
    }
  }
  function rgba(c,a){return 'rgba('+c[0]+','+c[1]+','+c[2]+','+a+')';}

  function step(){
    ctx.clearRect(0,0,W,H);
    const LINK=145*DPR, MR=190*DPR;
    // update + draw nodes
    for(const n of nodes){
      n.x+=n.vx; n.y+=n.vy;
      if(n.x<0||n.x>W)n.vx*=-1; if(n.y<0||n.y>H)n.vy*=-1;
      // gentle pull toward cursor
      if(mouse.on){
        const dx=mouse.x-n.x, dy=mouse.y-n.y, d=Math.hypot(dx,dy);
        if(d<MR){ const f=(1-d/MR)*0.035; n.vx+=dx/d*f; n.vy+=dy/d*f; }
      }
      // damping + speed cap
      n.vx*=0.995; n.vy*=0.995;
      const sp=Math.hypot(n.vx,n.vy), cap=0.9*DPR;
      if(sp>cap){n.vx=n.vx/sp*cap; n.vy=n.vy/sp*cap;}
    }
    // links node-node
    for(let i=0;i<nodes.length;i++){
      const a=nodes[i];
      for(let j=i+1;j<nodes.length;j++){
        const b=nodes[j], dx=a.x-b.x, dy=a.y-b.y, d=Math.hypot(dx,dy);
        if(d<LINK){
          ctx.strokeStyle=rgba(COL.base,(1-d/LINK)*0.16);
          ctx.lineWidth=1*DPR; ctx.beginPath();
          ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y); ctx.stroke();
        }
      }
    }
    // links node-cursor (the "web" that reaches to the mouse)
    if(mouse.on){
      for(const n of nodes){
        const dx=mouse.x-n.x, dy=mouse.y-n.y, d=Math.hypot(dx,dy);
        if(d<MR){
          const t=1-d/MR;
          const c=t>0.55?COL.rojo:(t>0.3?COL.lima:COL.azul);
          ctx.strokeStyle=rgba(c,t*0.5);
          ctx.lineWidth=1.1*DPR; ctx.beginPath();
          ctx.moveTo(mouse.x,mouse.y); ctx.lineTo(n.x,n.y); ctx.stroke();
        }
      }
    }
    // nodes
    for(const n of nodes){
      let near=0;
      if(mouse.on){const d=Math.hypot(mouse.x-n.x,mouse.y-n.y); near=d<MR?1-d/MR:0;}
      const r=(1.5+near*1.6)*DPR;
      ctx.fillStyle=near>0.5?rgba(COL.rojo,.9):rgba(COL.base,.5+near*.4);
      ctx.beginPath(); ctx.arc(n.x,n.y,r,0,7); ctx.fill();
    }
    // cursor comet trail (the drag effect)
    if(trail.length>1){
      for(let i=1;i<trail.length;i++){
        const p=trail[i-1], q=trail[i], t=i/trail.length;
        ctx.strokeStyle=rgba(COL.rojo, t*0.5);
        ctx.lineWidth=(0.5+t*3.5)*DPR; ctx.lineCap='round';
        ctx.beginPath(); ctx.moveTo(p.x,p.y); ctx.lineTo(q.x,q.y); ctx.stroke();
      }
    }
    if(mouse.on){
      const g=ctx.createRadialGradient(mouse.x,mouse.y,0,mouse.x,mouse.y,26*DPR);
      g.addColorStop(0,rgba(COL.lima,.55)); g.addColorStop(1,rgba(COL.lima,0));
      ctx.fillStyle=g; ctx.beginPath(); ctx.arc(mouse.x,mouse.y,26*DPR,0,7); ctx.fill();
    }
    // fade trail
    if(trail.length>22) trail.shift();
    raf=requestAnimationFrame(step);
  }

  function setMouse(x,y){ mouse.x=x*DPR; mouse.y=y*DPR; mouse.on=true; trail.push({x:mouse.x,y:mouse.y}); }
  addEventListener('mousemove',e=>setMouse(e.clientX,e.clientY));
  addEventListener('mouseleave',()=>{mouse.on=false;trail=[];});
  addEventListener('touchmove',e=>{const t=e.touches[0]; if(t) setMouse(t.clientX,t.clientY);},{passive:true});
  addEventListener('resize',resize);

  let raf; resize();
  if(reduce){ mouse.on=false; step(); cancelAnimationFrame(raf); }
  else {
    step();
    document.addEventListener('visibilitychange',()=>{
      if(document.hidden){ cancelAnimationFrame(raf); }
      else { cancelAnimationFrame(raf); raf=requestAnimationFrame(step); }
    });
  }
})();
