// @vitest-environment node
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { afterEach, expect, it, vi } from 'vitest';
import express from 'express';
import type { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { createWorkflowVersionComparisonService } from '../workflow-version-comparison-service.js';
import { createWorkflowNodeStatsRouter } from '../../routes/workflow-node-stats.js';
vi.mock('../workflow-version-source-service.js', () => ({ createWorkflowVersionSourceService: () => ({ forWorkflow: async () => new Map([
  [12, { kind: 'auto_optimization', baselineDeployNumber: 10, effectiveAtMs: 2_000_000_000_000 }],
]) }) }));
const databases: SqliteDatabase[] = [];
afterEach(async () => { for (const db of databases.splice(0)) await db.close(); });
function fixture() {
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw); databases.push(db);
  raw.exec(`CREATE TABLE workflow_deploy_history (workflow_id TEXT, deploy_number INTEGER, version INTEGER, is_active INTEGER, gmt_create INTEGER, action TEXT);
    CREATE TABLE flow_runs (workflow_id TEXT, workflow_deploy_number INTEGER, status TEXT, started_at INTEGER);
    INSERT INTO workflow_deploy_history VALUES ('wf',10,1,0,1999900000,'deploy'),('wf',11,1,0,1999950000,'rollback'),('wf',12,2,1,2000000000,'deploy'),('wf',13,3,0,2000000010,'edit'),('other',12,2,1,2000000000,'deploy');`);
  const insert = raw.prepare('INSERT INTO flow_runs VALUES (?, ?, ?, ?)');
  insert.run('wf',10,'succeeded',1999900001); insert.run('wf',10,'failed',1999900002);
  insert.run('wf',11,'failed',1999950001); insert.run('wf',12,'succeeded',2000000001);
  insert.run('wf',12,'cancelled',2000000002000); insert.run('wf',12,'running',2000000003);
  insert.run('wf',null,'failed',2000000004); insert.run('wf',999,'succeeded',2000000005);
  insert.run('other',12,'failed',2000000001); insert.run('wf',12,'succeeded',2000000100);
  return { raw, db, service: createWorkflowVersionComparisonService(db, () => 2_000_000_100_000) };
}
it('uses exact deployments, equal half-open windows and terminal statuses with mixed timestamp units', async () => {
  const { service } = fixture(); const result = await service.compare('wf');
  expect(result.versions.map(v => v.deployNumber)).toEqual([12,11,10]);
  expect(result.baseline).toMatchObject({ deployNumber:10, terminalRuns:2, succeededRuns:1, failedRuns:1, successRate:50 });
  expect(result.current).toMatchObject({ deployNumber:12, terminalRuns:2, succeededRuns:1, failedRuns:1, successRate:50, excludedUnfinished:1 });
  expect(result.windowMs).toBe(100000); expect(result.differencePercentagePoints).toBe(0);
  expect(result.excludedUnknownDeployment).toBe(2);
});
it('keeps redeployments of the same spec separate and allows arbitrary released pairs', async () => {
  const { service } = fixture(); const result = await service.compare('wf', { baseline:10, current:11 });
  expect(result.baseline?.successRate).toBe(50); expect(result.current?.successRate).toBe(0);
  expect(result.differencePercentagePoints).toBe(-50);
  await expect(service.compare('wf', { current:13 })).rejects.toThrow('actual release');
  await expect(service.compare('wf', { baseline:12, current:12 })).rejects.toThrow('distinct');
});
it('does not invent baselines or zero percent for empty observations and isolates workflows', async () => {
  const { service, raw } = fixture();
  expect((await service.compare('wf', { current:11 })).status).toBe('choose_baseline');
  raw.exec("DELETE FROM flow_runs WHERE workflow_id='wf' AND workflow_deploy_number=12");
  const result = await service.compare('wf'); expect(result.status).toBe('no_samples');
  expect(result.current?.successRate).toBeNull(); expect(result.differencePercentagePoints).toBeNull();
  expect((await service.compare('absent')).status).toBe('no_active_version');
});
it('validates HTTP input and exposes only the requested workflow through the existing protected router mount', async () => {
  const { service } = fixture(); const app = express();
  const permissions = { getViewByIdsForOwner: async (actor: string) => ({ viewableIds:new Set(actor === 'owner' ? ['wf'] : []) }) } as unknown as BotWorkflowPermissionRepository;
  app.use('/api/workflows', createWorkflowNodeStatsRouter(null,null,service,permissions));
  const server = app.listen(0); const address = server.address() as {port:number}; const base = `http://127.0.0.1:${address.port}/api/workflows/wf/version-comparison`;
  try {
    expect((await fetch(base)).status).toBe(401);
    expect((await fetch(base, {headers:{'X-Staff-Id':'outsider'}})).status).toBe(403);
    expect((await fetch(base.replace('/wf/','/other/'), {headers:{'X-Staff-Id':'owner'}})).status).toBe(403);
    expect((await fetch(`${base}?days=7oops`, {headers:{'X-Staff-Id':'owner'}})).status).toBe(400);
    const response = await fetch(base, {headers:{'X-Staff-Id':'owner'}}); expect(response.status).toBe(200);
    expect((await response.json()).data.current.terminalRuns).toBe(2);
  } finally { await new Promise<void>((resolve,reject) => server.close(error => error ? reject(error) : resolve())); }
});
