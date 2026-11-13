'use strict';
const $ = (id) => document.getElementById(id);
const state = {payload:null, mode:'price',window:'all',segment:'test',chartPoints:[],chartArea:null, mouse:null, chartSampled:false};
const money = n => n==null?'--':'¥'+Math.abs(n).toLocaleString('ja-JP',{maximumFractionDigits:2,minimumFractionDigits:2});
const signedMoney = n => n==null?'--':(n<0?'−':n>0?'+':'')+money(n);
const pct = (n,d=2) => n==null?'--':Number(n).toFixed(d)+'%';
const f2 = n => n==null?'--':Number(n).toFixed(2);
function timeJP(t,short=false){if(!t)return '--';const d=new Date(t);if(Number.isNaN(d.getTime()))return '--';return new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',year:short?undefined:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d)}
function dateShort(t){if(!t)return '--';const d=new Date(t);return new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d)}
function classValue(el,v){el.classList.remove('positive','negative','neutral');el.classList.add(v>0?'positive':v<0?'negative':'neutral')}
function clock(){const now=new Date();$('localClock').innerHTML=now.toLocaleTimeString('ja-JP',{timeZone:'Asia/Tokyo',hour12:false})+' <small>JST</small>'}
function explainReason(s){const names={MODEL_WAIT_OR_VOLATILITY:'AI意見不一致・閾値未達',CUSTOM_AGENT_VETO:'独自AIが売買を拒否',MOVE_TOO_SMALL_FOR_COST:'予測値幅がコストに不足',PASS:'条件通過',SPREAD_TOO_HIGH:'スプレッド過大',SPREAD_TO_ATR_TOO_HIGH:'ATR比のコスト超過',ADX_TOO_LOW:'トレンド強度が不足',TREND_MISMATCH:'EMAトレンドと不一致',NEWS_BLACKOUT:'重要指標時間帯',OK:'条件通過'};return names[s]||s||'理由情報なし'}
function render(){const p=state.payload;if(!p)return;const s=p.stats, a=p.last_signal, sum=p.summary;
 $('dataLabel').textContent=p.data_label||'データ種別未指定';$('segmentDates').textContent=`${timeJP(p.start)} — ${timeJP(p.end)} (JST)`;
 $('equity').textContent=money(s.final_yen);$('initialBalance').textContent=money(s.initial_yen);
 $('netPnl').textContent=signedMoney(s.net_profit_yen);classValue($('netPnl'),s.net_profit_yen);$('returnPct').textContent=`収益率 ${s.return_pct>=0?'+':''}${pct(s.return_pct,4)}`;classValue($('returnPct'),s.return_pct);
 $('maxDd').textContent=pct(s.max_closed_trade_drawdown_pct,3);$('winRate').textContent=pct(s.win_rate_pct,1);$('tradeCount').textContent=`${s.trade_count??'--'} 取引（決済済み）`;
 $('profitFactor').textContent=s.profit_factor==null?'--':f2(s.profit_factor);classValue($('profitFactor'),s.profit_factor==null?0:s.profit_factor-1);
 $('modelTime').textContent='検証シグナル: '+timeJP(a.time)+' JST';
 $('signalSide').textContent=a.side===1?'BUY':a.side===-1?'SELL':'WAIT';
 const sw=$('signalSide').parentElement.parentElement;sw.classList.remove('signal-wait','signal-buy','signal-sell');sw.classList.add(a.side===1?'signal-buy':a.side===-1?'signal-sell':'signal-wait');
 $('signalReason').textContent=explainReason(a.execution||a.filter);$('trendProb').textContent=pct((a.trend??0)*100,1);$('revertProb').textContent=pct((a.reversion??0)*100,1);
 $('trendBar').style.width=`${Math.min(100,Math.max(0,(a.trend||0)*100))}%`;$('revertBar').style.width=`${Math.min(100,Math.max(0,(a.reversion||0)*100))}%`;
 const riskRatio=a.vol_cap?Math.min(1.5,(a.volatility??0)/a.vol_cap):0;
 $('volStatus').textContent=a.vol_veto?'高変動・拒否':'許容範囲';$('volBar').style.width=`${Math.min(100,riskRatio*100)}%`;
 const agents=sum.custom_agents||[],evolved=sum.evolved_agents||[];$('agentCount').textContent=`3 MODEL + ${agents.length} CUSTOM + ${evolved.length} EVOLVED`;$('agentCount').title=[...agents,...evolved].map(x=>x.name).join(', ')||'独自AIなし';
 const evo=p.evolution||{};
 $('evoStatus').textContent=!evo.available?'未評価':evo.paper_promoted?'検証通過・PAPER採用':evo.promotion_eligible?'検証通過・未採用':'候補拒否 / SHADOW';
 $('evoStatus').title=evo.available?`${evo.candidate_name||'-'} / ${evo.candidate_count||0}候補 / 検証損益 ${evo.validation_net_yen}円 / 2倍コスト ${evo.double_cost_net_yen}円`:'進化エンジン未実行';
 $('aiGateStatus').textContent=a.custom_veto?'独自AIが拒否':a.event_veto?'指標イベント拒否':a.side===0?'見送り':'シグナル通過';$('aiGateStatus').title=Object.entries(a.custom_votes||{}).map(([name,v])=>`${name}: ${(v*100).toFixed(1)}%`).join(' / ');
 $('haltStatus').textContent=s.permanent_drawdown_halt?'作動済み':'DD停止なし';$('haltStatus').className=s.permanent_drawdown_halt?'negative':'neutral';
 $('ddLimit').textContent=pct((sum.config.max_drawdown_stop??0)*100,0);$('eventCount').textContent=`${sum.events_loaded??0} 件（読み込み済）`;const r=p.online_research||{};$('researchStatus').textContent=r.available?`${r.official_hit_count} 件（保存済）`:'未取得';$('researchStatus').title=r.last_headline||'検索結果スナップショットなし';
 const hist=p.history20||{};
 $('gateLabel').textContent=hist.available?'長期履歴':'戦略フィルター';
 $('gateSummary').textContent=hist.hourly_coverage_pct!=null?`H1月次 ${hist.hourly_coverage_pct}%`:
   hist.daily_observations!=null?`日次 ${hist.daily_observations.toLocaleString('ja-JP')}件`:
   `ADX ≥${sum.gates.min_adx??'--'} / spread ≤${sum.gates.max_spread_pips??'--'}p`;
 $('gateSummary').title=hist.available?
  `日次 ${hist.daily_start||'--'}〜${hist.daily_end||'--'} / H1 ${hist.hourly_bars??'未登録'}本 / 20年H1検証 ${hist.hourly_20y_ready?'可能（出典認証なし）':'不足'}`:
  '長期データ未取得';
 $('fileStamp').textContent='検証結果のファイル更新: '+timeJP(p.file_updated_utc)+' JST';
 $('tradeLabel').textContent=`直近4 / ${s.trade_count??0} TRADES`;renderTrades(p.trades);renderChart();}
function renderTrades(rows){const tb=$('tradeRows');tb.replaceChildren();if(!rows.length){let tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=4;td.className='no-data';td.textContent='この検証区間には取引がありません';tr.append(td);tb.append(tr);return}
 for(const r of rows.slice(0,4)){const tr=document.createElement('tr');const d=document.createElement('td');d.textContent=timeJP(r.t);const sd=document.createElement('td');const span=document.createElement('span');span.className='side-pill '+(r.side==='BUY'?'side-buy':'side-sell');span.textContent=r.side;sd.append(span);const pnl=document.createElement('td');pnl.textContent=signedMoney(r.pnl);pnl.className=r.pnl>0?'positive':r.pnl<0?'negative':'';const eq=document.createElement('td');eq.textContent=money(r.equity);tr.append(d,sd,pnl,eq);tb.append(tr)}}
function setActive(group,key,value){document.querySelectorAll(`#${group} button`).forEach(b=>b.classList.toggle('selected',b.dataset[key]===value))}
function series(){const p=state.payload;if(!p)return [];
 let data=state.mode==='long'?(p.reference_daily||[]).map(x=>({t:new Date(x.date+'T00:00:00Z').getTime(),v:x.close})):
  state.mode==='price'?p.market.map(x=>({t:new Date(x.t).getTime(),v:x.c})):
  p.equity.map(x=>({t:new Date(x.t).getTime(),v:x.v}));
 data=data.filter(x=>Number.isFinite(x.t)&&x.v!==null&&Number.isFinite(x.v));if(!data.length)return [];
 if(state.mode==='price'){const a=new Date(p.start).getTime();const b=new Date(p.end).getTime()+2*3600000;data=data.filter(x=>x.t>=a&&x.t<=b)}
 if(state.mode!=='long'&&state.window!=='all'){const cutoff=data[data.length-1].t-Number(state.window)*3600000;data=data.filter(x=>x.t>=cutoff)}
 state.chartSampled=data.length>2400;if(data.length>2400){const k=Math.ceil(data.length/2400);data=data.filter((_,i)=>i%k===0||i===data.length-1)}return data}
function renderChart(){$('timeframeLabel').textContent=state.mode==='long'?'日次参照 · 20Y（約定不可）':'H1 · Trend gated';const canvas=$('priceChart'),box=canvas.getBoundingClientRect();if(!box.width||!box.height)return;const dpr=window.devicePixelRatio||1;canvas.width=Math.floor(box.width*dpr);canvas.height=Math.floor(box.height*dpr);const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);const W=box.width,H=box.height;ctx.clearRect(0,0,W,H);const P={l:9,r:70,t:12,b:29};const pw=W-P.l-P.r,ph=H-P.t-P.b;
 const pts=series();if(state.mode==='price'&&state.chartSampled)$('chartNote').textContent='価格を間引き表示・エントリー位置は近似';state.chartPoints=[];state.chartArea=null;$('chartTitle').textContent=state.mode==='long'?'ドル円 20年間・日次参照レート':state.mode==='price'?'ドル円 価格推移':'仮想口座 資産曲線';$('chartLegend').textContent=state.mode==='long'?'FRED NY正午参照':state.mode==='price'?'USD/JPY · 終値':'決済後残高';$('chartExtraLegend').textContent=state.mode!=='equity'?'EMA50':'初期資金';$('chartNote').textContent=state.mode==='long'?'日次参照レート・約定価格ではありません':state.mode==='price'?(state.chartSampled?'価格を間引き表示・エントリー位置は近似':'検証データ・エントリー位置を表示'):'決済済み損益のみ・含み損益を含まない';$('chartRange').textContent=pts.length?(state.mode==='long'?`${new Date(pts[0].t).toISOString().slice(0,10)} — ${new Date(pts[pts.length-1].t).toISOString().slice(0,10)} (日次)`:`${dateShort(pts[0].t)} — ${dateShort(pts[pts.length-1].t)} JST`):'該当データなし';
 if(pts.length<2||pw<60||ph<40){ctx.fillStyle='#8397a3';ctx.font='12px sans-serif';ctx.fillText('表示できる検証データがありません',30,60);return}
 const times=pts.map(x=>x.t),values=pts.map(x=>x.v),minX=Math.min(...times),maxX=Math.max(...times);let minY=Math.min(...values),maxY=Math.max(...values);if(minY===maxY){minY-=1;maxY+=1}else{const pad=(maxY-minY)*.11;minY-=pad;maxY+=pad}
 const X=t=>P.l+(t-minX)/(maxX-minX||1)*pw,Y=v=>P.t+(maxY-v)/(maxY-minY)*ph;
 ctx.strokeStyle='#263a47';ctx.lineWidth=1;ctx.font='10px sans-serif';ctx.textAlign='left';ctx.fillStyle='#718a97';for(let i=0;i<5;i++){let y=P.t+(ph/4)*i;ctx.beginPath();ctx.moveTo(P.l,y);ctx.lineTo(P.l+pw,y);ctx.stroke();let v=maxY-(maxY-minY)*i/4;ctx.fillText(state.mode!=='equity'?v.toFixed(3):'¥'+Math.round(v).toLocaleString('ja-JP'),P.l+pw+7,y+3)}
 for(let i=0;i<5;i++){const t=minX+(maxX-minX)*i/4;const x=X(t);ctx.strokeStyle='#1c3442';ctx.beginPath();ctx.moveTo(x,P.t);ctx.lineTo(x,P.t+ph);ctx.stroke();ctx.fillStyle='#7a909a';ctx.textAlign=i===4?'right':i===0?'left':'center';ctx.fillText(state.mode==='long'?new Date(t).getUTCFullYear().toString():dateShort(t),x,P.t+ph+18)}
 function line(points,color,width=2,dash=[]){if(points.length<2)return;ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dash);ctx.beginPath();for(let i=0;i<points.length;i++){const x=X(points[i].t),y=Y(points[i].v);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)}ctx.stroke();ctx.setLineDash([])}
 if(state.mode!=='equity'){// EMA50 is calculated on earlier input bars; never on future prices.
  const raw=state.mode==='long'?(state.payload.reference_daily||[]).map(x=>({t:x.date+'T00:00:00Z',c:x.close})):state.payload.market;let ema=null,arr=[];const alpha=2/51;const stride=Math.max(1,Math.ceil(raw.length/2400));for(let ri=0;ri<raw.length;ri++){const x=raw[ri];if(x.c==null)continue;ema=ema===null?x.c:alpha*x.c+(1-alpha)*ema;const t=new Date(x.t).getTime();if(t>=minX&&t<=maxX&&(ri%stride===0||ri===raw.length-1))arr.push({t,v:ema})}line(arr,'#629ac1',1.2,[4,4]);
  const markers=state.mode==='price'?(state.payload.trade_markers||[]):[];for(const m of markers){const t=new Date(m.t).getTime();if(t<minX||t>maxX)continue;const j=closestIndex(times,t);const yy=Y(values[j]);const xx=X(t);ctx.beginPath();const up=m.side==='BUY';ctx.moveTo(xx,yy+(up?8:-8));ctx.lineTo(xx-4,yy+(up?14:-14));ctx.lineTo(xx+4,yy+(up?14:-14));ctx.closePath();ctx.fillStyle=up?'#48dfb4':'#ff818b';ctx.fill()}
 }else{const init=state.payload.stats.initial_yen;ctx.strokeStyle='#77a7c4';ctx.lineWidth=1;ctx.setLineDash([6,4]);ctx.beginPath();ctx.moveTo(P.l,Y(init));ctx.lineTo(P.l+pw,Y(init));ctx.stroke();ctx.setLineDash([])}
 const first=pts[0],last=pts[pts.length-1];const grad=ctx.createLinearGradient(0,P.t,0,P.t+ph);grad.addColorStop(0,state.mode!=='equity'?'#40d6aa26':'#7da8da29');grad.addColorStop(1,'#10243100');ctx.beginPath();ctx.moveTo(X(first.t),P.t+ph);for(let i=0;i<pts.length;i++)ctx.lineTo(X(pts[i].t),Y(pts[i].v));ctx.lineTo(X(last.t),P.t+ph);ctx.closePath();ctx.fillStyle=grad;ctx.fill();line(pts,state.mode!=='equity'?'#4ce2bc':'#82b4e6',2);
 state.chartPoints=pts;state.chartArea={P,pw,ph,minX,maxX,minY,maxY,X,Y,W,H};if(state.mouse!=null)drawCrosshair(ctx,state.mouse)}
function closestIndex(xs,t){let lo=0,hi=xs.length-1;while(lo<hi){const m=(lo+hi)>>1;if(xs[m]<t)lo=m+1;else hi=m}return lo===0?0:Math.abs(xs[lo]-t)<Math.abs(xs[lo-1]-t)?lo:lo-1}
function drawCrosshair(ctx,x){const a=state.chartArea;if(!a||x<a.P.l||x>a.P.l+a.pw)return;const pts=state.chartPoints;const i=closestIndex(pts.map(p=>p.t),a.minX+(x-a.P.l)/a.pw*(a.maxX-a.minX));const v=pts[i];const px=a.X(v.t),py=a.Y(v.v);ctx.strokeStyle='#8da9b4aa';ctx.setLineDash([3,5]);ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(px,a.P.t);ctx.lineTo(px,a.P.t+a.ph);ctx.moveTo(a.P.l,py);ctx.lineTo(a.P.l+a.pw,py);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='#ddfff4';ctx.beginPath();ctx.arc(px,py,4,0,Math.PI*2);ctx.fill();const tip=$('chartTooltip');tip.hidden=false;tip.textContent=`${state.mode==='long'?new Date(v.t).toISOString().slice(0,10):timeJP(v.t)+' JST'} / ${state.mode!=='equity'?v.v.toFixed(3):money(v.v)}`;tip.style.left=Math.max(5,Math.min(a.W-195,px+12))+'px';tip.style.top=Math.max(2,py-38)+'px'}
async function load(){try{const res=await fetch('/api/dashboard?segment='+encodeURIComponent(state.segment),{cache:'no-store'});if(!res.ok)throw new Error('HTTP '+res.status);const data=await res.json();if(data.error)throw new Error(data.error);state.payload=data;state.mouse=null;$('chartTooltip').hidden=true;render();$('freshness').textContent='ファイル監視: 15秒';}catch(e){$('freshness').textContent='読込エラー';$('signalReason').textContent='データ読み込み失敗: '+e.message;console.error(e)}}
window.addEventListener('DOMContentLoaded',()=>{clock();setInterval(clock,1000);$('segment').addEventListener('change',e=>{state.segment=e.target.value;load()});document.querySelectorAll('#modeGroup button').forEach(b=>b.addEventListener('click',()=>{state.mode=b.dataset.mode;setActive('modeGroup','mode',state.mode);renderChart()}));document.querySelectorAll('#windowGroup button').forEach(b=>b.addEventListener('click',()=>{state.window=b.dataset.window;setActive('windowGroup','window',state.window);renderChart()}));$('priceChart').addEventListener('mousemove',e=>{state.mouse=e.clientX-e.target.getBoundingClientRect().left;renderChart()});$('priceChart').addEventListener('mouseleave',()=>{state.mouse=null;$('chartTooltip').hidden=true;renderChart()});new ResizeObserver(()=>renderChart()).observe($('priceChart'));load();setInterval(load,15000)});
