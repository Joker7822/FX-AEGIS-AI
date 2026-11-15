/* File-picker-only UI: no fetch, POST, WebSocket, storage or broker access. */
'use strict';
(function () {
  const $ = id => document.getElementById(id);
  const number = n => n == null ? '—' : Number(n).toLocaleString('ja-JP');
  const percent = n => n == null ? '—' : (100 * n).toFixed(1) + '%';
  let report = null;
  function clear() {
    report = null;
    for (const id of ['monitorStatus','operationalStatus','scored','pending','accuracy','brier','observed','late',
                       'events','alerts','recentAccuracy','recentBrier','lastObserved','chartDesc']) $(id).textContent = '—';
    $('sourceKind').textContent = '市場データ未確認';
    $('fileStatus').textContent = 'レポート未選択。AI予測の正答率は確認できません。';
    $('alertMsg').textContent = '';
    const tbody = $('predictionRows'); tbody.replaceChildren();
    const row = tbody.insertRow(); row.insertCell().textContent = 'まだ予測記録がありません';
    const canvas=$('probChart'); const context=canvas.getContext('2d');
    if (context) context.clearRect(0,0,canvas.width,canvas.height);
  }
  function drawProbability() {
    if (!report) return;
    const el = $('probChart'), ctx = el.getContext('2d'); if (!ctx) return;
    const width = Math.max(250, Math.floor(el.clientWidth));
    const height = 280, dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    el.width = Math.floor(width * dpr); el.height = Math.floor(height * dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.clearRect(0,0,width,height);
    const inset={left:46,right:15,top:21,bottom:26}, w=width-inset.left-inset.right,h=height-inset.top-inset.bottom;
    ctx.font='12px system-ui'; ctx.fillStyle='#9eb5c4'; ctx.strokeStyle='#233e4e'; ctx.lineWidth=1;
    for(let value=0;value<=1.00001;value+=.25){let y=inset.top+h*(1-value);ctx.beginPath();ctx.moveTo(inset.left,y);ctx.lineTo(width-inset.right,y);ctx.stroke();ctx.fillText((value*100).toFixed(0)+'%',5,y+4);}
    const rows=report.predictions;
    if(!rows.length){ctx.fillText('記録された予測はまだありません',inset.left+10,50);return;}
    const x=i=>inset.left+(rows.length===1?w/2:i*w/(rows.length-1));
    const y=p=>inset.top+h*(1-p);
    ctx.strokeStyle='#70d9c9';ctx.lineWidth=1.6;ctx.beginPath();
    rows.forEach((r,i)=>{if(i===0)ctx.moveTo(x(i),y(r.probability_up));else ctx.lineTo(x(i),y(r.probability_up));}); ctx.stroke();
    rows.forEach((r,i)=>{const cx=x(i),cy=y(r.probability_up);
      ctx.fillStyle='#6fdec9';ctx.beginPath();ctx.arc(cx,cy,rows.length>200?1.2:2.6,0,Math.PI*2);ctx.fill();
      if(r.actual_up!==null){ctx.fillStyle='#e8c48a';ctx.beginPath();ctx.arc(cx,y(r.actual_up?1:0),rows.length>200?1.4:3,0,Math.PI*2);ctx.fill();}
    });
    ctx.fillStyle='#94adbb';ctx.fillText(rows[0].signal_time.slice(0,16),inset.left,height-8);
    const last=rows.at(-1).signal_time.slice(0,16);ctx.fillText(last,width-inset.right-ctx.measureText(last).width,height-8);
  }
  function render(r) {
    report = r;
    $('fileStatus').textContent = '端末内で読み込み完了。生価格やAPIトークンは含まれません。';
    const blocked = r.supervisor_status.startsWith('BLOCKED_') || r.monitor_status !== 'ACTIVE';
    $('monitorStatus').textContent = blocked?'停止・要確認':'研究監視中';
    $('monitorStatus').className = blocked?'status-bad':'status-good';
    $('operationalStatus').textContent = r.supervisor_status;
    $('alertMsg').textContent = blocked?'予測停止を検出しました。自動再開はせず、監査記録を保全してください。':'';
    $('scored').textContent = number(r.metrics.overall.scored);
    $('pending').textContent = '未判定 ' + number(r.metrics.overall.pending);
    $('accuracy').textContent = percent(r.metrics.overall.accuracy);
    $('brier').textContent = r.metrics.overall.brier==null?'—':r.metrics.overall.brier.toFixed(4);
    $('sourceKind').textContent = r.status==='SYNTHETIC_TEST_ONLY'?'人工データ／実績ではない':'利用者提供・真正性未確認';
    $('observed').textContent = number(r.observed_bars_since_freeze);
    $('late').textContent = `時間内 ${number(r.on_time_bars)} / 遅延 ${number(r.delayed_bars)}`;
    $('events').textContent = number(r.operation_events);$('alerts').textContent = '停止通知 ' + number(r.alerts);
    $('recentAccuracy').textContent = percent(r.metrics.recent_24.accuracy);
    $('recentBrier').textContent = 'Brier '+ (r.metrics.recent_24.brier==null?'—':r.metrics.recent_24.brier.toFixed(4));
    $('lastObserved').textContent = r.last_observed_at_utc.slice(0,19).replace('T',' ');
    $('chartDesc').textContent = `直近 ${r.predictions.length}件表示。古い ${r.predictions_truncated_for_view}件は表示省略。未採点予測は結果を描画しません。`;
    const tbody=$('predictionRows');tbody.replaceChildren();
    r.predictions.slice(-30).reverse().forEach(p=>{
      const tr=tbody.insertRow();
      const fields=[p.signal_time.slice(0,16).replace('T',' '),percent(p.probability_up),p.side===1?'買い':p.side===-1?'売り':'見送り',p.actual_up===null?'未判定':p.actual_up?'上昇':'非上昇'];
      fields.forEach(v=>tr.insertCell().textContent=v);
    });
    drawProbability();
  }
  document.addEventListener('DOMContentLoaded',()=>{
    clear();
    $('clear').addEventListener('click',()=>{$('forwardFile').value='';clear();});
    $('forwardFile').addEventListener('change',async event=>{
      try {
        if(event.target.files.length!==1)throw Error('ローカルJSONを1ファイル選択してください');
        const result=await AegisForwardReport.load(event.target.files[0]);
        render(result);
      } catch(err) {clear();$('alertMsg').textContent='読み込み不可：'+err.message;}
      finally {event.target.value='';}
    });
    window.addEventListener('resize',drawProbability);
  });
})();
