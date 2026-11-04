/* PeajeControl Go - fondo de particulas de la app (canvas #fondo-red de base.html).
   Red de puntos que se atrae suavemente al mouse/dedo, con lineas hacia el cursor, estela y brillo.
   Colores segun el tema (data-theme); al cambiar de tema, cambiarTema() llama a window.pcRedibujar.
   Tope ~30 fps, se pausa con la pestana oculta y respeta "reducir movimiento". */
(function(){
  var cv=document.getElementById('fondo-red'); if(!cv||!cv.getContext) return;
  var ctx=cv.getContext('2d'), W=0,H=0,DPR=1,nodes=[],raf=0,last=0,trail=[];
  var reduce=matchMedia('(prefers-reduced-motion:reduce)').matches;
  var ROJO='227,6,19', LIMA='223,255,0', AZUL='59,130,246';
  var mouse={x:-999,y:-999,on:false,moved:false};
  /* paleta por tema: nocturno usa rojo/lima/azul como el login; en luz la lima no se ve sobre fondo claro, se usa rojo/azul */
  function tema(){ var l=document.documentElement.getAttribute('data-theme')==='light';
    return l?{base:AZUL,linea:.36,punto:.75,c1:ROJO,c2:AZUL,c3:AZUL,brillo:AZUL,estela:ROJO,bA:.35}
            :{base:'148,163,184',linea:.3,punto:.8,c1:ROJO,c2:LIMA,c3:AZUL,brillo:LIMA,estela:ROJO,bA:.55}; }
  function resize(){
    DPR=Math.min(1.5,window.devicePixelRatio||1);
    W=cv.width=Math.floor(innerWidth*DPR); H=cv.height=Math.floor(innerHeight*DPR);
    var n=Math.min(58,Math.max(20,Math.round(innerWidth*innerHeight/24000))); nodes=[];
    for(var i=0;i<n;i++) nodes.push({x:Math.random()*W,y:Math.random()*H,
      vx:(Math.random()-.5)*.14*DPR,vy:(Math.random()-.5)*.14*DPR,c:Math.random()<.14?ROJO:(Math.random()<.14?AZUL:null)});
  }
  function draw(){
    ctx.clearRect(0,0,W,H); var T=tema(), L=190*DPR, MR=190*DPR, i, j, a, b, d, dx, dy, t, c, on=mouse.on&&!reduce;
    for(i=0;i<nodes.length;i++){ a=nodes[i];
      if(!reduce){ a.x+=a.vx; a.y+=a.vy; if(a.x<0||a.x>W)a.vx*=-1; if(a.y<0||a.y>H)a.vy*=-1;
        if(on){ dx=mouse.x-a.x; dy=mouse.y-a.y; d=Math.hypot(dx,dy);          /* atraccion suave hacia el cursor */
          if(d<MR&&d>1){ t=(1-d/MR)*.03*DPR; a.vx+=dx/d*t; a.vy+=dy/d*t; } }
        a.vx*=.995; a.vy*=.995; d=Math.hypot(a.vx,a.vy); t=.8*DPR;
        if(d>t){ a.vx=a.vx/d*t; a.vy=a.vy/d*t; } }
      for(j=i+1;j<nodes.length;j++){ b=nodes[j]; d=Math.hypot(a.x-b.x,a.y-b.y);
        if(d<L){ ctx.strokeStyle='rgba('+T.base+','+(Math.min(1,(1-d/L)*1.6)*T.linea).toFixed(3)+')'; ctx.lineWidth=DPR;
          ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y); ctx.stroke(); } } }
    if(on){                                                                 /* lineas nodo-cursor */
      for(i=0;i<nodes.length;i++){ a=nodes[i]; d=Math.hypot(mouse.x-a.x,mouse.y-a.y);
        if(d<MR){ t=1-d/MR; c=t>.55?T.c1:(t>.3?T.c2:T.c3);
          ctx.strokeStyle='rgba('+c+','+(t*.55).toFixed(3)+')'; ctx.lineWidth=1.1*DPR;
          ctx.beginPath(); ctx.moveTo(mouse.x,mouse.y); ctx.lineTo(a.x,a.y); ctx.stroke(); } } }
    for(i=0;i<nodes.length;i++){ a=nodes[i], t=0;
      if(on){ d=Math.hypot(mouse.x-a.x,mouse.y-a.y); t=d<MR?1-d/MR:0; }
      ctx.fillStyle='rgba('+(t>.5?T.c1:(a.c||T.base))+','+Math.min(1,T.punto+t*.2).toFixed(2)+')';
      ctx.beginPath(); ctx.arc(a.x,a.y,(1.9+t*1.6)*DPR,0,7); ctx.fill(); }
    if(on&&trail.length>1){                                                 /* estela (efecto de arrastre) */
      ctx.lineCap='round';
      for(i=1;i<trail.length;i++){ t=i/trail.length;
        ctx.strokeStyle='rgba('+T.estela+','+(t*.5).toFixed(3)+')'; ctx.lineWidth=(.5+t*3.5)*DPR;
        ctx.beginPath(); ctx.moveTo(trail[i-1].x,trail[i-1].y); ctx.lineTo(trail[i].x,trail[i].y); ctx.stroke(); } }
    if(on){                                                                 /* brillo que sigue al cursor */
      var g=ctx.createRadialGradient(mouse.x,mouse.y,0,mouse.x,mouse.y,26*DPR);
      g.addColorStop(0,'rgba('+T.brillo+','+T.bA+')'); g.addColorStop(1,'rgba('+T.brillo+',0)');
      ctx.fillStyle=g; ctx.beginPath(); ctx.arc(mouse.x,mouse.y,26*DPR,0,7); ctx.fill(); }
  }
  function loop(t){ raf=requestAnimationFrame(loop);
    if(t-last<33) return; last=t;            /* tope ~30 fps */
    if(mouse.moved){ trail.push({x:mouse.x,y:mouse.y}); mouse.moved=false; }   /* 1 punto de estela por cuadro */
    else if(trail.length) trail.shift();     /* la estela se desvanece sola */
    if(trail.length>22) trail.shift();
    draw(); }
  function start(){ if(reduce||raf) return; raf=requestAnimationFrame(loop); }
  function stop(){ if(raf){ cancelAnimationFrame(raf); raf=0; } }
  window.pcRedibujar=draw;                   /* al cambiar de tema se repinta (tambien con movimiento reducido) */
  resize(); draw();                          /* con movimiento reducido queda un cuadro estatico, sin interaccion */
  if(!reduce){
    /* el listener solo guarda coordenadas: el dibujo ocurre en el loop */
    var mover=function(x,y){ mouse.x=x*DPR; mouse.y=y*DPR; mouse.on=true; mouse.moved=true; };
    addEventListener('mousemove',function(e){ mover(e.clientX,e.clientY); },{passive:true});
    addEventListener('touchmove',function(e){ var p=e.touches[0]; if(p) mover(p.clientX,p.clientY); },{passive:true});
    document.documentElement.addEventListener('mouseleave',function(){ mouse.on=false; trail=[]; });
    addEventListener('touchend',function(){ mouse.on=false; trail=[]; });
    start();
    document.addEventListener('visibilitychange',function(){ document.hidden?stop():start(); });
  }
  var rt; addEventListener('resize',function(){ clearTimeout(rt); rt=setTimeout(function(){ resize(); draw(); },200); });
})();
