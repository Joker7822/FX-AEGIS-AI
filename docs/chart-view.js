/* Zero-dependency, read-only canvas chart. Local quotes never leave the browser. */
'use strict';
(function (root) {
  const M = root.AegisChartModel;
  let viewport=null, key='', config=null, hitBoxes=[], hoverX=null, dragging=null, pinch=null;
  const activePointers=new Map();
  const AXIS_RIGHT=61, AXIS_LEFT=12, AXIS_TOP=14, AXIS_BOTTOM=28;
  const color={up:'#68d8ba',down:'#ee8c87',line:'#71d5cf',gold:'#e8c889',purple:'#a3a1fa',grid:'#283c48',text:'#9eb4c1'};
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  const fmt=(x,d=3)=>Number(x).toLocaleString('ja-JP',{minimumFractionDigits:d,maximumFractionDigits:d});

  function reset(){viewport=null;key='';hoverX=null;dragging=null;pinch=null;activePointers.clear();}

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
    if(style==='candles'&&c.annotations){
      const a=c.annotations,first=plotBars[0].ts,last=visible.at(-1).ts;
      const bucket=ts=>{
        if(ts<first||ts>last)return -1;
        let lo=0,hi=plotBars.length;
        while(lo<hi){const m=(lo+hi)>>1;if(plotBars[m].ts<=ts)lo=m+1;else hi=m;}
        return lo-1;
      };
      const marker=(ts,price,tint,shape)=>{
        const j=bucket(ts);if(j<0||!Number.isFinite(price)||price<lo||price>hi)return;
        const px=x(j),py=y(price);ctx.save();ctx.strokeStyle=ctx.fillStyle=tint;ctx.lineWidth=1.8;
        ctx.beginPath();
        if(shape==='up'){ctx.moveTo(px,py-8);ctx.lineTo(px-5,py+2);ctx.lineTo(px+5,py+2);}
        else if(shape==='down'){ctx.moveTo(px,py+8);ctx.lineTo(px-5,py-2);ctx.lineTo(px+5,py-2);}
        else {ctx.arc(px,py,4,0,2*Math.PI);}
        if(shape==='dot')ctx.stroke();else {ctx.closePath();ctx.fill();}
        ctx.restore();
      };
      if(c.showLevels){
        for(const t of a.trades.slice(0,300)){
          const a0=bucket(t.entryTs),a1=bucket(t.exitTs);
          if(a0<0&&a1<0)continue;
          const left=x(Math.max(0,a0)),right=x(Math.min(plotBars.length-1,Math.max(0,a1)));
          if(left>right)continue;
          for(const [price,tint] of [[t.stopLoss,'#f18e86'],[t.takeProfit,'#6fddaa']]){
            if(price===null||price<lo||price>hi)continue;
            ctx.save();ctx.strokeStyle=tint;ctx.globalAlpha=.68;ctx.setLineDash([4,4]);
            ctx.beginPath();ctx.moveTo(left,y(price));ctx.lineTo(right,y(price));ctx.stroke();ctx.restore();
          }
        }
      }
      if(c.showTrades){
        for(const t of a.trades.slice(0,600)){
          const tint=t.side==='long'?'#69e2b1':'#ffa28c';
          marker(t.entryTs,t.entry,tint,t.side==='long'?'up':'down');
          marker(t.exitTs,t.exit,tint,'dot');
        }
      }
      if(c.showPredictions){
        const preds=a.predictions,step=Math.max(1,Math.ceil(preds.length/320));
        for(let i=0;i<preds.length;i+=step){
          const p=preds[i],j=bucket(p.signalTs);
          if(j<0)continue;
          const value=plotBars[j].close;
          marker(p.signalTs,value,p.correct?'#a8d3f4':'#e6b96b','dot');
        }
      }
    }
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

  function zoom(factor,anchorFraction=.5) {
    if(!config)return false;
    const all=model(config).filtered.length;
    if(all<3)return false;
    viewport=M.zoomRange(viewport,all,factor,anchorFraction);
    hoverX=null;
    return true;
  }
  function viewportInfo() {
    if(!config)return {start:0,end:0,total:0};
    const all=model(config).filtered.length;
    return {start:viewport.start,end:viewport.end,total:all};
  }

  function install(canvas,redraw) {
    if(canvas.dataset.chartWired)return;
    canvas.dataset.chartWired='1';
    const positionFraction=clientX=>{
      const box=canvas.getBoundingClientRect();
      return clamp((clientX-box.left-AXIS_LEFT)/Math.max(10,box.width-AXIS_RIGHT-AXIS_LEFT),0,1);
    };
    const touchDistance=(a,b)=>Math.hypot(a.x-b.x,a.y-b.y);
    // Pinch is reserved for the smartphone/coarse-pointer layout.
    // Desktop mouse, stylus, and touch laptops keep drag/wheel/buttons.
    const mobilePinchAllowed=()=>typeof root.matchMedia==='function' &&
      root.matchMedia('(max-width: 900px) and (pointer: coarse)').matches;
    const pointerStop=e=>{
      activePointers.delete(e.pointerId);
      pinch=null;
      dragging=null;
      if(activePointers.size===1 && viewport){
        const remaining=activePointers.values().next().value;
        dragging={x:remaining.x,start:viewport.start};
      }
    };
    canvas.addEventListener('pointerdown',e=>{
      if(!config||!viewport||(e.pointerType==='mouse' && e.button!==0))return;
      activePointers.set(e.pointerId,{x:e.clientX,y:e.clientY,touch:e.pointerType==='touch'});
      canvas.setPointerCapture?.(e.pointerId);
      if(activePointers.size===1){
        dragging={x:e.clientX,start:viewport.start};
      }else if(activePointers.size===2){
        const [a,b]=Array.from(activePointers.values());
        pinch=(mobilePinchAllowed()&&a.touch&&b.touch)
          ?{distance:Math.max(1,touchDistance(a,b)),base:{...viewport},
            anchorFraction:positionFraction((a.x+b.x)/2)}
          :null;
        dragging=null;
      }
    });
    canvas.addEventListener('pointermove',e=>{
      if(activePointers.has(e.pointerId))
        activePointers.set(e.pointerId,{x:e.clientX,y:e.clientY,touch:e.pointerType==='touch'});
      if(activePointers.size>=2&&pinch&&mobilePinchAllowed()&&config){
        const [a,b]=Array.from(activePointers.values());
        const distance=Math.max(1,touchDistance(a,b));
        const total=model(config).filtered.length;
        viewport=M.zoomRange(pinch.base,total,pinch.distance/distance,pinch.anchorFraction);
        // A moving pinch midpoint pans the content without shifting source times.
        const moveFraction=positionFraction((a.x+b.x)/2);
        const delta=(pinch.anchorFraction-moveFraction)*(viewport.end-viewport.start);
        viewport=M.panRange(viewport,total,delta);
      }else if(activePointers.size===1&&dragging&&activePointers.has(e.pointerId)&&config){
        const total=model(config).filtered.length;
        const span=viewport.end-viewport.start;
        const delta=Math.round((dragging.x-e.clientX)*span/Math.max(1,canvas.clientWidth-AXIS_RIGHT-AXIS_LEFT));
        viewport=M.panRange({start:dragging.start,end:dragging.start+span},total,delta);
      }
      hoverX=activePointers.size>=2?null:e.clientX-canvas.getBoundingClientRect().left;
      redraw();
    });
    canvas.addEventListener('pointerleave',()=>{if(!activePointers.size)hoverX=null;redraw();});
    for(const event of ['pointerup','pointercancel','lostpointercapture'])
      canvas.addEventListener(event,pointerStop);
    canvas.addEventListener('wheel',e=>{
      if(!config)return;
      e.preventDefault();
      if(zoom(e.deltaY>0?1.27:.79,positionFraction(e.clientX)))redraw();
    },{passive:false});
    canvas.addEventListener('keydown',e=>{
      const accepted=['ArrowLeft','ArrowRight','+','=','-','_','Home','0'];
      if(!config||!viewport||!accepted.includes(e.key))return;
      e.preventDefault();
      const all=model(config).filtered.length;
      const span=viewport.end-viewport.start;
      if(e.key==='0')reset();
      else if(e.key==='Home')viewport={start:Math.max(0,all-span),end:all};
      else if(e.key==='ArrowLeft'||e.key==='ArrowRight')
        viewport=M.panRange(viewport,all,(e.key==='ArrowLeft'?-1:1)*Math.max(1,Math.round(span*.15)));
      else zoom(e.key==='+'||e.key==='='?.79:1.27);
      redraw();
    });
  }
  root.AegisChartView={draw,reset,install,zoom,viewportInfo};
})(typeof globalThis==='object'?globalThis:this);
