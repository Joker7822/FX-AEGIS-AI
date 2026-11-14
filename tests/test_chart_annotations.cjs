'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const A=require('../docs/annotations.js');
const H=3600000,hash='a'.repeat(64),start=Date.parse('2024-04-01T00:00:00Z');
const time=i=>new Date(start+i*H).toISOString().slice(0,19)+'Z';
const bars=Array.from({length:12},(_,i)=>({time:time(i),ts:start+i*H,
  bid:{open:150+i*.02},ask:{open:150.01+i*.02}}));
const meta=(trades=[],predictions=[])=>JSON.stringify({schema:'fx-aegis-h1-research-overlay-v1',
  quotes_sha256:hash,approved_for_live_trading:false,
  execution_assumption:'H1_OPEN_HYPOTHETICAL',trades,predictions});
const trade={id:'T1',side:'long',signal_time:time(1),entry_time:time(2),exit_time:time(3),
  entry_price:150.05,exit_price:150.06,stop_loss:149.50,take_profit:151.0};
const pred={signal_time:time(1),probability_up:.8};
test('valid overlay binds to the exact H1 quote digest',()=>{
  const r=A.parse(meta([trade],[pred]),hash,bars);
  assert.equal(r.summary.trades,1);assert.equal(r.summary.predictions,1);
  assert.equal(r.summary.accuracy,1);assert.ok(r.summary.brier>=0);
});
test('future actual labels come only from Bid/Ask next opens, never JSON claims',()=>{
  const badInput={...pred,actual_up:false,correct:false};
  const a=A.parse(meta([],[badInput]),hash,bars);
  assert.equal(a.predictions[0].actualUp,true);
  assert.equal(a.predictions[0].correct,true);
});
test('changing future prices changes only their dependent realized label',()=>{
  const a=A.parse(meta([],[pred]),hash,bars);
  const newBars=bars.map(b=>({...b,bid:{...b.bid},ask:{...b.ask}}));
  newBars[3].bid.open=100;newBars[3].ask.open=100.01;
  const b=A.parse(meta([],[pred]),hash,newBars);
  assert.notEqual(a.predictions[0].actualUp,b.predictions[0].actualUp);
  assert.equal(a.predictions[0].probabilityUp,b.predictions[0].probabilityUp);
});
test('SHA mismatch blocks overlay before drawing anything',()=>{
  assert.throws(()=>A.parse(meta([trade]),'b'.repeat(64),bars),/SHA-256/);
});
test('live trading approval and missing assumptions are refused',()=>{
  const m=JSON.parse(meta([trade]));m.approved_for_live_trading=true;
  assert.throws(()=>A.parse(JSON.stringify(m),hash,bars),/研究専用/);
  delete m.execution_assumption;m.approved_for_live_trading=false;
  assert.throws(()=>A.parse(JSON.stringify(m),hash,bars),/研究専用/);
});
test('no entry on signal candle / timestamp gaps',()=>{
  assert.throws(()=>A.parse(meta([{...trade,entry_time:time(1)}]),hash,bars),/次足/);
  const gap=bars.filter(b=>b.time!==time(2));
  assert.throws(()=>A.parse(meta([{...trade,entry_time:time(3),exit_time:time(4)}]),hash,gap),/次足/);
});
test('stop-loss and take-profit must agree with trade direction',()=>{
  assert.throws(()=>A.parse(meta([{...trade,stop_loss:151}]),hash,bars),/損切り/);
  assert.throws(()=>A.parse(meta([{...trade,take_profit:149}]),hash,bars),/利確/);
});
test('duplicate IDs, invalid price and probabilities are rejected',()=>{
  assert.throws(()=>A.parse(meta([trade,trade]),hash,bars),/重複/);
  assert.throws(()=>A.parse(meta([{...trade,entry_price:500}]),hash,bars),/乖離/);
  assert.throws(()=>A.parse(meta([],[{...pred,probability_up:1.2}]),hash,bars),/確率/);
});
test('future labels are only computed when two future candles exist',()=>{
  assert.throws(()=>A.parse(meta([],[{...pred,signal_time:time(10)}]),hash,bars),/後続2本/);
});
test('duplicate prediction at one signal time is refused',()=>{
  assert.throws(()=>A.parse(meta([],[pred,pred]),hash,bars),/重複/);
});
