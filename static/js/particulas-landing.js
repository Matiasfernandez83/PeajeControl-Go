/* PeajeControl Go - fondo de particulas de la portada (canvas #net dentro del hero).
   Solo anima mientras el hero esta a la vista y reacciona al mouse/dedo sobre el hero. */
(function(){
  const hero=document.getElementById('hero'), cv=document.getElementById('net'), ctx=cv.getContext('2d');
  const reduce=matchMedia('(prefers-reduced-motion:reduce)').matches;
  const COL={base:[150,170,200],rojo:[227,6,19],lima:[223,255,0],azul:[59,130,246]};
  const mouse={x:-999,y:-999,on:false};
  let W,H,DPR,nodes=[],trail=[],raf=0,visible=true;
  function rgba(c,a){return 'rgba('+c[0]+','+c[1]+','+c[2]+','+a+')';}
  function resize(){
    DPR=Math.min(2,window.devicePixelRatio||1);
    const w=hero.clientWidth,h=hero.clientHeight;
    W=cv.width=Math.floor(w*DPR); H=cv.height=Math.floor(h*DPR);
    const target=Math.min(70,Math.max(26,Math.round(w*h/16000)));
    nodes=[];
    for(let i=0;i<target;i++)nodes.push({x:Math.random()*W,y:Math.random()*H,vx:(Math.random()-.5)*.22*DPR,vy:(Math.random()-.5)*.22*DPR});
    if(reduce)draw();
  }
  function draw(){
    ctx.clearRect(0,0,W,H);
    const LINK=145*DPR,MR=190*DPR;
    for(let i=0;i<nodes.length;i++){const a=nodes[i];
      for(let j=i+1;j<nodes.length;j++){const b=nodes[j],d=Math.hypot(a.x-b.x,a.y-b.y);
        if(d<LINK){ctx.strokeStyle=rgba(COL.base,(1-d/LINK)*.18);ctx.lineWidth=DPR;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();}}}
    if(mouse.on)for(const n of nodes){const d=Math.hypot(mouse.x-n.x,mouse.y-n.y);
      if(d<MR){const t=1-d/MR,c=t>.55?COL.rojo:(t>.3?COL.lima:COL.azul);
        ctx.strokeStyle=rgba(c,t*.5);ctx.lineWidth=1.1*DPR;ctx.beginPath();ctx.moveTo(mouse.x,mouse.y);ctx.lineTo(n.x,n.y);ctx.stroke();}}
    for(const n of nodes){let near=0;
      if(mouse.on){const d=Math.hypot(mouse.x-n.x,mouse.y-n.y);near=d<MR?1-d/MR:0;}
      ctx.fillStyle=near>.5?rgba(COL.rojo,.9):rgba(COL.base,.5+near*.4);
      ctx.beginPath();ctx.arc(n.x,n.y,(1.5+near*1.6)*DPR,0,7);ctx.fill();}
    for(let i=1;i<trail.length;i++){const p=trail[i-1],q=trail[i],t=i/trail.length;
      ctx.strokeStyle=rgba(COL.rojo,t*.5);ctx.lineWidth=(.5+t*3.5)*DPR;ctx.lineCap='round';
      ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.stroke();}
    if(mouse.on){const g=ctx.createRadialGradient(mouse.x,mouse.y,0,mouse.x,mouse.y,26*DPR);
      g.addColorStop(0,rgba(COL.lima,.55));g.addColorStop(1,rgba(COL.lima,0));
      ctx.fillStyle=g;ctx.beginPath();ctx.arc(mouse.x,mouse.y,26*DPR,0,7);ctx.fill();}
  }
  function step(){
    for(const n of nodes){n.x+=n.vx;n.y+=n.vy;
      if(n.x<0||n.x>W)n.vx*=-1; if(n.y<0||n.y>H)n.vy*=-1;
      if(mouse.on){const dx=mouse.x-n.x,dy=mouse.y-n.y,d=Math.hypot(dx,dy),MR=190*DPR;
        if(d<MR&&d>0){const f=(1-d/MR)*.035;n.vx+=dx/d*f;n.vy+=dy/d*f;}}
      n.vx*=.995;n.vy*=.995;
      const sp=Math.hypot(n.vx,n.vy),cap=.9*DPR; if(sp>cap){n.vx=n.vx/sp*cap;n.vy=n.vy/sp*cap;}}
    draw();
    if(trail.length>22)trail.shift();
    raf=requestAnimationFrame(step);
  }
  function play(){cancelAnimationFrame(raf);if(!reduce&&visible&&!document.hidden)raf=requestAnimationFrame(step);}
  function setMouse(x,y){const r=cv.getBoundingClientRect();mouse.x=(x-r.left)*DPR;mouse.y=(y-r.top)*DPR;mouse.on=true;trail.push({x:mouse.x,y:mouse.y});}
  if(!reduce){
    hero.addEventListener('mousemove',e=>setMouse(e.clientX,e.clientY));
    hero.addEventListener('mouseleave',()=>{mouse.on=false;trail=[];});
    hero.addEventListener('touchmove',e=>{const t=e.touches[0];if(t)setMouse(t.clientX,t.clientY);},{passive:true});
    document.addEventListener('visibilitychange',play);
    if('IntersectionObserver' in window)new IntersectionObserver(es=>{visible=es[0].isIntersecting;play();}).observe(hero);
  }
  let rt;addEventListener('resize',()=>{clearTimeout(rt);rt=setTimeout(()=>{resize();play();},150);});
  resize();play();
})();
