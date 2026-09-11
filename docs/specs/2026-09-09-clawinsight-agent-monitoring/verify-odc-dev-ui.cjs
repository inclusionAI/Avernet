'use strict';
/** Local UI acceptance host: real monitoring router + MysqlDatabase + guarded mesh dev datasource.
 * Never runs migrations. Seeds ONLY isolated per-run mock bots via actual HTTP POST.
 * SIGINT/SIGTERM removes ONLY this run's exact event IDs and bot IDs, then closes the host.
 * Required: CLAWWEB_DEV_CONFIG_FILE pointing to your existing private dev YAML.
 * Optional: MONITORING_UI_PORT (default 3001, matches public Vite proxy).
 * MONITORING_UI_MINUTES: 1..480, default 30.
 * MONITORING_UI_KEEP_CHECKS=true: refresh only this run's healthy/error mock checks every minute.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const { pathToFileURL } = require('node:url');
const repo = path.resolve(__dirname, '../../..');
const req = createRequire(path.join(repo, 'src/evolverun/clawweb/package.json'));
const root = path.join(repo, 'src/evolverun/clawweb/public');
const load = rel => import(pathToFileURL(path.join(root, rel)).href);
let pool, server, scenario, stopping = false;
let cleanupTimer, checkTimer;
let checkInFlight = Promise.resolve();
async function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  clearTimeout(cleanupTimer);
  clearInterval(checkTimer);
  try {
    await checkInFlight;
    if (server) await new Promise(resolve => server.close(resolve));
    if (pool && scenario) {
      for (const event of scenario.events) await pool.execute('DELETE FROM insight_monitoring_diagnoses WHERE event_id = ? AND bot_id = ?', [event.eventId, event.botId]);
      for (const bot of scenario.bots) await pool.execute('DELETE FROM insight_monitoring_bot_checks WHERE bot_id = ?', [bot.botId]);
      const placeholders = scenario.bots.map(() => '?').join(',');
      for (const table of ['insight_monitoring_diagnoses', 'insight_monitoring_bot_checks']) {
        const [[row]] = await pool.query(`SELECT COUNT(*) n FROM ${table} WHERE bot_id IN (${placeholders})`, scenario.bots.map(b => b.botId));
        assert.equal(Number(row.n), 0);
      }
      console.log('CLEANUP_OK: this run has zero remaining rows; other bots untouched.');
    }
  } catch (error) { code = 1; console.error('CLEANUP_FAILED:', error.code || error.name, 'Use the printed run ID to investigate.'); }
  finally { await pool?.end(); process.exitCode = code; }
}
async function main() {
  const minutes = Number(process.env.MONITORING_UI_MINUTES || 30);
  assert.ok(Number.isInteger(minutes) && minutes >= 1 && minutes <= 480, 'MONITORING_UI_MINUTES must be 1..480');
  const keepChecks = process.env.MONITORING_UI_KEEP_CHECKS === 'true';
  const configFile = process.env.CLAWWEB_DEV_CONFIG_FILE;
  if (!configFile) throw new Error('Set CLAWWEB_DEV_CONFIG_FILE to your private local dev YAML (never commit it).');
  const ds = req('yaml').parse(fs.readFileSync(configFile, 'utf8')).database.zdas.datasources[0];
  assert.ok(ds.host === '127.0.0.1' && Number(ds.port) === 11306 && ds.database === 'clawweb_ds' && ds.user, 'Refusing non-local-mesh datasource');
  pool = req('mysql2/promise').createPool({ host: ds.host, port: Number(ds.port), user: ds.user,
    password: ds.password || '', database: ds.database, charset: 'utf8mb4', connectionLimit: 5, connectTimeout: 8000 });
  const [[identity]] = await pool.query('SELECT DATABASE() db');
  assert.equal(identity.db, 'agentclawdb', 'Refusing unexpected physical database');
  console.log('DEV_DATASOURCE_OK (credentials not printed; no migrations)');
  const { MysqlDatabase } = await load('shared/dist/server/db.js');
  const { createMonitoringRuntime } = await load('modules/clawinsight/dist/server/services/monitoring/monitoring-runtime.js');
  const { createMonitoringRouter } = await load('modules/clawinsight/dist/server/routes/monitoring.js');
  const { createMonitoringMockScenario } = await load('modules/clawinsight/dist/server/services/monitoring/mock-scenarios.js');
  const runId = crypto.randomUUID();
  const now = Date.now();
  scenario = createMonitoringMockScenario(runId, now);
  console.log(`RUN_ID=${runId}`);
  const db = new MysqlDatabase(pool, 'zdas');
  const runtime = createMonitoringRuntime(() => db, { CLAWWEB_MONITORING_ENABLED: 'true',
    CLAWWEB_MONITORING_BOTS_JSON: JSON.stringify(scenario.bots), CLAWWEB_MONITORING_STALE_SECONDS: '300' });
  assert.ok(runtime.service);
  const app = req('express')();
  app.use(req('express').json({ limit: '10mb' }));
  app.use('/api/insight/v1', createMonitoringRouter(runtime));
  const port = Number(process.env.MONITORING_UI_PORT || 3001);
  assert.ok(Number.isInteger(port) && port > 0 && port < 65536);
  server = await new Promise((resolve, reject) => { const s = app.listen(port, '127.0.0.1', () => resolve(s)); s.once('error', reject); });
  const base = `http://127.0.0.1:${port}/api/insight/v1`;
  async function call(route, body) {
    const response = await fetch(base + route, body ? { method: 'POST', headers: { 'Content-Type': 'application/json',
       ...(body.eventId ? { 'Idempotency-Key': body.eventId } : {}) }, body: JSON.stringify(body), signal: AbortSignal.timeout(10000) } : { signal: AbortSignal.timeout(10000) });
    const result = await response.json();
    assert.ok(response.ok, `${route}: HTTP ${response.status} ${JSON.stringify(result)}`);
    return result;
  }
  for (const event of scenario.events) {
    const ack = await call('/internal/monitoring/diagnosis-events', event);
    assert.equal(ack.stored, true); assert.equal(ack.duplicate, false);
  }
  const duplicate = await call('/internal/monitoring/diagnosis-events', scenario.events[0]);
  assert.equal(duplicate.duplicate, true);
  for (const check of scenario.checks) assert.equal((await call('/internal/monitoring/bot-checks', check)).applied, true);
  assert.equal((await call('/monitoring/bots')).items.length, 4);
  for (const [i, bot] of scenario.bots.entries()) {
    const prefix = `/monitoring/bots/${encodeURIComponent(bot.botId)}`;
    const status = await call(prefix + '/status');
    assert.equal(status.status, ['HEALTHY', 'ERROR', 'UNKNOWN', 'PAUSED'][i]);
    assert.equal(status.diagnosisCount, i < 2 ? 30 : 0);
    if (i >= 2) continue;
    const page = await call(prefix + '/diagnoses?page=2&pageSize=10');
    assert.equal(page.page, 2); assert.equal(page.items.length, 10); assert.equal(page.total, 30);
    assert.deepEqual(page.counts, { all: 30, alert: 10, pass: 10, unresolved: 10 });
    const alert = await call(prefix + '/diagnoses?decision=ALERT');
    assert.equal(alert.total, 10); assert.ok(alert.items.every(x => x.decision === 'ALERT'));
    const date = new Date(now + 8 * 3600000).toISOString().slice(0, 10);
    const range = await call(prefix + `/diagnoses?startDate=${date}&endDate=${date}`);
    assert.ok(range.total > 0 && range.total < 30);
    assert.ok(range.items.every(x => x.occurredAt && new Date(Date.parse(x.occurredAt) + 8 * 3600000).toISOString().slice(0, 10) === date));
    const all = await call(prefix + '/diagnoses?pageSize=50');
    assert.equal(all.items.at(-1).occurredAt, null);
    assert.equal(new Set(all.items.map(x => x.humanIntervention)).size, 2);
    const keyword = bot.engine === 'TE' ? 'mock-trace-29' : 'agent:main:mock:29';
    assert.equal((await call(prefix + '/diagnoses?keyword=' + encodeURIComponent(keyword))).total, 1);
  }
  const [[rows]] = await pool.query('SELECT COUNT(*) n FROM insight_monitoring_diagnoses WHERE bot_id IN (?, ?)', scenario.bots.slice(0, 2).map(b => b.botId));
  assert.equal(Number(rows.n), 60);
  console.log('API_ACCEPTANCE_OK: 60 persisted events, duplicate ACK, OC/TE pagination, dates, search, intervention, 4 status states.');
  if (keepChecks) {
    let checking = false;
    checkTimer = setInterval(() => {
      if (stopping || checking) return;
      checking = true;
      checkInFlight = (async () => {
        const timestamp = new Date().toISOString();
        for (const check of scenario.checks.slice(0, 2)) {
          const result = await call('/internal/monitoring/bot-checks', { ...check, checkedAt: timestamp,
            lastSuccessfulCheckAt: check.status === 'HEALTHY' ? timestamp : check.lastSuccessfulCheckAt });
          assert.equal(result.applied, true);
        }
      })().catch(error => console.error('MOCK_CHECK_REFRESH_FAILED:', error.code || error.name))
        .finally(() => { checking = false; });
    }, 60 * 1000);
  }
  const cleanupAt = new Date(Date.now() + minutes * 60 * 1000).toISOString();
  console.log('MOCK_BOTS=' + JSON.stringify(scenario.bots.map(b => b.botId)));
  console.log(`UI_READY: public Vite /insight?module=monitoring; Ctrl-C cleans ONLY this run. Auto-cleanup in ${minutes} minutes (${cleanupAt}).`);
  console.log(`MOCK_CHECK_REFRESH=${keepChecks}: synthetic POST heartbeats only; no AIStudio connection or new diagnosis generation.`);
  cleanupTimer = setTimeout(() => void stop(), minutes * 60 * 1000);
}
process.once('SIGINT', () => void stop());
process.once('SIGTERM', () => void stop());
main().catch(async error => { console.error('UI_ACCEPTANCE_FAILED:', error.code || error.name, error.message?.replace(/password[^\s]*/gi, '[redacted]')); await stop(1); });
