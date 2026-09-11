'use strict';
/* End-to-end: real HTTP monitoring route -> real ODC dev DB (via meshboot 127.0.0.1:11306).
   Reuses production MysqlDatabase adapter; does NOT run migrations. Writes mock-e2e-* rows only.
   Read-only introspection first; aborts if datasource is not the local mesh dev proxy. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { createRequire } = require('node:module');
const { pathToFileURL } = require('node:url');
const root = '/Users/wjh/workspace';
const req = createRequire(path.join(root, 'Avernet/src/evolverun/clawweb/package.json'));
const express = req('express');
const mysql = req('mysql2/promise');
const yaml = req('yaml');
const DIST = path.join(root, 'Avernet/src/evolverun/clawweb/public');
const importDist = (rel) => import(pathToFileURL(path.join(DIST, rel)).href);

let pool;
const pass = [], fail = [];
const check = (name, cond, detail) => { (cond ? pass : fail).push(name); console.log(`${cond ? 'PASS' : 'FAIL'} | ${name}${detail ? ' :: ' + detail : ''}`); };
const j = (x) => { try { return JSON.stringify(x); } catch { return String(x); } };

(async () => {
  const configFile = path.join(root, 'ocb/src/evolverun/clawweb/internal/configs/application-default.yaml');
  const ds = yaml.parse(fs.readFileSync(configFile, 'utf8')).database.zdas.datasources[0];
  const safe = ds.host === '127.0.0.1' && Number(ds.port) === 11306 && ds.database === 'clawweb_ds' && !!ds.user;
  if (!safe) throw new Error('Refusing: datasource is not the local mesh dev proxy (127.0.0.1:11306/clawweb_ds). Aborting to protect non-dev DBs.');
  pool = mysql.createPool({ host: ds.host, port: Number(ds.port), user: ds.user, password: ds.password || '',
    database: ds.database, charset: 'utf8mb4', connectionLimit: 5, connectTimeout: 8000 });
  console.log('[config] local mesh dev datasource OK (creds not printed)');

  // ---------- SECTION A: read-only schema introspection ----------
  console.log('\n===== SECTION A: READ-ONLY SCHEMA INTROSPECTION =====');
  for (const t of ['insight_monitoring_diagnoses', 'insight_monitoring_bot_checks']) {
    const [cols] = await pool.query(`SELECT COLUMN_NAME name, COLUMN_TYPE type, IS_NULLABLE nullable, COLLATION_NAME collation, COLUMN_KEY ckey, EXTRA extra, COLUMN_DEFAULT def
      FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION`, [t]);
    const [idx] = await pool.query(`SELECT INDEX_NAME name, NON_UNIQUE nonu, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) cols
      FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? GROUP BY INDEX_NAME`, [t]);
    console.log(`\n-- ${t}: ${cols.length} columns, ${idx.length} indexes`);
    for (const c of cols) console.log(`   ${String(c.name).padEnd(34)} ${String(c.type).padEnd(22)} null=${c.nullable} coll=${c.collation || '-'} key=${c.ckey || '-'} def=${c.def === null ? 'NULL' : String(c.def)} extra=${c.extra || '-'}`);
    for (const i of idx) console.log(`   IDX ${String(i.name).padEnd(34)} unique=${i.nonu == 0 ? 'YES' : 'no'} cols=[${i.cols}]`);
  }

  // ---------- build runtime with real MysqlDatabase adapter (no migrations) ----------
  const { MysqlDatabase } = await importDist('shared/dist/server/db.js');
  const db = new MysqlDatabase(pool, 'zdas');
  const { createMonitoringRuntime } = await importDist('modules/clawinsight/dist/server/services/monitoring/monitoring-runtime.js');
  const { createMonitoringRouter } = await importDist('modules/clawinsight/dist/server/routes/monitoring.js');
  const runId = crypto.randomUUID();
  const T = Date.now();
  const iso = (ms) => new Date(ms).toISOString();
  const env = { CLAWWEB_MONITORING_ENABLED: 'true',
    CLAWWEB_MONITORING_BOTS_JSON: JSON.stringify([{ botId: 'mock-bot-te', engine: 'TE' }, { botId: 'mock-bot-oc', engine: 'OC' }]),
    CLAWWEB_MONITORING_STALE_SECONDS: '999999' };
  const runtime = createMonitoringRuntime(() => db, env, () => Date.now());
  if (!runtime.service) throw new Error('runtime.service is null — monitoring config invalid');
  const app = express();
  app.use(express.json({ limit: '10mb' }));
  app.use('/api/insight/v1', createMonitoringRouter(runtime));
  const server = await new Promise((res, rej) => { const s = app.listen(0, '127.0.0.1', (e) => e ? rej(e) : res(s)); s.once('error', rej); });
  const port = server.address().port;
  const base = `http://127.0.0.1:${port}/api/insight/v1`;
  console.log(`\n[host] monitoring route mounted at ${base}`);

  const call = async (p, init) => { const r = await fetch(base + p, init); let body; try { body = await r.json(); } catch { body = null; } return { status: r.status, body }; };
  const postEvent = (event, headers = {}) => call('/internal/monitoring/diagnosis-events', { method: 'POST', headers: { 'Content-Type': 'application/json',  'Idempotency-Key': String(event.eventId), ...headers }, body: JSON.stringify(event) });
  const postCheck = (chk, headers = {}) => call('/internal/monitoring/bot-checks', { method: 'POST', headers: { 'Content-Type': 'application/json',  ...headers }, body: JSON.stringify(chk) });

  const alert = JSON.parse(fs.readFileSync(path.join(DIST, 'modules/clawinsight/server/fixtures/monitoring/alert.json'), 'utf8'));
  const passFix = JSON.parse(fs.readFileSync(path.join(DIST, 'modules/clawinsight/server/fixtures/monitoring/pass.json'), 'utf8'));
  const e0 = { ...alert, eventId: `mock-e2e-${runId}-0`, diagnosisId: `mock-e2e-${runId}-0` };           // ALERT, TE, confidence 0.86
  const e1 = { ...passFix, eventId: `mock-e2e-${runId}-1`, diagnosisId: `mock-e2e-${runId}-1` };          // PASS, TE, confidence null
  const checkNew = { schemaVersion: 'claw-monitoring/bot-check/v1', botId: 'mock-bot-te', engine: 'TE', checkedAt: iso(T), lastSuccessfulCheckAt: iso(T), status: 'HEALTHY' };

  console.log('\n===== SECTION B: HTTP WRITE/READ FLOW (real route -> real DB) =====');
  let r;
  r = await postEvent(e0); check('B1 POST diagnosis(alert) -> 201 stored not-dup', r.status === 201 && r.body?.stored === true && r.body?.duplicate === false && r.body?.eventId === e0.eventId, `status=${r.status} body=${j(r.body)}`);
  r = await postEvent(e0); check('B2 duplicate POST -> 200 duplicate=true', r.status === 200 && r.body?.stored === true && r.body?.duplicate === true, `status=${r.status} body=${j(r.body)}`);
  r = await postEvent({ ...e0, businessDiagnosis: 'CHANGED-CONTENT' }); check('B3 changed content same id -> 409 conflict', r.status === 409 && r.body?.error?.code === 'MONITORING_EVENT_CONFLICT', `status=${r.status} code=${r.body?.error?.code} body=${j(r.body)}`);
  r = await postEvent({ ...alert, eventId: `mock-e2e-${runId}-x`, diagnosisId: `mock-e2e-${runId}-x`, botId: 'foreign-bot', engine: 'TE' }); check('B4 foreign bot -> 403 not-allowed', r.status === 403 && r.body?.error?.code === 'MONITORING_BOT_NOT_ALLOWED', `status=${r.status} code=${r.body?.error?.code}`);
  r = await postEvent(e0); check('B5 no Authorization -> duplicate ACK', r.status === 200 && r.body?.duplicate === true, `status=${r.status}`);
  r = await postEvent(e1); check('B6 POST diagnosis(pass) -> 201', r.status === 201 && r.body?.stored === true, `status=${r.status} body=${j(r.body)}`);
  r = await postCheck(checkNew); check('B7 POST bot-check new -> 200 applied=true', r.status === 200 && r.body?.applied === true, `status=${r.status} body=${j(r.body)}`);
  r = await postCheck({ ...checkNew, checkedAt: iso(T - 60000), lastSuccessfulCheckAt: iso(T - 60000) }); check('B8 POST bot-check older ts -> applied=false (ignored)', r.status === 200 && r.body?.applied === false, `status=${r.status} body=${j(r.body)}`);
  r = await postCheck({ ...checkNew, status: 'ERROR' }); check('B9 POST bot-check same ts diff status -> 409 conflict', r.status === 409 && r.body?.error?.code === 'MONITORING_EVENT_CONFLICT', `status=${r.status} code=${r.body?.error?.code}`);
  r = await call('/monitoring/bots'); check('B10 GET bots -> 2 items', r.status === 200 && r.body?.items?.length === 2, `status=${r.status} len=${r.body?.items?.length}`);
  r = await call('/monitoring/bots/mock-bot-te/status'); check('B11 GET status -> HEALTHY count>=2', r.status === 200 && r.body?.status === 'HEALTHY' && r.body?.diagnosisCount >= 2, `status=${r.status} body=${j(r.body)}`);
  r = await call('/monitoring/bots/mock-bot-te/diagnoses?page=1&pageSize=50'); check('B12 GET diagnoses -> counts reflect alert+pass', r.status === 200 && (r.body?.counts?.alert) >= 1 && (r.body?.counts?.pass) >= 1 && (r.body?.counts?.all) >= 2, `status=${r.status} counts=${j(r.body?.counts)} items=${r.body?.items?.length}`);
  r = await call('/monitoring/bots/mock-bot-te/diagnoses?page=1&pageSize=50&decision=ALERT'); check('B13 GET diagnoses decision=ALERT -> all items ALERT', r.status === 200 && (r.body?.items || []).every((i) => i.decision === 'ALERT') && r.body?.counts?.alert >= 1, `status=${r.status} items=${r.body?.items?.length} total=${r.body?.total}`);
  r = await call('/monitoring/bots/nope/status'); check('B14 GET status unknown bot -> 404', r.status === 404, `status=${r.status} code=${r.body?.error?.code}`);

  console.log('\n===== SECTION C: DIRECT DB READBACK (raw pool, independent of route) =====');
  const [[cRow]] = await pool.query(`SELECT COUNT(*) c FROM insight_monitoring_diagnoses WHERE bot_id = 'mock-bot-te' AND event_id LIKE ?`, [`mock-e2e-${runId}-%`]);
  check('C1 raw count of inserted TE diagnoses = 2', Number(cRow.c) === 2, `count=${cRow.c}`);
  const [[dRow]] = await pool.query(`SELECT event_id, bot_id, engine, decision, human_intervention, confidence_json, business_problem_category, system_diagnosis, business_diagnosis FROM insight_monitoring_diagnoses WHERE event_id = ?`, [e0.eventId]);
  check('C2 e0 row content matches fixture', !!dRow && dRow.bot_id === 'mock-bot-te' && dRow.engine === 'TE' && dRow.decision === 'ALERT' && Number(dRow.human_intervention) === 1 && dRow.confidence_json === '0.86' && dRow.business_problem_category === '外部服务异常' && dRow.business_diagnosis === alert.businessDiagnosis, `row=${j(dRow)}`);
  const [[bRow]] = await pool.query(`SELECT bot_id, status, checked_at_ms FROM insight_monitoring_bot_checks WHERE bot_id = 'mock-bot-te'`);
  check('C3 bot_check row is newer HEALTHY @T', !!bRow && bRow.status === 'HEALTHY' && Number(bRow.checked_at_ms) === T, `row=${j(bRow)}`);

  await new Promise((res) => server.close(() => res()));
  await db.close();
  console.log(`\n===== SUMMARY: ${pass.length} PASS, ${fail.length} FAIL =====`);
  if (fail.length) console.log('FAILED:\n - ' + fail.join('\n - '));
  console.log(`runId=${runId}`);
  console.log(`test data: event_id LIKE 'mock-e2e-${runId}-%' + bot_check mock-bot-te remain in dev DB.`);
  console.log(`cleanup: DELETE FROM insight_monitoring_diagnoses WHERE event_id LIKE 'mock-e2e-${runId}-%'; DELETE FROM insight_monitoring_bot_checks WHERE bot_id='mock-bot-te';`);
  process.exit(fail.length ? 1 : 0);
})().catch(async (e) => {
  console.error('E2E_ERROR', e?.stack || e);
  try { await pool?.end(); } catch {}
  process.exit(1);
});
