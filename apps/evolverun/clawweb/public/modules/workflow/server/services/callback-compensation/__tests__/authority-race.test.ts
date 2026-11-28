import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { mysqlDialect, zdasDialect } from '@avernet/clawweb-shared/server/db/dialect';
import { CallbackCompensationRepository } from '../../../repositories/callback-compensation-repository.js';
import { CallbackCompensationSource, nodeFields } from '../../../repositories/callback-compensation-source.js';
import { fixture, type Fixture, NOW } from './fixture.js';

let f: Fixture;
beforeEach(async () => { f = fixture(); f.run(); f.config(); await f.authorize(); });
afterEach(async () => { vi.restoreAllMocks(); await f.close(); });

/** Pause at the actual exec boundary, after service preflight but before the
 * authorization UPDATE runs. This deliberately bypasses mtime changes: a frozen
 * authority guard must compare content, not rely on second-resolution clocks.
 */
function beforeAuthorization(sqlToRun?: string) {
  const actual = f.db.exec.bind(f.db);
  let hits = 0;
  vi.spyOn(f.db, 'exec').mockImplementation((sql, params) => {
    const authorization = /UPDATE\s+workflow_callback_compensation_items\s+SET\s+sending_until_ms\s*=\s*\?/i.test(sql)
      && /EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+workflow_callback_compensation_batches\b/i.test(sql);
    if (authorization) {
      hits++;
      if (hits === 1 && sqlToRun) f.raw.exec(sqlToRun);
    }
    return actual(sql, params);
  });
  return () => hits;
}

const nodeColumns = nodeFields.split(',').map(field => field.trim());
const replaceNode = `INSERT INTO node_executions (${nodeColumns.join(',')})
  SELECT id + 100,${nodeColumns.slice(1).join(',')} FROM node_executions WHERE flow_id='flow-1';
  DELETE FROM node_executions WHERE flow_id='flow-1' AND id < 100`;
const changes = [
  ['run reopened with new retry fence', "UPDATE flow_runs SET status='running',retry_epoch=retry_epoch+1 WHERE flow_id='flow-1'"],
  ['trial relation inserted', "INSERT INTO workflow_repair_release_records (record_id,record_kind) VALUES ('flow-1','trial_run')"],
  ['null phase becomes nonnull', "UPDATE flow_runs SET current_phase='P7' WHERE flow_id='flow-1'"],
  ['case-only receiver secret rotation', "UPDATE http_callback_configs SET secret=lower(secret) WHERE config_id='callback-string-id'"],
  ['result JSON trailing whitespace only', "UPDATE flow_runs SET result_json=result_json || ' ' WHERE flow_id='flow-1'"],
  ['result JSON case-only change', "UPDATE flow_runs SET result_json=replace(result_json,'SENSITIVE-RESULT','sensitive-result') WHERE flow_id='flow-1'"],
  ['node replaced at same count and timestamps', replaceNode],
  ['receiver secret rotated', "UPDATE http_callback_configs SET secret='dummy' WHERE config_id='callback-string-id'"],
  ['same-second run result', `UPDATE flow_runs SET result_json='{"result":"changed after preflight"}' WHERE flow_id='flow-1'`],
  ['same-second run input', `UPDATE flow_runs SET input_json='{"input":"changed after preflight"}' WHERE flow_id='flow-1'`],
  ['same-second run params', `UPDATE flow_runs SET params_json='{"params":"changed after preflight"}' WHERE flow_id='flow-1'`],
  ['same-second node input', `UPDATE node_executions SET input_json='{"input":"changed after preflight"}' WHERE flow_id='flow-1'`],
  ['same-second node output even when not included in payload', `UPDATE node_executions SET output_json='{"output":"changed after preflight"}' WHERE flow_id='flow-1'`],
] as const;

describe('atomic authority at the final pre-POST SQL boundary', () => {
  it.each(changes)('rejects %s before authorization SQL executes', async (_name, mutation) => {
    const { batch, service } = await f.queued();
    const runTime = f.raw.prepare('SELECT gmt_modified FROM flow_runs').get();
    const nodeTimes = f.raw.prepare('SELECT gmt_modified FROM node_executions ORDER BY gmt_modified').all();
    const configTime = f.raw.prepare('SELECT gmt_modified FROM http_callback_configs').get();
    const hits = beforeAuthorization(mutation);

    await service.tick();
    expect(hits()).toBe(1); // Fail if the deterministic barrier no longer intercepts authorizePost.
    expect(f.raw.prepare('SELECT gmt_modified FROM flow_runs').get()).toEqual(runTime);
    expect(f.raw.prepare('SELECT gmt_modified FROM node_executions ORDER BY gmt_modified').all()).toEqual(nodeTimes);
    expect(f.raw.prepare('SELECT gmt_modified FROM http_callback_configs').get()).toEqual(configTime);
    expect.soft(f.calls).toHaveLength(0);
    expect.soft((await service.get('wf', 'admin', batch.batchId)).items[0].state).not.toBe('received');

    await service.tick();
    expect.soft(f.calls).toHaveLength(0);
    expect.soft((await service.get('wf', 'admin', batch.batchId)).counts.received).toBe(0);
  });

  it('control: unchanged frozen authority passes the same barrier and sends once', async () => {
    const { batch, service } = await f.queued();
    const hits = beforeAuthorization();
    await service.tick();
    expect(hits()).toBe(1);
    expect(f.calls).toHaveLength(1);
    expect((await service.get('wf', 'admin', batch.batchId)).items[0].state).toBe('received');
    await service.tick();
    expect(f.calls).toHaveLength(1);
  });
});

describe.each([mysqlDialect, zdasDialect])('$name atomic authority query contract', dialect => {
  it('binds exact byte-sensitive null-safe run/config/node authority in the same authorization UPDATE', async () => {
    const source = new CallbackCompensationSource(f.db);
    const snapshot = (await source.snapshot('wf', 'flow-1'))!;
    const config = (await source.config('wf', 'callback-string-id'))!;
    const evidence = f.proofs.get('flow-1')!;
    const statements: { sql: string; params: unknown[] }[] = [];
    const database: IDatabase = {
      dbType: dialect.name === 'zdas' ? 'zdas' : 'mysql', dialect,
      query: f.db.query.bind(f.db), transaction: f.db.transaction.bind(f.db), close: async () => {},
      async exec(sql, params) { statements.push({ sql, params: params ?? [] }); return { affectedRows: 1 }; },
    };
    expect(await new CallbackCompensationRepository(database).authorizePost('item-1', NOW + 60_000,
      NOW, NOW + 60_001, { snapshot, config, evidence })).toBe(true);
    expect(statements).toHaveLength(1);
    const { sql, params } = statements[0];
    expect(sql).toMatch(/^UPDATE workflow_callback_compensation_items SET sending_until_ms = \?/);
    expect(sql).toContain("state = 'sending'");
    expect(sql).toContain('sending_until_ms = ? AND sending_until_ms > ?');
    expect(sql).toContain('b.stopped = 0');
    expect(params[0]).toBe(NOW + 60_001);
    expect(params.slice(2, 5)).toEqual(['item-1', NOW + 60_000, NOW]);

    const sensitive: [string, unknown][] = [
      ['c.secret', config.secret], ['c.url', config.url], ['c.notify_on', config.notify_on],
      ['r.result_json', snapshot.run.result_json], ['r.input_json', snapshot.run.input_json],
      ['r.params_json', snapshot.run.params_json], ['r.current_phase', null], ['r.state_json', null],
      ['r.status', 'succeeded'], ['r.env', 'pre'],
      ['n.input_json', snapshot.nodes[0].input_json], ['n.output_json', snapshot.nodes[0].output_json],
      ['n.error_text', null],
    ];
    for (const [column, value] of sensitive) {
      const exact = `CAST(${column} AS BINARY) <=> CAST(? AS BINARY)`;
      const offset = sql.indexOf(exact);
      expect(offset, `${column} must not use collation-dependent text equality`).toBeGreaterThanOrEqual(0);
      const parameter = (sql.slice(0, offset).match(/\?/g) ?? []).length;
      expect(params[parameter]).toBe(value);
      const escaped = column.replace('.', '\\.');
      expect(sql).not.toMatch(new RegExp(`\\b${escaped}\\s*(?:=|<=>|LIKE)\\s*\\?`, 'i'));
    }
    expect(sql).toContain('(SELECT COUNT(*) FROM node_executions n WHERE n.flow_id = ?) = ?');
    expect(sql).toContain('NOT EXISTS (SELECT 1 FROM workflow_repair_release_records');
    expect(sql).toContain('NOT EXISTS (SELECT 1 FROM http_callback_logs');
    expect(params).toContain(snapshot.nodes[0].id); // Existing row identity, not just equal node count.
    expect(params).toContain(evidence.configIds[0]);
    expect(params).toContain(evidence.urls[0]);
  });
});

async function seedNodes(count: number) {
  const fields = nodeColumns.filter(field => field !== 'id');
  const first = f.raw.prepare(`SELECT ${fields.join(',')} FROM node_executions WHERE flow_id='flow-1'`).get() as Record<string, unknown>;
  const insert = f.raw.prepare(`INSERT INTO node_executions (${fields.join(',')}) VALUES (${fields.map(() => '?').join(',')})`);
  f.raw.transaction(() => {
    for (let i = 2; i <= count; i++) insert.run(...fields.map(field => field === 'node_id' ? `node-${i}` : first[field]));
    f.raw.prepare("UPDATE flow_runs SET node_count=?,succeeded_count=? WHERE flow_id='flow-1'").run(count, count);
  })();
  await f.authorize(); // Normal snapshot hash and trusted fixture evidence, no frozen data overrides.
}

function expectEveryNodeInAuthorization() {
  const statements = vi.mocked(f.db.exec).mock.calls.filter(([sql]) =>
    /UPDATE\s+workflow_callback_compensation_items\s+SET\s+sending_until_ms\s*=\s*\?/i.test(sql));
  expect(statements).toHaveLength(1);
  const [sql, params] = statements[0];
  expect(sql.match(/EXISTS \(SELECT 1 FROM node_executions n WHERE/g)).toHaveLength(950);
  expect(params!.length).toBeGreaterThanOrEqual(950 * nodeColumns.length);
  expect(params!.length).toBeLessThan(32766); // This expression-depth regression is below the separate bind limit.
}

describe('950-node atomic authority service path', () => {
  it('unchanged complete authority sends exactly once and becomes received', async () => {
    await seedNodes(950);
    const { batch, service } = await f.queued();
    const hits = beforeAuthorization();
    await service.tick();
    expect(hits()).toBe(1);
    expectEveryNodeInAuthorization();
    expect(f.calls).toHaveLength(1);
    expect(JSON.parse(f.calls[0].rawBody).ext_info.node_executions).toHaveLength(950);
    expect((await service.get('wf', 'admin', batch.batchId)).items[0].state).toBe('received');
    await service.tick();
    expect(f.calls).toHaveLength(1);
  });

  it('changing the last of 950 nodes immediately before CAS never sends or becomes received', async () => {
    await seedNodes(950);
    const { batch, service } = await f.queued();
    const hits = beforeAuthorization(`UPDATE node_executions SET output_json='{"output":"LAST-OF-950-CHANGED"}'
      WHERE flow_id='flow-1' AND node_id='node-950'`);
    await service.tick();
    expect(hits()).toBe(1);
    expectEveryNodeInAuthorization();
    expect(f.raw.prepare("SELECT COUNT(*) n FROM node_executions WHERE flow_id='flow-1'").get()).toEqual({ n: 950 });
    expect(f.raw.prepare("SELECT output_json FROM node_executions WHERE node_id='node-950'").get())
      .toEqual({ output_json: '{"output":"LAST-OF-950-CHANGED"}' });
    expect(f.calls).toHaveLength(0);
    expect((await service.get('wf', 'admin', batch.batchId)).counts.received).toBe(0);
    await service.tick();
    expect(f.calls).toHaveLength(0);
    expect((await service.get('wf', 'admin', batch.batchId)).items[0].state).not.toBe('received');
  });
});

describe('1300-node authority bind budget', () => {
  it('stays review-only before preview can reserve a delivery and rejects create without sending', async () => {
    await seedNodes(1300);
    const service = f.service();
    const expectNoReservation = () => {
      for (const table of ['workflow_callback_compensation_items', 'workflow_callback_compensation_batches']) {
        expect(f.raw.prepare(`SELECT COUNT(*) n FROM ${table}`).get()).toEqual({ n: 0 });
      }
      expect(f.calls).toHaveLength(0);
    };

    const preview = await service.preview('wf', 'admin', { configId: 'callback-string-id', flowIds: ['flow-1'] });
    expect(preview.items).toHaveLength(1);
    expect(preview.items[0]).toMatchObject({
      flowId: 'flow-1', status: 'review', reasons: ['AUTHORITY_SIZE_REQUIRES_REVIEW'],
    });
    expect(preview.summary).toEqual({ eligible: 0, delivered: 0, review: 1, ineligible: 0 });
    expectNoReservation();

    await expect(service.create('wf', 'admin', {
      previewId: preview.previewId, itemIds: [preview.items[0].itemId],
      reason: 'Reviewed notification recovery', requestKey: 'oversized-authority-1300',
    })).rejects.toMatchObject({ code: 'SELECTION_NOT_ELIGIBLE' });
    expectNoReservation();

    await service.tick();
    expectNoReservation();
  });
});
