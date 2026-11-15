'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const A=require('../docs/forward-report.js');
const clock=i=>new Date(Date.parse('2026-11-15T00:00:00Z')+i*3600000).toISOString().slice(0,19)+'Z';
const date=i=>new Date(Date.parse('2026-11-15T00:00:00Z')+i*3600000).toISOString();
const row=(i,p,actual)=>({signal_time:clock(i),signal_index:i, predicted_at_utc:new Date(Date.parse(date(i+1))+5*60000).toISOString(),
  probability_up:p,actual_up:actual,scored_at_utc:actual===null?null:date(i+4),side:0});
const preds=[row(1,.8,true),row(2,.25,false),row(3,.4,null)];
function report() {
 const metrics=A.calculate(preds);const round=n=>n===null?null:Math.round(n*1e6)/1e6;
 return {schema:A.SCHEMA,status:'SYNTHETIC_TEST_ONLY',source_kind:'SYNTHETIC_TEST_FIXTURE',
  supervisor_status:'OBSERVED_SYNTHETIC_TEST_ONLY',monitor_status:'ACTIVE',
  frozen_at_utc:date(0),last_observed_at_utc:date(8),
  last_event_sha256:'a'.repeat(64),frozen_lock_sha256:'b'.repeat(64),quote_sha256:'c'.repeat(64),
  operation_events:3,observed_bars_since_freeze:4,on_time_bars:3,delayed_bars:1,alerts:0,
  predictions_truncated_for_view:0,predictions:preds.map(x=>({...x})),
  metrics:{overall:{...metrics,accuracy:round(metrics.accuracy),brier:round(metrics.brier)},
   recent_24:{...metrics,accuracy:round(metrics.accuracy),brier:round(metrics.brier)},
   recent_168:{...metrics,accuracy:round(metrics.accuracy),brier:round(metrics.brier)}},
  execution_assumption:'NO_BROKER_ORDERS_RESEARCH_ONLY',approved_for_live_trading:false,
  no_network_access:true,source_authenticity_independently_verified:false,timestamp_independently_attested:false};
}
const parse=x=>A.parse(JSON.stringify(x));
test('synthetic forward research report accepted with independent metric check',()=>{
 const r=parse(report());assert.equal(r.metrics.overall.scored,2);assert.equal(r.metrics.overall.pending,1);
 assert.equal(r.predictions.length,3);
});
test('refuse live order approval or unsupported source authenticity',()=>{
 const r=report();r.approved_for_live_trading=true;assert.throws(()=>parse(r),/研究専用/);
 r.approved_for_live_trading=false;r.source_authenticity_independently_verified=true;
 assert.throws(()=>parse(r),/研究専用/);
});
test('refuse dishonest accuracy or brier',()=>{
 const r=report();r.metrics.overall.accuracy=.17;assert.throws(()=>parse(r),/一致/);
 const n=report();n.metrics.recent_24.brier=.0;assert.throws(()=>parse(n),/一致/);
});
test('unscored predictions must have no scored timestamp',()=>{
 const r=report();r.predictions[2].scored_at_utc=date(7);
 assert.throws(()=>parse(r),/予測時刻/);
});
test('refuse future-leaking prediction timestamps',()=>{
 const r=report();r.predictions[0].predicted_at_utc=date(3);
 assert.throws(()=>parse(r),/予測時刻/);
});
test('refuse bad hashes, invalid timestamps, reversed indices',()=>{
 const r=report();r.quote_sha256='bad';assert.throws(()=>parse(r),/ハッシュ/);
 const q=report();q.predictions[1].signal_index=1;assert.throws(()=>parse(q),/予測/);
 const t=report();t.last_observed_at_utc='yesterday';assert.throws(()=>parse(t),/時刻/);
});
test('refuse inconsistent observed count',()=>{
 const r=report();r.delayed_bars=10;assert.throws(()=>parse(r),/観測件数/);
});
test('refuse oversized payload and invalid JSON',()=>{
 assert.throws(()=>A.parse('x'.repeat(1000001)),/上限/);
 assert.throws(()=>A.parse('{'),/読み取れません/);
});
test('blocked monitor report may be displayed but never approved',()=>{
 const r=report();r.supervisor_status='BLOCKED_MODEL_DRIFT_REVIEW_REQUIRED';r.monitor_status='PAUSED_MODEL_DRIFT_REVIEW_REQUIRED';
 assert.equal(parse(r).supervisor_status,r.supervisor_status);
});
