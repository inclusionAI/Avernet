import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import express from 'express';
import type { Server } from 'node:http';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { timeoutFixture } from '../../repositories/__tests__/timeout-write.fixture.js';
import { FlowRunRepository } from '../../repositories/flow-run-repository.js';
import { NodeExecutionRepository } from '../../repositories/node-execution-repository.js';
import { FlowEventRepository } from '../../repositories/event-repository.js';
import { createRunsRouter } from '../runs.js';

let f: ReturnType<typeof timeoutFixture>; let server: Server | undefined;
beforeEach(() => {
  f = timeoutFixture();
  // Include the actual lightweight-list fields so the old route reaches abort,
  // rather than making the epoch regression accidentally pass via a missing column.
  for (const column of ['workflow_id', 'workflow_title', 'parent_flow_id', 'root_flow_id', 'node_count', 'triggered_by', 'identity_key',
    'origin_session_key', 'origin_session_id', 'origin_bot_id', 'user_id', 'plugin_version', 'engine', 'env', 'tag',
    'workflow_version', 'workflow_deploy_number', 'gmt_create', 'evolution_analysis_status', 'id']) {
    f.raw.exec(`ALTER TABLE flow_runs ADD COLUMN ${column} TEXT`);
  }
  f.raw.exec(`ALTER TABLE node_executions ADD COLUMN error_text TEXT; ALTER TABLE node_executions ADD COLUMN completed_at INTEGER;
    ALTER TABLE node_executions ADD COLUMN gmt_modified INTEGER;
    INSERT INTO node_executions (flow_id,status) VALUES ('flow','running');
    CREATE TABLE flow_events (event_id TEXT,flow_id TEXT,workflow_id TEXT,node_id TEXT,event_type TEXT,attempt INTEGER,time INTEGER,
      data_json TEXT,error_text TEXT,gmt_create INTEGER,gmt_modified INTEGER);
    CREATE TABLE update_audit (id INTEGER);
    CREATE TRIGGER audit_update AFTER UPDATE ON flow_runs BEGIN INSERT INTO update_audit VALUES (1); END;`);
});
afterEach(async () => {
  if (server) await new Promise<void>(resolve => { server!.closeAllConnections(); server!.close(() => resolve()); });
  server = undefined; await f.close(); vi.restoreAllMocks();
});
function seed(epoch: number | null | 'absent' = 2, status = 'running') {
  f.seed({}, { epoch: epoch === 'absent' ? 0 : epoch, status });
  f.raw.exec("UPDATE flow_runs SET workflow_id='wf'; DELETE FROM update_audit");
  if (epoch === 'absent') f.raw.exec('ALTER TABLE flow_runs DROP COLUMN retry_epoch');
}
async function http(db: IDatabase = f.db, available = true) {
  const repo = new FlowRunRepository(db); const nodes = new NodeExecutionRepository(db);
  const reconcile = vi.spyOn(nodes, 'reconcileStaleRunning');
  const controls = { releaseAllSlotsForFlowByFlowId: vi.fn().mockResolvedValue(1), deleteQueueEntriesForFlowByFlowId: vi.fn().mockResolvedValue(1) };
  const app = express(); app.use(express.json());
  app.use('/runs', createRunsRouter(available ? repo : null, nodes, new FlowEventRepository(db), null, null, controls as never));
  server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  const post = () => fetch(`http://127.0.0.1:${(server!.address() as { port: number }).port}/runs/flow/abort`, {
    method: 'POST', headers: { 'content-type': 'application/json', 'x-user-id': 'operator' }, body: JSON.stringify({ reason: 'stop this attempt' }),
  });
  return { post, reconcile, controls };
}
const nodes = () => f.raw.prepare('SELECT * FROM node_executions').all();
const events = () => f.raw.prepare('SELECT * FROM flow_events').all();
const updates = () => f.raw.prepare('SELECT COUNT(*) AS count FROM update_audit').get() as { count: number };

it.each([0, null, 'absent', 2] as const)('real HTTP abort carries the original %s epoch through node cleanup and audit', async epoch => {
  seed(epoch); const started = f.row().started_at; const h = await http();
  const response = await h.post(); expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ ok: true, flowId: 'flow', status: 'cancelled', reconciledNodes: 1 });
  expect(f.row()).toMatchObject({ status: 'cancelled', started_at: started }); expect(updates()).toEqual({ count: 1 });
  expect(nodes()).toEqual([expect.objectContaining({ status: 'skipped', error_text: expect.stringContaining('stop this attempt') })]);
  expect(h.reconcile).toHaveBeenCalledExactlyOnceWith('flow', 'cancelled', expect.stringContaining('stop this attempt'), epoch === 'absent' ? undefined : epoch ?? undefined);
  expect(h.controls.releaseAllSlotsForFlowByFlowId).toHaveBeenCalledExactlyOnceWith('flow');
  expect(h.controls.deleteQueueEntriesForFlowByFlowId).toHaveBeenCalledExactlyOnceWith('flow');
  expect(events()).toEqual([expect.objectContaining({ event_type: 'flow_aborted', workflow_id: 'wf' })]);
});
it.each(['succeeded', 'failed', 'retry'] as const)('rejects %s winning after the original route read, without borrowing the new epoch', async winner => {
  seed(); let raced = false; let accepted: Record<string, unknown> | undefined;
  const db = new Proxy(f.db, { get(target, key) {
    if (key === 'query') return async <T>(sql: string, params?: unknown[]) => {
      const rows = await target.query<T>(sql, params);
      if (!raced && /^\s*SELECT\b/i.test(sql) && /workflow_id/.test(sql) && /FROM flow_runs/i.test(sql)) {
        raced = true;
        expect(await f.repo.updateCompletion('flow', { status: winner === 'succeeded' ? 'succeeded' : 'failed', retryEpoch: 2,
          resultJson: 'winner', completedAt: 123 })).toBe(true);
        if (winner === 'retry') expect(await f.repo.resumeForRetry('flow', 3)).toBe('resumed');
        accepted = f.row(); f.raw.exec('DELETE FROM update_audit');
      }
      return rows;
    };
    const member = Reflect.get(target, key); return typeof member === 'function' ? member.bind(target) : member;
  } });
  const h = await http(db); const beforeNodes = nodes(); const response = await h.post();
  expect(raced).toBe(true); expect(response.status).toBe(409); expect(f.row()).toEqual(accepted); expect(updates()).toEqual({ count: 0 });
  expect(nodes()).toEqual(beforeNodes); expect(events()).toEqual([]); expect(h.reconcile).not.toHaveBeenCalled();
  expect(h.controls.releaseAllSlotsForFlowByFlowId).not.toHaveBeenCalled(); expect(h.controls.deleteQueueEntriesForFlowByFlowId).not.toHaveBeenCalled();
});
it.each(['succeeded', 'failed', 'completed', 'cancelled', 'canceled'])('rejects already terminal %s without writes', async status => {
  seed(2, status); const before = f.row(); const h = await http();
  expect((await h.post()).status).toBe(409); expect(f.row()).toEqual(before); expect(updates()).toEqual({ count: 0 });
  expect(h.reconcile).not.toHaveBeenCalled(); expect(events()).toEqual([]);
});
it('database write errors are500, not a successful abort or conflict', async () => {
  seed(); const before = f.row(); f.raw.exec("CREATE TRIGGER fail_abort BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'write unavailable'); END");
  const h = await http(); expect((await h.post()).status).toBe(500); expect(f.row()).toEqual(before);
  expect(h.reconcile).not.toHaveBeenCalled(); expect(events()).toEqual([]); expect(h.controls.releaseAllSlotsForFlowByFlowId).not.toHaveBeenCalled();
});
it('database snapshot read errors are500 rather than missing404', async () => {
  seed(); const h = await http(); vi.spyOn(f.db, 'query').mockRejectedValueOnce(new Error('read unavailable'));
  expect((await h.post()).status).toBe(500); expect(updates()).toEqual({ count: 0 }); expect(h.reconcile).not.toHaveBeenCalled();
});
it('missing parent remains404', async () => { const h = await http(); expect((await h.post()).status).toBe(404); expect(updates()).toEqual({ count: 0 }); });
it('unconfigured database remains503', async () => { const h = await http(f.db, false); expect((await h.post()).status).toBe(503); });
