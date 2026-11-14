'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {TextEncoder} = require('node:util');
const M=require('../docs/chart-model.js');

function example(rows=72){
 const out=[M.HEADER.join(',')];const start=Date.parse('2024-01-01T00:00:00Z');
 for(let i=0;i<rows;i++){
  const bid=142+i*.002,ask=bid+.01;
  const f=n=>n.toFixed(3);
  out.push([new Date(start+i*3600000).toISOString().slice(0,19)+'Z',f(bid),f(bid+.012),f(bid-.013),f(bid+.004),f(ask),f(ask+.013),f(ask-.012),f(ask+.004)].join(','));
 }
 return out.join('\n')+'\n';
}
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
const meta=s=>({schema:'fx-aegis-h1-bidask-v1',symbol:'USDJPY',quote_timezone:'UTC',timestamp_semantics:'BAR_OPEN',source_kind:'BROKER_BID_ASK_H1',source_name:'user supplied, not verified',sha256:sha(s)});

test('valid H1 CSV uses real bid and ask separately',()=>{
 const parsed=M.parseH1Csv(example());
 assert.equal(parsed.bars.length,72);assert.equal(parsed.gaps,0);
 const b=parsed.bars[0];assert.equal(b.bid.open,142);assert.equal(b.ask.open,142.01);
});
test('bid and ask cannot be swapped',()=>{
 const s=example().replace('142.010,142.023,141.998,142.014','141.000,141.013,140.988,141.004');
 assert.throws(()=>M.parseH1Csv(s),/整合/);
});
test('no empty prices or invalid timestamps',()=>{
 assert.throws(()=>M.parseH1Csv(example().replace(',142.000,',',,')),/価格/);
 assert.throws(()=>M.parseH1Csv(example().replace('2024-01-01T00:00:00Z','2024-01-01T00:00:00')),/UTC/);
 assert.throws(()=>M.parseH1Csv(example().replace('2024-01-01T01:00:00Z','2024-01-01T00:00:00Z')),/重複/);
});
test('stated provenance cannot substitute checksum',()=>{
 const s=example();const m=meta(s);assert.equal(M.validateProvenance(m,m.sha256).synthetic,false);
 assert.throws(()=>M.validateProvenance(m,'0'.repeat(64)),/SHA-256/);
 assert.throws(()=>M.validateProvenance({...m,schema:'fred-reference'},m.sha256),/出典/);
});
test('browser loader hashes local files and does not request network',async()=>{
 const s=example();
 const fake=(name,text)=>({name,size:new TextEncoder().encode(text).byteLength,text:async()=>text,arrayBuffer:async()=>new TextEncoder().encode(text).buffer});
 const loaded=await M.loadLocalFiles([fake('bidask_h1.csv',s),fake('provenance.json',JSON.stringify(meta(s)))],crypto.webcrypto);
 assert.equal(loaded.bars.length,72);assert.equal(loaded.sha256,sha(s));
 await assert.rejects(()=>M.loadLocalFiles([fake('bidask_h1.csv',s),fake('provenance.json',JSON.stringify(meta(s.replace('142.000','142.001'))))],crypto.webcrypto),/SHA-256/);
});
test('D1 daily aggregation uses only underlying hourly OHLC',()=>{
 const bars=M.parseH1Csv(example()).bars;
 const d=M.aggregates(bars,'D1','ask');
 assert.equal(d.length,3);assert.equal(d[0].sourceCount,24);
 assert.equal(d[0].open,bars[0].ask.open);assert.equal(d[0].close,bars[23].ask.close);
 assert.equal(d[0].high,Math.max(...bars.slice(0,24).map(b=>b.ask.high)));
});
test('EMA is causal: changing the future never changes the prefix',()=>{
 const vals=[...Array(60)].map((_,i)=>({close:1+i*.1}));
 const before=M.ema(vals,20);
 vals[59].close=700;
 const after=M.ema(vals,20);
 assert.deepEqual(before.slice(0,-1),after.slice(0,-1));
});
test('long display density uses real observed extrema, not invented prices',()=>{
 const vals=M.aggregates(M.parseH1Csv(example()).bars,'H1','bid');
 const compact=M.compressOHLC(vals,14);
 assert.ok(compact.length<=14);
 assert.equal(compact[0].open,vals[0].open);
 assert.equal(compact.at(-1).close,vals.at(-1).close);
 assert.equal(Math.max(...compact.map(p=>p.high)),Math.max(...vals.map(p=>p.high)));
 assert.equal(Math.min(...compact.map(p=>p.low)),Math.min(...vals.map(p=>p.low)));
 assert.ok(compact.some(p=>p.sourceCount>1));
});
test('window filters by timestamps rather than year-string approximation',()=>{
 const v=M.aggregates(M.parseH1Csv(example()).bars);
 const limited=M.filterWindow(v,'1d');assert.ok(limited.length>1&&limited.length<v.length);
 assert.equal(M.filterWindow(v,'all').length,v.length);
});
