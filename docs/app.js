'use strict';
const $ = id => document.getElementById(id);
const state={data:null,points:[],scenario:null,plot:'price',window:'all',localBars:[],localMeta:null,localGaps:0};
const nf = (v,d=0) => v==null?'—':Number(v).toLocaleString('ja-JP',{minimumFractionDigits:d,maximumFractionDigits:d});
const isoDate=s=>String(s||'').slice(0,10);
const money=v=>v==null?'—':nf(v,0)+' 円';
const pct=v=>v==null?'—':`${v>=0?'+':''}${nf(v,2)}%`;
function ema(prices,n){if(!prices.length)return [];let value=prices[0];return prices.map((x,i)=>{if(i)value=(2/(n+1))*x+(1-2/(n+1))*value;return value});}
function simulate(points,opts){const {capital,fast,slow,allocation,spread,short}=opts;
 if(!Number.isFinite(capital)||capital<1000||capital>1e9||!(fast>=2&&fast<slow&&slow<=500)||!(allocation>0&&allocation<=1)||!(spread>=0&&spread<=50)||points.length<slow+2)throw Error('入力条件またはデータ件数が不足しています');
 const prices=points.map(x=>x.close),fastE=ema(prices,fast),slowE=ema(prices,slow);let equity=capital,peak=equity,dd=0,last=0,changes=0;const curve=[];
 for(let i=slow+1;i<points.length;i++){const j=i-1, side=fastE[j]>slowE[j]?1:(short&&fastE[j]<slowE[j]?-1:0);const shift=Math.abs(side-last),step=prices[i]/prices[j]-1,cost=shift*spread*.01/prices[j]*allocation;equity*=Math.max(.000001,1+allocation*side*step-cost);changes+=Number(shift>0);peak=Math.max(peak,equity);dd=Math.max(dd,100*(1-equity/peak));curve.push({date:points[i].date,close:equity});last=side;}
 return {start:curve[0]?.date,end:curve.at(-1)?.date,capital,final:equity,pnl:equity-capital,returnPct:(equity/capital-1)*100,maxDd:dd,changes,curve};}
function formatSync(s){if(!s)return '—';const d=new Date(s);return Number.isNaN(+d)?'—':d.toLocaleDateString('ja-JP',{timeZone:'Asia/Tokyo',month:'2-digit',day:'2-digit'})}
function setScenarioDisplay(s){$('simPnl').textContent=money(s.pnl);$('simPnl').className=s.pnl>=0?'positive':'negative';$('simReturn').textContent='仮定の期間収益率 '+pct(s.returnPct);$('maxDd').textContent=nf(s.maxDd,2)+'%';$('switches').textContent=nf(s.changes)+'回の方向変更';$('finalEquity').textContent=money(s.final);$('trades').textContent=nf(s.changes)+' 回';$('simDates').textContent=`${isoDate(s.start)}～${isoDate(s.end)}`;$('simMessage').textContent='計算済み：参考値に仮想コストを適用（約定再現ではありません）';}
function readOptions(){return {capital:Number($('capital').value),fast:Number($('fast').value),slow:Number($('slow').value),allocation:Number($('allocation').value)/100,spread:Number($('spread').value),short:$('direction').value==='both'};}
function recalc(){try{const o=readOptions();for(const v of [o.fast,o.slow]){if(!Number.isInteger(v))throw Error('EMA期間は整数で指定してください')}const s=simulate(state.points,o);state.scenario=s;setScenarioDisplay(s);draw();}catch(e){$('simMessage').textContent='計算できません：'+e.message;}}
function updateChartControls(){
 document.querySelectorAll('[data-plot]').forEach(b=>{b.disabled=b.dataset.plot==='candles'&&!state.localBars.length;b.classList.toggle('active',b.dataset.plot===state.plot)});
 document.querySelectorAll('[data-window]').forEach(b=>{
   const short=['7d','1d'].includes(b.dataset.window);
   b.hidden=short&&state.plot!=='candles';
   b.classList.toggle('active',b.dataset.window===state.window);
 });
 for(const id of ['candleTimeframe','candleSide'])$(id).disabled=state.plot!=='candles';
}
function draw(){
 updateChartControls();
 const local=state.plot==='candles',result=AegisChartView.draw($('chart'),{
   plot:state.plot,window:state.window,reference:state.points,
   equity:state.scenario?.curve,localBars:state.localBars,
   timeframe:$('candleTimeframe').value,side:$('candleSide').value,
   showEma:$('showEma').checked
 });
 $('emptyChart').hidden=result.available;
 $('emptyChart').textContent=local?'H1 Bid/AskのCSVと出典JSONを2ファイル選択してください':'表示可能な公表値・計算結果がありません';
 const side=$('candleSide').value.toUpperCase();
 $('chartLegend').textContent=local?
   `端末内の${state.localMeta?.synthetic?'人工生成データ':'出典未検証データ'} · ${side}`:
   state.plot==='equity'?'参考値から計算した仮想資産曲線':'FRED DEXJPUS / 日次参考レート';
 $('chartTitle').textContent=local?`USD/JPY ${$('candleTimeframe').value} ${side} ローソク足`:
   state.plot==='equity'?'仮想シナリオの資産曲線':'USD/JPY / 日次参考レート';
 $('chartExtra').textContent=local?`ローカル照合済み · 時間欠損${state.localGaps}箇所`:
   state.plot==='price'?'OHLCではありません':'';
 $('chartCaption').textContent=result.available?`${result.first} ～ ${result.last} / ${result.count.toLocaleString('ja-JP')}本${result.compressed?'（表示密度に合わせ区間集約）':''} · ${local?'約定価格・出典真正性は未確認':'実約定ではない'}`:'読み込み待ち';
}
async function importLocalH1(event){
 try{
   $('localStatus').textContent='端末内でファイルのSHA-256とOHLCを検査中…';
   const checked=await AegisChartModel.loadLocalFiles(event.target.files);
   state.localBars=checked.bars;state.localMeta=checked.meta;state.localGaps=checked.gaps;
   $('localStatus').textContent=`${checked.meta.synthetic?'人工生成':'ユーザー申告・出典未検証'} / ${nf(checked.bars.length)}本 / SHA-256一致 / 時間欠損${checked.gaps}箇所。ブラウザ内のみ・アップロードなし。`;
   $('localClear').hidden=false;state.plot='candles';state.window='7d';
   AegisChartView.reset();draw();
 }catch(e){$('localStatus').textContent='取込不可：'+e.message;}
 finally{event.target.value='';}
}
function clearLocalH1(){
 state.localBars=[];state.localMeta=null;state.localGaps=0;state.plot='price';state.window='all';
 $('localFiles').value='';$('localClear').hidden=true;
 $('localStatus').textContent='端末内のH1データをクリアしました。FREDはローソク足では表示できません。';
 AegisChartView.reset();draw();
}

function updateUI(data){const pts=Array.isArray(data.reference_daily)?data.reference_daily:[];state.points=pts.filter(p=>/^\d{4}-\d{2}-\d{2}$/.test(p.date)&&Number.isFinite(p.close)&&p.close>0);const present=state.points.length>0;const meta=data.provenance||{};
 $('datasource').textContent=present?'公式の参考データ保存済み':'実データ未取得';$('lastRate').textContent=present?nf(state.points.at(-1).close,3):'—';$('lastDate').textContent=present?`公表観測日 ${state.points.at(-1).date}`:'最新値なし';$('observations').textContent=present?nf(state.points.length):'0';$('range').textContent=present?`${state.points[0].date} ～ ${state.points.at(-1).date}`:'未取得';$('coverage').textContent=present?'観測日のみ（休場日を除く）':'GitHub Actions初回実行待ち';$('lastSync').textContent=formatSync(meta.retrieved_at_utc);$('hash').textContent=meta.csv_sha256?`SHA256: ${meta.csv_sha256.slice(0,12)}…`:'検証情報なし';$('audit').textContent=meta.csv_sha256?'データSHA記録':'未取得';$('sourceDetail').textContent=meta.source||'日次参考レート・約定価格ではありません';$('footerState').textContent=present?`公開済み観測値 ${state.points.at(-1).date}`:'データ待ち / 未検証';$('scenarioForm').querySelector('button').disabled=!present;
 if(present){recalc()}else{$('simMessage').textContent='公式データ取得完了後にシミュレーションできます';draw()}
}
async function load(){try{const res=await fetch('./data/research.json',{cache:'no-store'});if(!res.ok)throw Error(`HTTP ${res.status}`);const data=await res.json();if(data.no_live_trading!==true||data.simulation_is_hypothetical!==true)throw Error('データ定義の検証に失敗');state.data=data;updateUI(data);}catch(e){$('datasource').textContent='データ公開未完了';$('simMessage').textContent='データの読み込みに失敗：'+e.message;draw()}}
async function loadRepro(){
 try{
  const res=await fetch('./data/reproducibility.json',{cache:'no-store'});
  if(!res.ok)throw Error('再実行証跡が未公開');
  const p=await res.json();
  const expected=state.data?.provenance?.csv_sha256;
  if(p.status!=='REPLAY_VERIFIED'||!expected||p.data_sha256!==expected||!/^([0-9a-f]{64})$/.test(p.record_sha256))throw Error('照合不一致');
  $('audit').textContent='再実行一致（CI）';
  $('reproDetail').textContent=`${p.candidate_count}条件 / 証跡 ${p.record_sha256.slice(0,10)}… / 実売買不可`;
 }catch(e){$('audit').textContent='再実行未確認';$('reproDetail').textContent=e.message;}
}
async function loadEvolution(){
 try{
  const res=await fetch('./data/evolution.json',{cache:'no-store'});
  if(!res.ok)throw Error('進化データ未公開');
  const e=await res.json();
  const expected=state.data?.provenance?.csv_sha256;
  if(!expected||e.data_sha256!==expected||e.approved_for_live_trading!==false||e.source_kind!=='FRED_DAILY_REFERENCE_NON_EXECUTABLE')throw Error('進化証跡不一致');
  const screened=Number.isInteger(e.statistically_screened_count)?e.statistically_screened_count:null;
  $('evolutionState').textContent=screened===null?`${e.candidate_count}案 / 旧版審査未対応`:`${e.candidate_count}案 / 統計適格${screened}件`;
  const rank=Number.isFinite(e.rank_instability_fraction)?` / 順位不安定 ${nf(e.rank_instability_fraction*100,1)}%（参考値）`:'';
  $('evolutionState').title=`統計審査: ${e.overfit_audit_status||'未対応'}${rank} / 探索履歴${e.exploration_history_runs||0}週分 / 新データ126件以上の紙上検証が必要。日次参考値は実際の取引価格ではなく、将来利益は保証しません。`;
 }catch(err){$('evolutionState').textContent='未確認（売買禁止）';}
}
function boot(){
 setInterval(()=>{$('clockJst').textContent=new Date().toLocaleTimeString('ja-JP',{timeZone:'Asia/Tokyo',hour12:false})+' JST'},1000);
 AegisChartView.install($('chart'),draw);
 $('scenarioForm').addEventListener('submit',e=>{e.preventDefault();recalc()});
 document.querySelectorAll('[data-plot]').forEach(b=>b.addEventListener('click',()=>{
   if(b.disabled)return;state.plot=b.dataset.plot;
   if(state.plot!=='candles'&&['7d','1d'].includes(state.window))state.window='all';
   if(state.plot==='candles'&&state.window==='5')state.window='7d';
   AegisChartView.reset();draw();
 }));
 document.querySelectorAll('[data-window]').forEach(b=>b.addEventListener('click',()=>{
   if(b.hidden)return;state.window=b.dataset.window;AegisChartView.reset();draw();
 }));
 for(const id of ['candleTimeframe','candleSide','showEma'])$(id).addEventListener('change',()=>{AegisChartView.reset();draw()});
 $('localFiles').addEventListener('change',importLocalH1);
 $('localClear').addEventListener('click',clearLocalH1);
 $('chartReset').addEventListener('click',()=>{AegisChartView.reset();draw()});
 window.addEventListener('resize',draw);
 updateChartControls();
 load().then(()=>Promise.all([loadRepro(),loadEvolution()]));
}
document.addEventListener('DOMContentLoaded',boot);
