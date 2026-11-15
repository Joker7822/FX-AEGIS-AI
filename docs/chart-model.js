/* FX AEGIS AI: local-only USDJPY H1 quote viewer; no broker transport. */
'use strict';
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.AegisChartModel = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  const HEADER = ['time','bid_open','bid_high','bid_low','bid_close','ask_open','ask_high','ask_low','ask_close'];
  const MAX_BYTES = 75 * 1024 * 1024;
  const MAX_BARS = 300000;
  const DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:00:00Z$/;
  const HEX64 = /^[0-9a-f]{64}$/;
  const finite = n => Number.isFinite(n) && n > 0;

  function parseH1Csv(text) {
    if (typeof text !== 'string') throw Error('CSVが読み込めません');
    const lines = text.replace(/^\uFEFF/, '').trimEnd().split(/\r?\n/);
    if (lines.shift() !== HEADER.join(',')) throw Error('H1 CSVの列名が規定と一致しません');
    if (lines.length < 20 || lines.length > MAX_BARS) throw Error('H1データは20〜300,000本が必要です');
    const bars = [];
    let previous = -Infinity;
    let gaps = 0;
    for (const line of lines) {
      const values = line.split(',');
      if (values.length !== HEADER.length) throw Error('CSVに不正な列数があります');
      const t = values[0];
      if (!DATE_TIME.test(t)) throw Error('UTC時刻はYYYY-MM-DDTHH:00:00Zで指定してください');
      const time = Date.parse(t);
      if (!Number.isFinite(time) || new Date(time).toISOString().slice(0,19) !== t.slice(0,19)) throw Error('不正な日時です');
      if (time <= previous) throw Error('H1時刻が重複・逆行しています');
      if (time - previous > 3600000 && Number.isFinite(previous)) gaps++;
      previous = time;
      const numbers = values.slice(1).map(Number);
      if (numbers.some((n,i) => !values[i+1].trim() || !finite(n))) throw Error('価格が不正です');
      const [bo,bh,bl,bc,ao,ah,al,ac] = numbers;
      if (bh < Math.max(bo,bc) || bl > Math.min(bo,bc) || ah < Math.max(ao,ac) || al > Math.min(ao,ac)
          || bo < bl || bc > bh || ao < al || ac > ah || ao < bo || ac < bc) throw Error('Bid/AskのOHLCが整合しません');
      bars.push({time:t,ts:time,bid:{open:bo,high:bh,low:bl,close:bc},ask:{open:ao,high:ah,low:al,close:ac}});
    }
    return {bars,gaps};
  }

  function validateProvenance(meta, hash) {
    if (!meta || typeof meta !== 'object') throw Error('provenance.jsonが必要です');
    if (meta.schema !== 'fx-aegis-h1-bidask-v1' || meta.symbol !== 'USDJPY'
        || meta.quote_timezone !== 'UTC' || meta.timestamp_semantics !== 'BAR_OPEN'
        || !['BROKER_BID_ASK_H1','SYNTHETIC_TEST_FIXTURE'].includes(meta.source_kind)) throw Error('出典メタデータがH1規格と一致しません');
    if (typeof meta.sha256 !== 'string' || !HEX64.test(meta.sha256) || meta.sha256 !== hash) throw Error('CSVのSHA-256と出典メタデータが一致しません');
    return {source_kind:meta.source_kind, source_name:String(meta.source_name||'出典未確認').slice(0,120), synthetic:meta.source_kind==='SYNTHETIC_TEST_FIXTURE'};
  }

  async function loadLocalFiles(fileList, cryptoApi = typeof crypto === 'object' ? crypto : null) {
    const files = Array.from(fileList||[]);
    if (files.length !== 2) throw Error('bidask_h1.csvとprovenance.jsonの2ファイルを選択してください');
    const csv = files.find(f => /\.csv$/i.test(f.name));
    const provenance = files.find(f => /\.json$/i.test(f.name));
    if (!csv || !provenance || csv.size > MAX_BYTES || provenance.size > 100000) throw Error('入力形式またはファイルサイズに問題があります');
    if (!cryptoApi || !cryptoApi.subtle || !cryptoApi.subtle.digest) throw Error('安全なHTTPS環境でSHA-256を検査してください');
    const bytes = await csv.arrayBuffer();
    const hashBytes = new Uint8Array(await cryptoApi.subtle.digest('SHA-256',bytes));
    const sha256 = Array.from(hashBytes, b => b.toString(16).padStart(2,'0')).join('');
    const meta = validateProvenance(JSON.parse(await provenance.text()),sha256);
    const content = new TextDecoder('utf-8',{fatal:true}).decode(bytes);
    const {bars,gaps} = parseH1Csv(content);
    return {bars,gaps,meta,sha256};
  }

  function aggregates(bars, timeframe='H1', side='bid') {
    if (!['H1','D1'].includes(timeframe) || !['bid','ask'].includes(side)) throw Error('足種または価格種別が不正です');
    if (timeframe==='H1') return bars.map(b=>({date:b.time,ts:b.ts,...b[side],sourceCount:1,partial:false}));
    const days=[];
    let current=null;
    let last=NaN;
    for (const b of bars) {
      const date=b.time.slice(0,10);
      const v=b[side];
      if (!current || current.date !== date) {
        current={date,ts:b.ts,open:v.open,high:v.high,low:v.low,close:v.close,sourceCount:0,partial:false};
        days.push(current);
      }
      if (current.sourceCount && b.ts-last!==3600000) current.partial=true;
      current.high=Math.max(current.high,v.high);
      current.low=Math.min(current.low,v.low);
      current.close=v.close;
      current.sourceCount++;
      last=b.ts;
    }
    for (const d of days) if (d.sourceCount<24) d.partial=true;
    return days;
  }

  function ema(points, period) {
    const out=[];let prev=null;
    const a=2/(period+1);
    for (const p of points){
      const n=p.close;
      prev=prev===null?n:(n*a+prev*(1-a));
      out.push(prev);
    }
    return out;
  }

  function filterWindow(points, duration) {
    if (duration==='all' || !points.length) return points;
    const days=({'1d':1,'7d':7,'30d':30,'90d':90,'1':365,'5':365*5})[duration];
    if (!days) return points;
    const cutoff=points[points.length-1].ts - days*86400000;
    return points.filter(p=>p.ts>=cutoff);
  }

  function compressOHLC(points, maxCount) {
    if (points.length <= maxCount) return points;
    const result=[];
    for (let start=0;start<points.length;start+=Math.ceil(points.length/maxCount)) {
      const seg=points.slice(start,start+Math.ceil(points.length/maxCount));
      result.push({date:seg[0].date,ts:seg[0].ts,open:seg[0].open,high:Math.max(...seg.map(x=>x.high)),
        low:Math.min(...seg.map(x=>x.low)),close:seg[seg.length-1].close,
        sourceCount:seg.reduce((sum,x)=>sum+x.sourceCount,0),partial:seg.some(x=>x.partial),lastDate:seg[seg.length-1].date,ma20:seg[seg.length-1].ma20,ma50:seg[seg.length-1].ma50});
    }
    return result;
  }
  // Pure viewport math: keep the selected observation under the zoom anchor.
  // These ranges only select existing bars; no OHLC values are fabricated.
  function zoomRange(range,total,factor,anchorFraction=.5,minBars=8) {
    if (!Number.isSafeInteger(total)||total<0||!Number.isFinite(factor)||factor<=0)
      throw Error('Invalid chart zoom arguments');
    if(total===0)return {start:0,end:0};
    const span=Math.max(1,Math.min(total,(range?.end??total)-(range?.start??0)));
    const minimum=Math.min(total,Math.max(2,Math.floor(minBars)));
    const size=Math.max(minimum,Math.min(total,Math.round(span*factor)));
    const fraction=Number.isFinite(anchorFraction)?Math.max(0,Math.min(1,anchorFraction)):.5;
    const anchor=(range?.start??0)+fraction*span;
    const start=Math.max(0,Math.min(total-size,Math.round(anchor-fraction*size)));
    return {start,end:start+size};
  }

  function panRange(range,total,delta) {
    if(!Number.isSafeInteger(total)||total<0||!Number.isFinite(delta))
      throw Error('Invalid chart pan arguments');
    if(total===0)return {start:0,end:0};
    const span=Math.max(1,Math.min(total,(range?.end??total)-(range?.start??0)));
    const start=Math.max(0,Math.min(total-span,Math.round((range?.start??0)+delta)));
    return {start,end:start+span};
  }

  return {HEADER,parseH1Csv,validateProvenance,loadLocalFiles,aggregates,ema,filterWindow,compressOHLC,zoomRange,panRange};
});
