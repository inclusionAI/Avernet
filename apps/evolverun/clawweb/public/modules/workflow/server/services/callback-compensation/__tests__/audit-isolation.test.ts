import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { CallbackCompensationRepository } from '../../../repositories/callback-compensation-repository.js';
import { hash } from '../protection.js';
import type { CallbackCompensationService } from '../service.js';
import { fixture, type Fixture } from './fixture.js';

let f: Fixture;
let requestNumber: number;
let failedAuditAttempts: number;

beforeEach(() => {
  f = fixture();
  f.config();
  requestNumber = 0;
  failedAuditAttempts = 0;
});
afterEach(async () => { await f.close(); });

async function addRun(flowId: string) {
  f.run(flowId);
  await f.authorize(flowId);
}

async function queue(service: CallbackCompensationService, flowIds: string[]) {
  const preview = await service.preview('wf', 'admin', { configId: 'callback-string-id', flowIds });
  expect(preview.summary.eligible).toBe(flowIds.length);
  return service.create('wf', 'admin', {
    previewId: preview.previewId,
    itemIds: preview.items.map(item => item.itemId),
    reason: 'Reviewed audit isolation test notification',
    requestKey: `audit-isolation-test-${++requestNumber}`,
  });
}

function blockAudit(backlog = false) {
  // A JS counter observes actual SQLite trigger executions even when the insert rolls back.
  f.raw.function('count_failed_audit', () => { failedAuditAttempts++; return 0; });
  f.raw.exec(`CREATE TRIGGER fail_audit BEFORE INSERT ON http_callback_logs
    WHEN ${backlog ? "NEW.flow_id LIKE 'audit-backlog-%'" : "NEW.flow_id = 'flow-1'"}
    BEGIN SELECT count_failed_audit(); SELECT RAISE(ABORT, 'isolated audit failure'); END`);
}

function item(flowId: string) {
  return f.raw.prepare(`SELECT state, http_status, audit_pending, audit_id
    FROM workflow_callback_compensation_items WHERE flow_id = ?`).get(flowId);
}

function postedFlows() {
  return f.calls.map(call => JSON.parse(call.rawBody).flow_id);
}

function auditCounts() {
  return f.raw.prepare('SELECT flow_id, COUNT(*) AS n FROM http_callback_logs GROUP BY flow_id ORDER BY flow_id').all();
}

describe('per-item audit failure isolation', () => {
  it.each([
    { layout: 'same batch', firstStatus: 204, firstState: 'received' },
    { layout: 'different batches', firstStatus: 204, firstState: 'received' },
    { layout: 'same batch', firstStatus: 503, firstState: 'unknown' },
    { layout: 'different batches', firstStatus: 503, firstState: 'unknown' },
  ])('finishes healthy work before reporting a post-settlement audit failure ($layout, $firstState)', async ({ layout, firstStatus, firstState }) => {
    await addRun('flow-1');
    await addRun('flow-2');
    const service = f.service({ transport: async request => {
      f.calls.push(request);
      return { status: JSON.parse(request.rawBody).flow_id === 'flow-1' ? firstStatus : 204 };
    } });
    const first = await queue(service, layout === 'same batch' ? ['flow-1', 'flow-2'] : ['flow-1']);
    const second = layout === 'same batch' ? first : await queue(service, ['flow-2']);
    blockAudit();

    for (let tick = 0; tick < 3; tick++) {
      await expect(service.tick()).rejects.toThrow('isolated audit failure');
      // The failed promise is visible to the host only after the healthy selected row has run.
      expect(item('flow-1')).toMatchObject({ state: firstState, http_status: firstStatus, audit_pending: 1, audit_id: null });
      expect(item('flow-2')).toMatchObject({ state: 'received', http_status: 204, audit_pending: 0, audit_id: expect.any(Number) });
      expect(postedFlows()).toEqual(['flow-1', 'flow-2']);
      expect(auditCounts()).toEqual([{ flow_id: 'flow-2', n: 1 }]);
      expect((await service.get('wf', 'admin', first.batchId)).counts.auditPending).toBe(1);
      expect((await service.get('wf', 'admin', second.batchId)).counts.queued).toBe(0);
    }
    expect(failedAuditAttempts).toBe(3);

    f.raw.exec('DROP TRIGGER fail_audit');
    await service.close();
    const restarted = f.service();
    const peer = f.service();
    await Promise.all([restarted.tick(), peer.tick()]);
    await restarted.tick();
    expect(item('flow-1')).toMatchObject({ state: firstState, audit_pending: 0, audit_id: expect.any(Number) });
    expect(auditCounts()).toEqual([{ flow_id: 'flow-1', n: 1 }, { flow_id: 'flow-2', n: 1 }]);
    expect(postedFlows()).toEqual(['flow-1', 'flow-2']);
    expect((await restarted.get('wf', 'admin', first.batchId)).counts.auditPending).toBe(0);
  });

  it.each([204, 503])('does not let existing audit_pending (%i) block a later batch over repeated ticks', async firstStatus => {
    await addRun('flow-1');
    const service = f.service();
    await queue(service, ['flow-1']);
    blockAudit();
    f.outcome(firstStatus);
    await expect(service.tick()).rejects.toThrow('isolated audit failure');
    expect(postedFlows()).toEqual(['flow-1']);

    await addRun('flow-2');
    const second = await queue(service, ['flow-2']);
    f.outcome(204);
    for (let tick = 0; tick < 3; tick++) {
      await expect(service.tick()).rejects.toThrow('isolated audit failure');
      expect((await service.get('wf', 'admin', second.batchId)).items[0])
        .toMatchObject({ state: 'received', auditPending: false });
      expect(postedFlows()).toEqual(['flow-1', 'flow-2']);
    }
    expect(failedAuditAttempts).toBe(4);
    expect(item('flow-1')).toMatchObject({ state: firstStatus === 204 ? 'received' : 'unknown', audit_pending: 1 });
    f.raw.exec('DROP TRIGGER fail_audit');
    await service.tick();
    await service.tick();
    expect(auditCounts()).toEqual([{ flow_id: 'flow-1', n: 1 }, { flow_id: 'flow-2', n: 1 }]);
    expect(postedFlows()).toEqual(['flow-1', 'flow-2']);
  });

  it.each([
    { phase: 'claim', predicate: "OLD.state = 'queued' AND NEW.state = 'sending'", state: 'queued', sent: false },
    { phase: 'authority', predicate: "OLD.state = 'sending' AND NEW.state = 'sending'", state: 'sending', sent: false },
    { phase: 'settle', predicate: "NEW.state = 'received'", state: 'sending', sent: true },
  ])('propagates $phase persistence failure immediately even after an earlier audit failure', async ({ phase, predicate, state, sent }) => {
    for (const flowId of ['flow-1', 'flow-2', 'flow-3']) await addRun(flowId);
    const service = f.service();
    await queue(service, ['flow-1', 'flow-2', 'flow-3']);
    blockAudit();
    f.raw.exec(`CREATE TRIGGER fail_persistence BEFORE UPDATE ON workflow_callback_compensation_items
      WHEN NEW.flow_id = 'flow-2' AND ${predicate}
      BEGIN SELECT RAISE(ABORT, '${phase} storage failure'); END`);

    await expect(service.tick()).rejects.toThrow(`${phase} storage failure`);
    // The earlier audit error must not mask this fatal error or allow the third item to run.
    expect(failedAuditAttempts).toBe(1);
    expect(item('flow-1')).toMatchObject({ state: 'received', audit_pending: 1 });
    expect(item('flow-2')).toMatchObject({ state, audit_pending: 0 });
    expect(item('flow-3')).toMatchObject({ state: 'queued', audit_pending: 0 });
    expect(postedFlows()).toEqual(sent ? ['flow-1', 'flow-2'] : ['flow-1']);

    f.raw.exec('DROP TRIGGER fail_persistence; DROP TRIGGER fail_audit');
    await service.tick();
    f.advance(61_000);
    await service.tick();
    await service.tick();
    expect(item('flow-1')).toMatchObject({ state: 'received', audit_pending: 0 });
    expect(item('flow-2')).toMatchObject({ state: phase === 'claim' ? 'received' : 'unknown', audit_pending: 0 });
    expect(item('flow-3')).toMatchObject({ state: 'received', audit_pending: 0 });
    expect(postedFlows()).toEqual(phase === 'authority' ? ['flow-1', 'flow-3'] : ['flow-1', 'flow-2', 'flow-3']);
    expect(auditCounts()).toEqual([{ flow_id: 'flow-1', n: 1 }, { flow_id: 'flow-2', n: 1 }, { flow_id: 'flow-3', n: 1 }]);
  });

  it('keeps dispatching new batches over repeated ticks despite more than 100 persistently failing audits', async () => {
    const flows = Array.from({ length: 101 }, (_, index) => `audit-backlog-${index}`);
    for (const flowId of flows) await addRun(flowId);
    const service = f.service();
    // A batch is limited to 100 items, so establish the backlog across two batches/ticks.
    await queue(service, flows.slice(0, 100));
    await queue(service, flows.slice(100));
    blockAudit(true);
    for (const count of [100, 101]) {
      const failure = await service.tick().catch(error => error);
      expect(failure).toBeInstanceOf(AggregateError);
      expect(failure.errors).toHaveLength(count);
      expect(failure.errors.every((error: Error) => error.message === 'isolated audit failure')).toBe(true);
      expect(postedFlows()).toEqual(flows.slice(0, count));
    }

    const laterFlows: string[] = [];
    for (let tick = 0; tick < 3; tick++) {
      const flowId = `flow-later-${tick}`;
      await addRun(flowId);
      const later = await queue(service, [flowId]);
      laterFlows.push(flowId);
      const failure = await service.tick().catch(error => error);
      expect(failure).toBeInstanceOf(AggregateError);
      expect(failure.errors).toHaveLength(100);
      expect((await service.get('wf', 'admin', later.batchId)).items[0]).toMatchObject({ state: 'received', auditPending: false });
      expect(f.raw.prepare(`SELECT COUNT(*) AS n FROM workflow_callback_compensation_items
        WHERE audit_pending = 1 AND audit_id IS NULL AND state = 'received'`).get()).toEqual({ n: 101 });
      expect(auditCounts()).toEqual(laterFlows.map(flow_id => ({ flow_id, n: 1 })));
      expect(postedFlows()).toEqual([...flows, ...laterFlows]);
    }
    expect(failedAuditAttempts).toBe(501);

    f.raw.exec('DROP TRIGGER fail_audit');
    await service.tick(); // Only the oldest 100 audits fit in this tick; no HTTP is repeated.
    expect(f.raw.prepare('SELECT COUNT(*) AS n FROM workflow_callback_compensation_items WHERE audit_pending = 1').get())
      .toEqual({ n: 1 });
    await service.tick();
    expect(f.raw.prepare('SELECT COUNT(*) AS n FROM workflow_callback_compensation_items WHERE audit_pending = 1').get())
      .toEqual({ n: 0 });
    await service.tick();
    expect(postedFlows()).toEqual([...flows, ...laterFlows]);
    expect(auditCounts()).toHaveLength(104);
    expect(auditCounts().every(row => (row as { n: number }).n === 1)).toBe(true);
  });

  it('selects at most 100 queued and 100 audit rows independently while preserving environment, stop and sending filters', async () => {
    const environment = hash('pre');
    const otherEnvironment = hash('prod');
    const insertBatch = f.raw.prepare(`INSERT INTO workflow_callback_compensation_batches
      (batch_id, environment_key, scope_key, request_key, request_hash, metadata_json, stopped)
      VALUES (?, ?, 'test-scope', ?, 'test-hash', '{}', ?)`);
    insertBatch.run('active', environment, 'test-request-active', 0);
    insertBatch.run('stopped', environment, 'test-request-stopped', 1);
    insertBatch.run('other-environment', otherEnvironment, 'test-request-other', 0);
    const insertItem = f.raw.prepare(`INSERT INTO workflow_callback_compensation_items
      (item_id, batch_id, environment_key, delivery_key, flow_id, state, frozen_cipher, audit_pending, sending_until_ms)
      VALUES (?, ?, ?, ?, ?, ?, 'test-query-only-cipher', ?, ?)`);
    function add(id: string, state: string, pending: number, batch = 'active', env = environment, until: number | null = null) {
      insertItem.run(id, batch, env, id, id, state, pending, until);
    }
    add('stopped-audit', 'unknown', 1, 'stopped');
    const audits = Array.from({ length: 100 }, (_, index) => `pending-${index}`);
    for (const id of audits) add(id, 'received', 1);
    add('sending-live', 'sending', 0, 'active', environment, Date.now() + 60_000);
    add('sending-expired', 'sending', 0, 'active', environment, 0);
    const queued = Array.from({ length: 101 }, (_, index) => `queued-${index}`);
    for (const id of queued) add(id, 'queued', 0);
    add('stopped-queued', 'queued', 0, 'stopped');
    add('other-queued', 'queued', 0, 'other-environment', otherEnvironment);
    add('other-audit', 'received', 1, 'other-environment', otherEnvironment);

    const work = await new CallbackCompensationRepository(f.db).work(environment);

    expect(work).toHaveLength(200);
    expect(work.slice(0, 100).map(row => row.item_id)).toEqual(queued.slice(0, 100));
    expect(work.slice(100).map(row => row.item_id)).toEqual(['stopped-audit', ...audits.slice(0, 99)]);
    expect(work.every(row => row.environment_key === environment)).toBe(true);
    expect(work.slice(100).every(row => row.audit_pending === 1)).toBe(true);
    expect(postedFlows()).toEqual([]);
    // Neither selection claims rows nor recovers a lease; those safety checks remain in the worker.
    expect(item('sending-live')).toMatchObject({ state: 'sending', audit_pending: 0 });
    expect(item('sending-expired')).toMatchObject({ state: 'sending', audit_pending: 0 });
    expect(item('queued-100')).toMatchObject({ state: 'queued', audit_pending: 0 });
    expect(item('pending-99')).toMatchObject({ state: 'received', audit_pending: 1 });
  });
});
