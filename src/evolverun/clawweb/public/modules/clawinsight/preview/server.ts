/** Explicit loopback-only UI acceptance fixture; never imported by production. */
import express from 'express';
import Database from 'better-sqlite3';
import { readFileSync } from 'node:fs';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { initializeMonitoringSqlite } from '../server/services/monitoring/schema';
import { MonitoringRepository } from '../server/repositories/monitoring-repository';
import { SqlMonitoringBotDirectory } from '../server/repositories/monitoring-bot-directory';
import { createMonitoringRuntime } from '../server/services/monitoring/monitoring-runtime';
import { createMonitoringRouter } from '../server/routes/monitoring';
import { parseCheck, parseDiagnosis } from '../server/services/monitoring/validation';

export async function createPreviewApp() {
  const db = new SqliteDatabase(new Database(':memory:'));
  await initializeMonitoringSqlite(db);
  await db.exec(`CREATE TABLE ac_bots (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT, entity_id TEXT, env TEXT,
    bot_name TEXT, owner_id TEXT, owner_name TEXT, avernet_tenant TEXT, is_delete INTEGER DEFAULT 0)`);
  const scope = { tenant: 'teamclaw', allowedTargetEnvs: ['dev', 'prod'] };
  const repo = new MonitoringRepository(db);
  const now = Date.now();
  const fixture = JSON.parse(readFileSync(new URL('../server/fixtures/monitoring/alert.json', import.meta.url), 'utf8'));
  const bot = async (botId: string, entityId: string, name: string, status: string | null, count: number, env = 'dev') => {
    await db.exec('INSERT INTO ac_bots (bot_id,entity_id,env,bot_name,owner_id,owner_name,avernet_tenant) VALUES (?,?,?,?,?,?,?)',
      [botId, entityId, env, name, entityId, entityId === '001234' ? '林小满' : '陈雨', scope.tenant]);
    const target = { botId, entityId, env };
    if (status) await repo.applyCheck({ target, wire: parseCheck({ schemaVersion: 'claw-monitoring/bot-check/v1', botId,
      engine: 'TE', status, checkedAt: new Date(now).toISOString(), lastSuccessfulCheckAt: new Date(now).toISOString() }, now) }, now);
    for (let i = 0; i < count; i++) {
      const eventId = `preview-${entityId}-${botId}-${env}-${i}`;
      const wire = parseDiagnosis({ ...fixture, botId, eventId, diagnosisId: eventId,
        sessionId: `session-${entityId}-${Math.floor(i / 2)}`, traceId: `trace-${eventId}`,
        decision: i % 3 === 0 ? 'ALERT' : 'PASS',
        confidence: i % 3 === 0 ? 0.86 : null,
        tcFaultLabel: i % 3 !== 0 ? null : i % 4 === 0 ? 'TC.TOOL.EXTERNAL_SERVICE.RESPONSE_TIMEOUT_WITH_EXTENDED_LABEL' : 'TC.MCP.DATA',
        occurredAt: new Date(now - i * 60_000).toISOString(), diagnosedAt: new Date(now).toISOString(),
      }, eventId);
      await repo.insertDiagnosis({ target, wire }, now);
    }
  };
  for (let i = 0; i < 26; i++) await bot(`assistant-${i}`, '001234', `工作助手 ${String(i + 1).padStart(2, '0')}`, i % 2 ? 'HEALTHY' : null, 0);
  await bot('default', '001234', '默认助手 · 生产', 'HEALTHY', 3, 'prod');
  await bot('default', '009876', '陈雨的默认助手', 'HEALTHY', 9);
  await bot('research-new', '009876', '陈雨的研究助手', null, 0);
  await bot('research-new', '001234', '研究助手', null, 0);
  await bot('weekly-report', '001234', '周报助手', 'PAUSED', 8);
  await bot('default', '001234', '我的默认助手', 'HEALTHY', 163);
  const app = express();
  const role = (cookie = '') => /(?:^|;\s*)monitoring_preview_role=(admin|member)(?:;|$)/.exec(cookie)?.[1];
  const runtime = createMonitoringRuntime(() => db, Date.now, {
    scope, isolatedStorageTenant: scope.tenant, directory: new SqlMonitoringBotDirectory(db, true),
    principal: async req => ({ ...scope, staffId: '001234', isAuthenticated: Boolean(role(req.headers.cookie)), isClawInsightAdmin: role(req.headers.cookie) === 'admin' }),
  });
  app.get('/preview/login/:role', (req, res) => {
    if (!['admin', 'member'].includes(req.params.role)) { res.sendStatus(400); return; }
    res.set('Set-Cookie', `monitoring_preview_role=${req.params.role}; Path=/; HttpOnly; SameSite=Strict`);
    res.redirect('/?module=monitoring');
  });
  app.get('/api/auth/me', (req, res) => {
    const current = role(req.headers.cookie);
    res.set('Cache-Control', 'no-store');
    if (!current) { res.sendStatus(401); return; }
    res.json({ userId: '001234', nickName: '林小满', userName: '001234', displayName: '林小满', avatarUrl: '',
      isAdmin: false, isClawInsightAdmin: current === 'admin' });
  });
  app.use((req, _res, next) => { req.isClawInsightAdmin = role(req.headers.cookie) === 'admin'; next(); });
  app.use('/api/insight/v1', createMonitoringRouter(runtime));
  return { app, db, close: () => db.close() };
}
