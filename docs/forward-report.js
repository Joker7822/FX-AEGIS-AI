/* FX AEGIS AI: local-only frozen forward-monitor report viewer. */
'use strict';
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.AegisForwardReport = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  const SCHEMA = 'fx-aegis-private-forward-dashboard-v1';
  const MAX_BYTES = 1000000;
  const HEX = /^[0-9a-f]{64}$/;
  const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)$/;
  const GOOD = new Set(['OBSERVED_SYNTHETIC_TEST_ONLY', 'OBSERVED_USER_SUPPLIED_QUOTES', 'IDLE_NO_NEW_H1']);
  const BLOCKED = new Set(['BLOCKED_EXPECTED_H1_MISSING', 'BLOCKED_MODEL_DRIFT_REVIEW_REQUIRED', 'BLOCKED_OPERATIONAL_ERROR']);
  const validTime = t => typeof t === 'string' && ISO.test(t) && Number.isFinite(Date.parse(t));
  const int = x => Number.isSafeInteger(x) && x >= 0;
  const prob = x => typeof x === 'number' && Number.isFinite(x) && x >= 0 && x <= 1;
  function calculate(rows) {
    const done = rows.filter(r => r.actual_up !== null);
    const scored = done.length;
    const correct = done.filter(r => (r.probability_up > .5) === r.actual_up).length;
    return {predictions: rows.length, scored, pending: rows.length - scored,
      accuracy: scored ? correct / scored : null,
      brier: scored ? done.reduce((sum, r) => sum + (r.probability_up - Number(r.actual_up)) ** 2, 0) / scored : null};
  }
  function parse(raw) {
    if (typeof raw !== 'string' || new TextEncoder().encode(raw).length > MAX_BYTES)
      throw Error('ローカルJSONの上限は1MBです');
    let r;
    try { r = JSON.parse(raw); } catch (_) { throw Error('JSONを読み取れません'); }
    if (!r || Array.isArray(r) || r.schema !== SCHEMA || r.approved_for_live_trading !== false ||
        r.no_network_access !== true || r.source_authenticity_independently_verified !== false ||
        r.timestamp_independently_attested !== false ||
        r.execution_assumption !== 'NO_BROKER_ORDERS_RESEARCH_ONLY') throw Error('研究専用宣言が不一致です');
    if (!['SYNTHETIC_TEST_ONLY','UNVERIFIED_USER_SUPPLIED_QUOTES'].includes(r.status) ||
        !GOOD.has(r.supervisor_status) && !BLOCKED.has(r.supervisor_status) ||
        !['ACTIVE','PAUSED_MODEL_DRIFT_REVIEW_REQUIRED'].includes(r.monitor_status))
      throw Error('監視状態が不正です');
    if (!HEX.test(r.last_event_sha256 || '') || !HEX.test(r.frozen_lock_sha256 || '') ||
        r.quote_sha256 !== null && r.quote_sha256 !== undefined && !HEX.test(r.quote_sha256))
      throw Error('ハッシュの形式が不正です');
    if (!validTime(r.frozen_at_utc) || !validTime(r.last_observed_at_utc) ||
        Date.parse(r.last_observed_at_utc) < Date.parse(r.frozen_at_utc))
      throw Error('監視時刻が不正です');
    for (const name of ['operation_events','observed_bars_since_freeze','on_time_bars','delayed_bars','alerts','predictions_truncated_for_view'])
      if (!int(r[name])) throw Error('監査件数が不正です');
    if (r.on_time_bars + r.delayed_bars !== r.observed_bars_since_freeze || !r.operation_events)
      throw Error('観測件数が矛盾しています');
    if (!Array.isArray(r.predictions) || r.predictions.length > 720)
      throw Error('予測配列が不正です');
    let priorIndex = -1, priorTime = -Infinity;
    for (const p of r.predictions) {
      if (!p || !int(p.signal_index) || p.signal_index <= priorIndex ||
          !validTime(p.signal_time) ||
          !validTime(p.predicted_at_utc) || !prob(p.probability_up) ||
          ![-1,0,1].includes(p.side) ||
          !(p.actual_up === null || typeof p.actual_up === 'boolean') ||
          !(p.scored_at_utc === null || validTime(p.scored_at_utc))) throw Error('予測行が不正です');
      const ts = Date.parse(p.signal_time), at = Date.parse(p.predicted_at_utc);
      if (!Number.isFinite(ts) || ts <= priorTime || !(ts + 3600000 <= at && at < ts + 7200000) ||
          (p.actual_up === null) !== (p.scored_at_utc === null) ||
          (p.scored_at_utc !== null && Date.parse(p.scored_at_utc) < at)) throw Error('予測時刻の整合性が不正です');
      priorIndex = p.signal_index; priorTime = ts;
    }
    if (!r.metrics || typeof r.metrics !== 'object') throw Error('評価値がありません');
    for (const key of ['overall','recent_24','recent_168']) {
      const m = r.metrics[key];
      if (!m || !int(m.predictions) || !int(m.scored) || !int(m.pending) ||
          m.predictions !== m.scored + m.pending ||
          !(m.accuracy === null || prob(m.accuracy)) ||
          !(m.brier === null || prob(m.brier)) ||
          ((m.scored === 0) !== (m.accuracy === null)) ||
          ((m.scored === 0) !== (m.brier === null))) throw Error('評価値が不正です');
    }
    if (r.metrics.overall.predictions !== r.predictions.length + r.predictions_truncated_for_view ||
        r.metrics.recent_24.predictions > 24 || r.metrics.recent_168.predictions > 168)
      throw Error('表示範囲と総予測件数が矛盾しています');
    const last24 = calculate(r.predictions.slice(-24));
    const last168 = calculate(r.predictions.slice(-168));
    const approx = (a,b) => Math.abs(a-b) < 0.0000011;
    for (const [key, actual] of [['recent_24',last24],['recent_168',last168]]) {
      const m = r.metrics[key];
      if (r.predictions_truncated_for_view === 0 || r.predictions.length >= (key === 'recent_24'?24:168)) {
        if (m.predictions !== actual.predictions || m.scored !== actual.scored || m.pending !== actual.pending ||
            (m.accuracy === null ? actual.accuracy !== null : !approx(m.accuracy, actual.accuracy)) ||
            (m.brier === null ? actual.brier !== null : !approx(m.brier, actual.brier)))
          throw Error('予測記録と集計が一致しません');
      }
    }
    if (r.predictions_truncated_for_view === 0) {
      const total = calculate(r.predictions), m = r.metrics.overall;
      if (m.scored !== total.scored || m.pending !== total.pending ||
          (m.accuracy === null ? total.accuracy !== null : !approx(m.accuracy,total.accuracy)) ||
          (m.brier === null ? total.brier !== null : !approx(m.brier,total.brier)))
        throw Error('全期間の評価値が一致しません');
    }
    return r;
  }
  async function load(file) {
    if (!file || !/\.json$/i.test(file.name) || file.size > MAX_BYTES)
      throw Error('1MB以内のforward_status.jsonを選択してください');
    return parse(await file.text());
  }
  return {SCHEMA,parse,load,calculate};
});
