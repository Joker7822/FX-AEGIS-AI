/* Zero-dependency, read-only canvas chart. Local quotes never leave the browser. */
'use strict';
(function (root) {
  const M = root.AegisChartModel;
  let viewport=null, key='', config=null, hitBoxes=[], hoverX=null, dragging=null;
  const AXIS_RIGHT=61, AXIS_LEFT=12, AXIS_TOP=14, AXIS_BOTTOM=28;
  const color={up:'#68d8ba',down:'#ee8c87',line:'#71d5cf',gold:'#e8c889',purple:'#a3a1fa',grid:'#283c48',text:'#9eb4c1'};
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  const fmt=(x,d=3)=>Number(x).toLocaleString('ja-JP',{minimumFractionDigits:d,maximumFractionDigits:d});

  function reset(){viewport=null;key='';hoverX=null;}

  function series(c) {
    if(c.plot==='candles') {
      const raw=M.aggregates(c.localBars||[],c.timeframe,c.side);
      const m20=M.ema(raw,20),m50=M.ema(raw,50);
      return {points:raw.map((p,i)=>({...p,ma20:m20[i],ma50:m50[i]})),style:'candles',currency:'JPY'};
    }
    const raw=c.plot==='equity'?(c.equity||[]):(c.reference||[]);
    const m20=M.ema(raw,20),m50=M.ema(raw,50);
    return {points:raw.map((p,i)=>({date:p.date,ts:Date.parse(p.date+'T00:00:00Z'),close:p.close,ma20:m20[i],ma50:m50[i]})),
      style:'line',currency:c.plot==='equity'?'JPY':'JPY/USD'};
  }

  function model(c) {
    const content=series(c);
    const filtered=M.filterWindow(content.points,c.window);
    const nextKey=[c.plot,c.window,c.timeframe,c.side,filtered.length,filtered.at(-1)?.date||''].join('|');
    if(nextKey!==key){viewport=null;key=nextKey;}
    if(!viewport)viewport={start:0,end:filtered.length};
    viewport.start=clamp(viewport.start,0,Math.max(0,filtered.length-2));
    viewport.end=clamp(viewport.end,viewport.start+2,filtered.length);
    return {filtered,visible:filtered.slice(viewport.start,viewport.end),style:content.style,currency:content.currency};
  }

  function draw(canvas,c) {
    config=c;
    const {filtered,visible,style,currency}=model(c);
    const host=canvas.parentElement, width=Math.max(180,canvas.clientWidth),height=Math.max(135,canvas.clientHeight);
    const ratio=Math.min(2,root.devicePixelRatio||1),ctx=canvas.getContext('2d');
    if(!ctx)return {available:false,count:0};
    canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
    ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);
    const empty=visible.length<2;
    hitBoxes=[];
    if(empty){if(root.document?.getElementById('chartHover'))root.document.getElementById('chartHover').hidden=true;return {available:false,count:visible.length,total:filtered.length};}
    const plotW=Math.max(10,width-AXIS_LEFT-AXIS_RIGHT),plotH=Math.max(10,height-AXIS_TOP-AXIS_BOTTOM);
    const maxCandles=Math.max(20,Math.floor(plotW/6));
    const plotBars=style==='candles'?M.compressOHLC(visible,maxCandles):visible;
    let values=style==='candles'?plotBars.flatMap(b=>[b.low,b.high]):plotBars.map(b=>b.close);
    if(c.showEma&&c.plot!=='equity')values=values.concat(plotBars.flatMap(b=>[b.ma20,b.ma50].filter(Number.isFinite)));
    const rawMin=Math.min(...values),rawMax=Math.max(...values),margin=(rawMax-rawMin||Math.max(1,rawMax*.01))*.09;
    const lo=rawMin-margin,hi=rawMax+margin;
    const y=value=>AXIS_TOP+(hi-value)/(hi-lo)*plotH;
    const x=i=>AXIS_LEFT+(i+.5)*plotW/plotBars.length;
    ctx.font='11px Segoe UI,Arial,sans-serif';ctx.textBaseline='middle';
    for(let i=0;i<=4;i++){
      const yy=AXIS_TOP+i*plotH/4;
      ctx.strokeStyle=color.grid;ctx.lineWidth=.65;
      ctx.beginPath();ctx.moveTo(AXIS_LEFT,yy);ctx.lineTo(width-AXIS_RIGHT+2,yy);ctx.stroke();
      ctx.fillStyle=color.text;ctx.textAlign='right';ctx.fillText(fmt(hi-(hi-lo)*i/4,style==='line'&&c.plot==='equity'?0:3),width-5,yy);
    }
    ctx.save();ctx.beginPath();ctx.rect(AXIS_LEFT,AXIS_TOP,plotW,plotH);ctx.clip();
    if(style==='candles'){
      const candleW=clamp(plotW/plotBars.length*.65,1.5,15);
      for(let i=0;i<plotBars.length;i++){
        const b=plotBars[i],cx=x(i),up=b.close>=b.open;
        ctx.strokeStyle=ctx.fillStyle=up?color.up:color.down;
        ctx.lineWidth=1.1;ctx.beginPath();ctx.moveTo(cx,y(b.high));ctx.lineTo(cx,y(b.low));ctx.stroke();
        const top=y(Math.max(b.open,b.close)),bottom=y(Math.min(b.open,b.close));
        ctx.fillRect(cx-candleW/2,top,candleW,Math.max(1.2,bottom-top));
        hitBoxes.push({x:cx,record:b});
      }
    }else{
      const path=()=>{ctx.beginPath();plotBars.forEach((b,i)=>{const xx=x(i),yy=y(b.close);if(i===0)ctx.moveTo(xx,yy);else ctx.lineTo(xx,yy);});};
      path();ctx.strokeStyle=c.plot==='equity'?color.gold:color.line;ctx.lineWidth=1.8;ctx.stroke();
      for(let i=0;i<plotBars.length;i++)hitBoxes.push({x:x(i),record:plotBars[i]});
    }
    function overlay(field,stroke){
      if(!c.showEma||c.plot==='equity')return;
      ctx.beginPath();let started=false;
      for(let i=0;i<plotBars.length;i++){
        const value=plotBars[i][field];if(!Number.isFinite(value)){started=false;continue;}
        if(!started){ctx.moveTo(x(i),y(value));started=true;}else ctx.lineTo(x(i),y(value));
      }
      ctx.lineWidth=1.3;ctx.strokeStyle=stroke;ctx.stroke();
    }
    overlay('ma20',color.gold);overlay('ma50',color.purple);
    if(hoverX!==null && hitBoxes.length){
      const index=clamp(Math.round((hoverX-AXIS_LEFT)/plotW*hitBoxes.length-.5),0,hitBoxes.length-1);
      const hit=hitBoxes[index];ctx.strokeStyle='#9bc8dc90';ctx.lineWidth=1;ctx.setLineDash([4,4]);
      ctx.beginPath();ctx.moveTo(hit.x,AXIS_TOP);ctx.lineTo(hit.x,AXIS_TOP+plotH);ctx.stroke();ctx.setLineDash([]);
      const tip=root.document?.getElementById('chartHover');
      if(tip){const b=hit.record,span=b.lastDate?`${b.date} ～ ${b.lastDate}`:b.date;
        tip.textContent=style==='candles'?`${span} UTC | 始 ${fmt(b.open)} 高 ${fmt(b.high)} 安 ${fmt(b.low)} 終 ${fmt(b.close)}${b.sourceCount>1?` | 集約${b.sourceCount}本`:''}${b.partial?' | 一部時間欠損':''}`:`${span} | ${fmt(b.close,c.plot==='equity'?0:3)} ${currency}`;
        tip.hidden=false;
      }
    }else{const tip=root.document?.getElementById('chartHover');if(tip)tip.hidden=true;}
    ctx.restore();
    ctx.fillStyle=color.text;ctx.font='10px Segoe UI,Arial,sans-serif';ctx.textBaseline='bottom';
    const xLabels=[0,Math.floor((plotBars.length-1)/2),plotBars.length-1];
    for(let i=0;i<xLabels.length;i++){
      const j=xLabels[i];ctx.textAlign=i===0?'left':i===2?'right':'center';
      const label=plotBars[j].date.slice(0,16).replace('T',' ');
      ctx.fillText(label,i===0?AXIS_LEFT:i===2?width-AXIS_RIGHT:x(j),height-4);
    }
    return {available:true,count:visible.length,total:filtered.length,compressed:style==='candles'&&plotBars.length<visible.length,
      first:visible[0].date,last:visible.at(-1).date};
  }

  function install(canvas,redraw){
    if(canvas.dataset.chartWired)return;canvas.dataset.chartWired='1';
    canvas.addEventListener('pointermove',e=>{
      if(dragging){
        const total=viewport.end-viewport.start;
        const move=Math.round((dragging.x-e.clientX)*total/Math.max(1,canvas.clientWidth-AXIS_RIGHT-AXIS_LEFT));
        const full=model(config).filtered.length;
        const start=clamp(dragging.start+move,0,Math.max(0,full-total));
        viewport={start,end:start+total};
      }
      hoverX=e.clientX-canvas.getBoundingClientRect().left;redraw();
    });
    canvas.addEventListener('pointerleave',()=>{if(!dragging)hoverX=null;redraw();});
    canvas.addEventListener('pointerdown',e=>{
      if(e.button!==0||!viewport)return;
      dragging={x:e.clientX,start:viewport.start};canvas.setPointerCapture?.(e.pointerId);
    });
    for(const event of ['pointerup','pointercancel'])canvas.addEventListener(event,()=>{dragging=null;});
    canvas.addEventListener('wheel',e=>{
      if(!config)return;
      e.preventDefault();const all=model(config).filtered.length;
      if(all<3)return;
      const n=viewport.end-viewport.start;
      const target=clamp(Math.round(n*(e.deltaY>0?1.27:.79)),Math.min(12,all),all);
      const box=canvas.getBoundingClientRect();
      const fraction=clamp((e.clientX-box.left-AXIS_LEFT)/Math.max(10,box.width-AXIS_RIGHT-AXIS_LEFT),0,1);
      const anchor=viewport.start+fraction*n;
      const start=clamp(Math.round(anchor-fraction*target),0,all-target);
      viewport={start,end:start+target};redraw();
    },{passive:false});
    canvas.addEventListener('keydown',e=>{
      if(!config||!viewport||!['ArrowLeft','ArrowRight','+','-','Home'].includes(e.key))return;
      e.preventDefault();const all=model(config).filtered.length,n=viewport.end-viewport.start;
      if(e.key==='Home'){viewport={start:Math.max(0,all-n),end:all};}
      else if(e.key==='ArrowLeft'||e.key==='ArrowRight'){
        const shift=(e.key==='ArrowLeft'?-1:1)*Math.max(1,Math.round(n*.15));
        const start=clamp(viewport.start+shift,0,all-n);viewport={start,end:start+n};
      }else{
        const target=clamp(Math.round(n*(e.key==='+'?.79:1.27)),Math.min(12,all),all);
        const start=clamp(Math.round((viewport.start+viewport.end-target)/2),0,all-target);
        viewport={start,end:start+target};
      }redraw();
    });
  }
  root.AegisChartView={draw,reset,install};
})(typeof globalThis==='object'?globalThis:this);
