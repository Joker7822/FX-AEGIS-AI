/* Local-only research annotations: no network, no order execution. */
'use strict';
(function(root,factory){
  const api=factory();
  if(typeof module==='object' && module.exports)module.exports=api;
  root.AegisAnnotations=api;
})(typeof globalThis==='object'?globalThis:this,function(){
  const HOUR=3600000, MAX_TRADES=3000,MAX_PREDICTIONS=20000,MAX_BYTES=4*1024*1024;
  const CLOCK=/^\d{4}-\d{2}-\d{2}T\d{2}:00:00Z$/;
  const HEX=/^[0-9a-f]{64}$/;
  const ID=/^[A-Za-z0-9_-]{1,64}$/;
  function requireTime(value,label){
    if(typeof value!=='string'||!CLOCK.test(value))throw Error(label+'はUTC H1時刻が必要です');
    const t=Date.parse(value);
    if(!Number.isFinite(t)||new Date(t).toISOString().slice(0,19)!==value.slice(0,19))throw Error(label+'の日時が不正です');
    return t;
  }
  function positive(value,label){
    if(typeof value!=='number'||!Number.isFinite(value)||value<=0)throw Error(label+'は正の有限数が必要です');
    return value;
  }
  function parse(text,quotesSha,bars){
    if(typeof text!=='string'||new TextEncoder().encode(text).length>MAX_BYTES)throw Error('注釈JSONのサイズが大きすぎます');
    let m;
    try{m=JSON.parse(text)}catch(_){throw Error('注釈JSONを解析できません')}
    if(!m||Array.isArray(m)||m.schema!=='fx-aegis-h1-research-overlay-v1'
       ||m.approved_for_live_trading!==false ||m.execution_assumption!=='H1_OPEN_HYPOTHETICAL'
       ||!HEX.test(m.quotes_sha256||'')||m.quotes_sha256!==quotesSha)
       throw Error('注釈の研究専用宣言または価格SHA-256が一致しません');
    if(!Array.isArray(bars)||!bars.length)throw Error('先にH1価格ファイルを読み込んでください');
    const trades=m.trades??[],preds=m.predictions??[];
    if(!Array.isArray(trades)||trades.length>MAX_TRADES||!Array.isArray(preds)||preds.length>MAX_PREDICTIONS)
      throw Error('注釈の件数が上限を超えています');
    const at=new Map(bars.map((b,i)=>[b.time,i]));
    const requireIndex=(stamp,label)=>{
      requireTime(stamp,label);
      const i=at.get(stamp);
      if(i===undefined)throw Error(label+'はH1価格データの範囲外です');
      return i;
    };
    const used=new Set(),tradesOut=[],predictionsOut=[];
    for(const row of trades){
      if(!row||typeof row!=='object'||!ID.test(row.id||'')||used.has(row.id))throw Error('取引IDが不正または重複しています');
      used.add(row.id);
      if(!['long','short'].includes(row.side))throw Error('売買方向はlong/shortのみです');
      const si=requireIndex(row.signal_time,'シグナル時刻');
      const ei=requireIndex(row.entry_time,'エントリー時刻');
      const xi=requireIndex(row.exit_time,'決済時刻');
      if(ei!==si+1||bars[ei].ts-bars[si].ts!==HOUR||xi<=ei)
        throw Error('シグナル後の連続した次足でのみエントリーできます');
      const entry=positive(row.entry_price,'エントリー価格'),exit=positive(row.exit_price,'決済価格');
      const side=row.side==='long'?1:-1;
      // Inputs are unverified hypothetical prices, never proof of broker fills.
      const refOpen=side===1?bars[ei].ask.open:bars[ei].bid.open;
      const refExit=side===1?bars[xi].bid.open:bars[xi].ask.open;
      if(Math.abs(entry-refOpen)>refOpen*.05||Math.abs(exit-refExit)>refExit*.05)
        throw Error('仮想約定価格がH1参照価格から大きく乖離しています');
      const sl=row.stop_loss==null?null:positive(row.stop_loss,'損切り');
      const tp=row.take_profit==null?null:positive(row.take_profit,'利確');
      if((sl!==null&&side*(entry-sl)<=0)||(tp!==null&&side*(tp-entry)<=0))
        throw Error('損切り/利確の位置が売買方向と矛盾します');
      tradesOut.push({id:row.id,side:row.side,signalTs:bars[si].ts,entryTs:bars[ei].ts,
        exitTs:bars[xi].ts,entry,exit,stopLoss:sl,takeProfit:tp,
        hypotheticalPips:side*(exit-entry)/.01});
    }
    const seenPreds=new Set();
    for(const row of preds){
      if(!row||typeof row!=='object')throw Error('予測レコードが不正です');
      const si=requireIndex(row.signal_time,'予測時刻');
      if(seenPreds.has(si))throw Error('予測時刻が重複しています');
      seenPreds.add(si);
      const p=row.probability_up;
      if(typeof p!=='number'||!Number.isFinite(p)||p<0||p>1)
        throw Error('予測確率は0から1までの数値です');
      if(si+2>=bars.length||bars[si+1].ts-bars[si].ts!==HOUR
        ||bars[si+2].ts-bars[si+1].ts!==HOUR)
        throw Error('予測判定に必要な後続2本の連続H1足がありません');
      const mid=b=>(b.bid.open+b.ask.open)/2;
      const actualUp=mid(bars[si+2])>mid(bars[si+1]);
      predictionsOut.push({signalTs:bars[si].ts,probabilityUp:p,actualUp,
        correct:(p>.5)===actualUp,entryTs:bars[si+1].ts,exitTs:bars[si+2].ts});
    }
    const n=predictionsOut.length;
    const brier=n?predictionsOut.reduce((s,p)=>s+(p.probabilityUp-Number(p.actualUp))**2,0)/n:null;
    const accuracy=n?predictionsOut.filter(p=>p.correct).length/n:null;
    return {trades:tradesOut,predictions:predictionsOut,summary:{trades:tradesOut.length,
      predictions:n,accuracy,brier,hypotheticalPips:tradesOut.reduce((s,t)=>s+t.hypotheticalPips,0)},
      quotesSha256:quotesSha,synthetic:!!m.synthetic,
      note:'売買は仮想記録・予測は申告値。実際のH1値動きから正誤を再計算。約定実績ではありません。'};
  }
  async function load(file,sha,bars){
    if(!file||!/\.json$/i.test(file.name)||file.size>MAX_BYTES)throw Error('注釈は4MB以内のJSONが必要です');
    return parse(await file.text(),sha,bars);
  }
  return {parse,load};
});
