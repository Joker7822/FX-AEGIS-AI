'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const M=require('../docs/chart-model.js');
const root=path.resolve(__dirname,'../docs');
const csv=fs.readFileSync(path.join(root,'demo/bidask_h1_synthetic.csv'),'utf8');
const meta=JSON.parse(fs.readFileSync(path.join(root,'demo/provenance.json'),'utf8'));

test('bundled H1 demo is explicitly synthetic, and SHA-256 verified',()=>{
 assert.equal(meta.source_kind,'SYNTHETIC_TEST_FIXTURE');
 assert.match(meta.source_name,/NOT MARKET DATA/);
 const sha=crypto.createHash('sha256').update(csv).digest('hex');
 assert.equal(sha,meta.sha256);
 assert.equal(M.validateProvenance(meta,sha).synthetic,true);
});
test('bundled demo is 168 real OHLC-shaped artificial hourly bars',()=>{
 const result=M.parseH1Csv(csv);
 assert.equal(result.bars.length,168);
 assert.equal(result.gaps,0);
 const candles=M.aggregates(result.bars,'H1','bid');
 assert.equal(candles.length,168);
 assert.ok(candles.some(c=>c.close<c.open));
 assert.ok(candles.some(c=>c.close>c.open));
});
test('browser-format demo files pass the same SHA verifier as private user uploads',async()=>{
 const {TextEncoder}=require('node:util');
 const file=(name,text)=>({name,size:Buffer.byteLength(text),arrayBuffer:async()=>new TextEncoder().encode(text).buffer,text:async()=>text});
 const checked=await M.loadLocalFiles([file('demo.csv',csv),file('provenance.json',JSON.stringify(meta))],crypto.webcrypto);
 assert.equal(checked.bars.length,168);
 assert.equal(checked.meta.synthetic,true);
});
test('browser UI requests demo and unmistakably labels it synthetic',()=>{
 const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
 const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
 assert.match(html,/id="chartDataBadge"/);
 assert.match(html,/人工デモ・実勢価格ではありません/);
 assert.match(app,/loadDemoH1\(\);/);
 assert.match(app,/checked\.meta\.synthetic!==true/);
 assert.match(app,/if\(customImportPending\|\|state\.localSha\)return/);
 assert.match(app,/overlayControls'\)\.hidden=!local\|\|demo/);
});
test('demo H1 can be aggregated for D1 chart without invented values',()=>{
 const raw=M.parseH1Csv(csv).bars;
 const d=M.aggregates(raw,'D1','bid');
 assert.equal(d.length,7);
 assert.equal(d.reduce((n,x)=>n+x.sourceCount,0),168);
 assert.ok(d.every(x=>x.sourceCount===24&&!x.partial));
});
