'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const M=require('../docs/chart-model.js');

test('zoom in narrows viewport without editing source OHLC',()=>{
 const previous={start:0,end:100};
 const result=M.zoomRange(previous,100,.75);
 assert.deepEqual(result,{start:13,end:88});
 assert.deepEqual(previous,{start:0,end:100});
});
test('anchored zoom keeps the left observation under the cursor',()=>{
 assert.deepEqual(M.zoomRange({start:40,end:120},200,.5,0),{start:40,end:80});
});
test('anchored zoom keeps the right observation under the cursor',()=>{
 assert.deepEqual(M.zoomRange({start:40,end:120},200,.5,1),{start:80,end:120});
});
test('zoom out never exceeds known history',()=>{
 assert.deepEqual(M.zoomRange({start:10,end:35},35,10),{start:0,end:35});
});
test('minimum candle window is eight records',()=>{
 const r=M.zoomRange({start:10,end:110},200,.001);
 assert.equal(r.end-r.start,8);
});
test('zero and short series are safe and bounded',()=>{
 assert.deepEqual(M.zoomRange(null,0,.8),{start:0,end:0});
 assert.deepEqual(M.zoomRange(null,3,.5),{start:0,end:3});
});
test('panning clamps at both edges',()=>{
 assert.deepEqual(M.panRange({start:10,end:30},100,-100),{start:0,end:20});
 assert.deepEqual(M.panRange({start:10,end:30},100,300),{start:80,end:100});
});
test('invalid zoom values are rejected',()=>{
 assert.throws(()=>M.zoomRange({start:0,end:30},100,0),/Invalid chart zoom/);
 assert.throws(()=>M.zoomRange({start:0,end:30},100,Infinity),/Invalid chart zoom/);
 assert.throws(()=>M.panRange({start:0,end:30},100,NaN),/Invalid chart pan/);
});
test('chart view buttons API, mouse wheel, keyboard, drag, pinch share bounded ranges',()=>{
 globalThis.AegisChartModel=M;
 require('../docs/chart-view.js');
 const chart=globalThis.AegisChartView;
 const events={},canvas={
  dataset:{},parentElement:{},clientWidth:800,clientHeight:360,
  getContext(){return null},
  getBoundingClientRect(){return {left:0,top:0,width:800,height:360}},
  addEventListener(name,fn){events[name]=fn},
  setPointerCapture(){},
 };
 const start=Date.parse('2025-01-01T00:00:00Z');
 const reference=Array.from({length:200},(_,i)=>({date:new Date(start+i*86400000).toISOString().slice(0,10),close:145+i*.01}));
 chart.draw(canvas,{plot:'price',window:'all',reference,showEma:false});
 let redraws=0;chart.install(canvas,()=>{redraws++});
 const before=chart.viewportInfo();
 assert.equal(before.end-before.start,200);
 assert.equal(chart.zoom(.75),true);
 let narrowed=chart.viewportInfo();
 assert.equal(narrowed.end-narrowed.start,150);
 let prevented=false;
 events.wheel({deltaY:-1,clientX:400,preventDefault(){prevented=true}});
 assert.ok(prevented);
 assert.ok(chart.viewportInfo().end-chart.viewportInfo().start<150);
 events.keydown({key:'-',preventDefault(){}});
 const increased=chart.viewportInfo().end-chart.viewportInfo().start;
 assert.ok(increased>0&&increased<=200);
 const pointer=(pointerId,clientX,clientY=200)=>({pointerId,clientX,clientY,pointerType:'touch',button:0});
 events.pointerdown(pointer(1,270));
 events.pointerdown(pointer(2,470));
 const pinchedBefore=chart.viewportInfo().end-chart.viewportInfo().start;
 events.pointermove(pointer(2,570));
 const pinchedAfter=chart.viewportInfo().end-chart.viewportInfo().start;
 assert.ok(pinchedAfter<=pinchedBefore);
 events.pointerup(pointer(2,570));events.pointerup(pointer(1,270));
 chart.reset();chart.draw(canvas,{plot:'price',window:'all',reference,showEma:false});
 assert.equal(chart.viewportInfo().end-chart.viewportInfo().start,200);
 assert.ok(redraws>0);
});
