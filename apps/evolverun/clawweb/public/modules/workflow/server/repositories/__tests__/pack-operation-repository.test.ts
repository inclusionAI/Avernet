// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { Worker } from 'node:worker_threads';
import { SqliteDatabase, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { packOperationDdl } from '@avernet/clawweb-shared/server/schema-pack-operations';
import { mysqlDialect, sqliteDialect, zdasDialect } from '@avernet/clawweb-shared/server/db/dialect';
import { PackOperationRepository } from '../pack-operation-repository.js';

const input = { requestId: '63b77758-ec30-4c8d-aab0-76746be07a46', workflowId: 'wf', actorId: 'actor', kind: 'save' as const, inputHash: 'hash' };
const payload = JSON.stringify({ commit: 'exact-object', bundle: { bytes: 'base64' }, candidateYaml: 'id: wf\n' });
const result = '{"version":8}';
const legacyDdl = `CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT COLLATE NOCASE NOT NULL UNIQUE,
  spec_json TEXT NOT NULL, pack_id TEXT, version INTEGER, owner_id TEXT);
  INSERT INTO workflow_specs VALUES (1, 'wf', '{"id":"wf"}', 'pack', 7, 'owner');
  INSERT INTO workflow_specs VALUES (2, 'other', '{"id":"other"}', 'pack', NULL, NULL);
  CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, workflow_id TEXT, pack_id TEXT,
    deploy_number INTEGER, version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER,
    spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER);
  INSERT INTO workflow_deploy_history VALUES (11, 'wf', 'pack', 7, 7, 'deploy/wf/#7', 'deploy', NULL,
    '{"id":"wf"}', NULL, 'bot', 'owner', 1);`;
let db: IDatabase;
let repo: PackOperationRepository;
let directory: string;
let filename: string;
beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'clawweb-pack-operation-'));
  filename = join(directory, 'operations.db');
  const raw = new Database(filename);
  raw.exec(legacyDdl);
  for (const ddl of packOperationDdl) raw.exec(sqliteDialect.renderDdl(ddl));
  db = new SqliteDatabase(raw);
  repo = new PackOperationRepository(db);
});
afterEach(async () => { await db.close(); rmSync(directory, { recursive: true, force: true }); });
async function ready() { await repo.begin(input); await repo.prepare(input.requestId, payload, 1); }
async function pushing() { await ready(); await repo.markPushing(input.requestId, 1); }

// Worker threads give synchronous SQLite separate event loops and connections,
// so concurrency is exercised against actual database locks, not a mock mutex.
async function contender(requestId: string) {
  const require = createRequire(import.meta.url);
  const worker = new Worker(`
    const { parentPort, workerData } = require('node:worker_threads');
    const { readFileSync, existsSync } = require('node:fs');
    const path = require('node:path');
    const localRequire = require('node:module').createRequire(workerData.anchor);
    const ts = localRequire('typescript');
    function load(filename) {
      const module = { exports: {} };
      const code = ts.transpileModule(readFileSync(filename, 'utf8'), {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
      }).outputText;
      new Function('require', 'module', 'exports', code)((id) => {
        if (id.startsWith('.')) {
          const target = path.resolve(path.dirname(filename), id.replace(/\\.js$/, '.ts'));
          if (existsSync(target)) return load(target);
        }
        return localRequire(id);
      }, module, module.exports);
      return module.exports;
    }
    const Database = localRequire('better-sqlite3');
    const { SqliteDatabase } = load(workerData.sqlite);
    const { PackOperationRepository } = load(workerData.repository);
    const db = new SqliteDatabase(new Database(workerData.filename));
    parentPort.once('message', async () => {
      try { parentPort.postMessage({ ok: true, value: await new PackOperationRepository(db).begin(workerData.input) }); }
      catch (error) { parentPort.postMessage({ ok: false, status: error.status, message: error.message }); }
      finally { await db.close(); }
    });
    parentPort.postMessage('ready');
  `, { eval: true, workerData: {
    anchor: require.resolve('typescript'), filename, input: { ...input, requestId },
    sqlite: fileURLToPath(new URL('../../../../../shared/server/db/sqlite-database.ts', import.meta.url)),
    repository: fileURLToPath(new URL('../pack-operation-repository.ts', import.meta.url)),
  } });
  await new Promise<void>((resolve, reject) => { worker.once('message', () => resolve()); worker.once('error', reject); });
  return {
    run: () => new Promise<any>((resolve, reject) => {
      worker.once('message', resolve); worker.once('error', reject); worker.postMessage('go');
    }),
    stop: () => worker.terminate(),
  };
}

describe('ClawWeb pack operation journal', () => {
  it('begins once and captures workflow and all active core fields', async () => {
    const first = await repo.begin(input);
    expect(first.created).toBe(true);
    expect(first.operation).toMatchObject({ ...input, phase: 'preparing', payloadJson: null, resultJson: null,
      baseline: { workflow: { id: 1, spec_json: '{"id":"wf"}', pack_id: 'pack', version: 7, owner_id: 'owner' },
        active: [{ id: 11, workflow_id: 'wf', deploy_number: 7, version: 7, is_active: 1 }] } });
    expect(await repo.begin(input)).toEqual({ operation: first.operation, created: false });
    expect(await repo.get('absent')).toBeNull();
  });
  it.each([{ inputHash: 'different' }, { actorId: 'different' }, { workflowId: 'other' }, { kind: 'deploy' as const }])(
    'rejects request identity reuse with %j', async change => {
      await repo.begin(input);
      await expect(repo.begin({ ...input, ...change })).rejects.toMatchObject({ status: 409 });
    });
  it('serializes different requests for one workflow but permits another workflow', async () => {
    const outcomes = await Promise.allSettled([repo.begin(input), repo.begin({ ...input, requestId: 'second' })]);
    expect(outcomes.filter(o => o.status === 'fulfilled')).toHaveLength(1);
    expect(outcomes.find(o => o.status === 'rejected')).toMatchObject({ reason: { status: 409 } });
    expect((await repo.begin({ ...input, requestId: 'other', workflowId: 'other' })).created).toBe(true);
  });
  it('returns created once for simultaneous duplicate requests', async () => {
    const results = await Promise.all([repo.begin(input), repo.begin(input)]);
    expect(results.map(o => o.created).sort()).toEqual([false, true]);
  });
  it.each([false, true])('serializes independent SQLite connections (same request: %s)', async same => {
    const a = await contender(input.requestId);
    const b = await contender(same ? input.requestId : 'second-process');
    try {
      const outcomes = await Promise.all([a.run(), b.run()]);
      expect(outcomes.filter(o => o.ok && o.value.created)).toHaveLength(1);
      if (same) expect(outcomes.filter(o => o.ok && !o.value.created)).toHaveLength(1);
      else expect(outcomes.filter(o => !o.ok && o.status === 409)).toHaveLength(1);
    } finally { await Promise.all([a.stop(), b.stop()]); }
  });
  it('stores exact prepared JSON and rejects a different payload', async () => {
    await ready();
    expect((await repo.prepare(input.requestId, payload, 1)).payloadJson).toBe(payload);
    await expect(repo.prepare(input.requestId, '{"bundle":"different"}', 1)).rejects.toMatchObject({ status: 409 });
  });
  it('allows one push claimant and never steals an old unknown lock', async () => {
    await ready();
    const claims = await Promise.all([repo.markPushing(input.requestId, 1), repo.markPushing(input.requestId, 1)]);
    expect(claims.map(c => c.claimed).sort()).toEqual([false, true]);
    await repo.markUnknown(input.requestId, '{"reason":"network"}', 1);
    expect((await repo.markPushing(input.requestId, 1)).claimed).toBe(false);
    await expect(repo.failBeforePush(input.requestId, '{}', 1)).rejects.toMatchObject({ status: 409 });
    await db.exec('UPDATE clawweb_pack_operations SET updated_at_ms = 0', []);
    await expect(repo.begin({ ...input, requestId: 'new' })).rejects.toMatchObject({ status: 409 });
  });
  it.each(['prepared', 'pushing', 'unknown'] as const)('retains %s data and lock after reopen', async phase => {
    await ready();
    if (phase !== 'prepared') await repo.markPushing(input.requestId, 1);
    if (phase === 'unknown') await repo.markUnknown(input.requestId, '{"network":"lost"}', 1);
    const expected = await repo.get(input.requestId);
    await db.close(); db = new SqliteDatabase(new Database(filename)); repo = new PackOperationRepository(db);
    expect(await repo.get(input.requestId)).toEqual(expected);
    await expect(repo.begin({ ...input, requestId: 'after-restart' })).rejects.toMatchObject({ status: 409 });
  });
  it('rolls business writes and completion back when the callback fails', async () => {
    await pushing();
    const failure = new Error('business DB write failed');
    await expect(repo.complete(input.requestId, result, async tx => {
      await tx.exec('UPDATE workflow_specs SET version = 8 WHERE id = 1', []); throw failure;
    }, 1)).rejects.toBe(failure);
    expect((await db.query<{ version: number }>('SELECT version FROM workflow_specs WHERE id = 1'))[0].version).toBe(7);
    expect((await repo.get(input.requestId))?.phase).toBe('pushing');
    await expect(repo.begin({ ...input, requestId: 'new' })).rejects.toMatchObject({ status: 409 });
  });
  it('rolls callback writes back when the final journal update fails', async () => {
    await pushing();
    await db.exec(`CREATE TRIGGER fail_completion BEFORE UPDATE ON clawweb_pack_operations
      WHEN NEW.phase = 'completed' BEGIN SELECT RAISE(ABORT, 'journal write failed'); END;`);
    await expect(repo.complete(input.requestId, result, async tx => {
      await tx.exec('UPDATE workflow_specs SET version = 8 WHERE id = 1', []);
    }, 1)).rejects.toThrow('journal write failed');
    expect((await db.query<{ version: number }>('SELECT version FROM workflow_specs WHERE id = 1'))[0].version).toBe(7);
    expect((await repo.get(input.requestId))?.phase).toBe('pushing');
  });
  it('completes exactly once and releases the workflow lock atomically', async () => {
    await pushing();
    const write = vi.fn(async (tx: IDatabase) => { await tx.exec('UPDATE workflow_specs SET version = 8 WHERE id = 1', []); });
    const done = await repo.complete(input.requestId, result, write, 1);
    expect(done.phase).toBe('completed');
    expect(await repo.complete(input.requestId, result, write, 1)).toEqual(done);
    expect(write).toHaveBeenCalledTimes(1);
    await expect(repo.complete(input.requestId, '{}', write, 1)).rejects.toMatchObject({ status: 409 });
    expect((await repo.begin({ ...input, requestId: 'next' })).operation.baseline.workflow.version).toBe(8);
    expect((await repo.begin(input)).created).toBe(false);
  });
  it.each([
    'UPDATE workflow_specs SET id = 99 WHERE id = 1',
    "UPDATE workflow_specs SET spec_json = '{}' WHERE id = 1",
    "UPDATE workflow_specs SET pack_id = 'changed' WHERE id = 1",
    'UPDATE workflow_specs SET version = 9 WHERE id = 1',
    "UPDATE workflow_specs SET owner_id = 'changed' WHERE id = 1",
    'UPDATE workflow_deploy_history SET id = 99 WHERE id = 11',
    "UPDATE workflow_deploy_history SET spec_json = '{}' WHERE id = 11",
    'UPDATE workflow_deploy_history SET is_active = 0 WHERE id = 11',
    "UPDATE workflow_deploy_history SET owner_id = 'changed' WHERE id = 11",
    'DELETE FROM workflow_specs WHERE id = 1',
  ])('fences completion when the baseline changes: %s', async sql => {
    await pushing(); await db.exec(sql, []);
    const write = vi.fn();
    await expect(repo.complete(input.requestId, result, write, 1)).rejects.toMatchObject({ status: 409 });
    expect(write).not.toHaveBeenCalled();
    expect((await repo.get(input.requestId))?.phase).toBe('pushing');
  });
  it('checks the baseline both at prepare and before claiming push', async () => {
    await repo.begin(input); await db.exec('UPDATE workflow_specs SET version = 8 WHERE id = 1', []);
    await expect(repo.prepare(input.requestId, payload, 1)).rejects.toMatchObject({ status: 409 });
    await db.exec('UPDATE workflow_specs SET version = 7 WHERE id = 1', []);
    await repo.prepare(input.requestId, payload, 1); await db.exec('UPDATE workflow_specs SET version = 8 WHERE id = 1', []);
    await expect(repo.markPushing(input.requestId, 1)).rejects.toMatchObject({ status: 409 });
  });
  it('captures every active row and detects membership changes', async () => {
    await db.exec(`INSERT INTO workflow_deploy_history SELECT 12, workflow_id, pack_id, 6, 6, tag_name,
      action, from_deploy_number, spec_json, note, bot_id, owner_id, 1 FROM workflow_deploy_history WHERE id = 11`, []);
    expect((await repo.begin(input)).operation.baseline.active.map(row => row.id)).toEqual([11, 12]);
    await repo.prepare(input.requestId, payload, 1); await repo.markPushing(input.requestId, 1);
    await db.exec('UPDATE workflow_deploy_history SET is_active = 0 WHERE id = 12', []);
    await expect(repo.complete(input.requestId, result, vi.fn(), 1)).rejects.toMatchObject({ status: 409 });
  });
  it('supports no active deployment and does not invent pending history', async () => {
    await db.exec('DELETE FROM workflow_deploy_history', []);
    expect((await repo.begin(input)).operation.baseline.active).toEqual([]);
    await repo.prepare(input.requestId, payload, 1); await repo.markPushing(input.requestId, 1);
    await repo.markUnknown(input.requestId, '{}', 1); await repo.complete(input.requestId, result, async () => {}, 1);
    expect(await db.query('SELECT * FROM workflow_deploy_history')).toEqual([]);
  });
  it('only writes formal history inside the committed business callback', async () => {
    const before = await db.query('SELECT * FROM workflow_deploy_history');
    await ready(); expect(await db.query('SELECT * FROM workflow_deploy_history')).toEqual(before);
    await repo.markPushing(input.requestId, 1); await repo.markUnknown(input.requestId, '{}', 1);
    expect(await db.query('SELECT * FROM workflow_deploy_history')).toEqual(before);
    await repo.complete(input.requestId, result, async tx => {
      await tx.exec(`INSERT INTO workflow_deploy_history SELECT 12, workflow_id, pack_id, 8, 8, 'deploy/wf/#8',
        'deploy', NULL, spec_json, NULL, bot_id, owner_id, 0 FROM workflow_deploy_history WHERE id = 11`, []);
    }, 1);
    expect(await db.query("SELECT * FROM workflow_deploy_history WHERE action = 'pending'")).toEqual([]);
    expect(await db.query('SELECT * FROM workflow_deploy_history')).toHaveLength(2);
  });
  it.each(['preparing', 'prepared'])('releases a confirmed pre-push failure from %s', async phase => {
    await repo.begin(input); if (phase === 'prepared') await repo.prepare(input.requestId, payload, 1);
    const failed = await repo.failBeforePush(input.requestId, '{"reason":"validation"}', 1);
    expect(failed.phase).toBe('failed');
    expect(await repo.failBeforePush(input.requestId, '{"reason":"validation"}', 1)).toEqual(failed);
    expect((await repo.begin({ ...input, requestId: 'retry' })).created).toBe(true);
  });
  it('rejects invalid transitions, malformed JSON and absent workflows', async () => {
    await expect(repo.begin({ ...input, workflowId: 'missing' })).rejects.toMatchObject({ status: 404 });
    await expect(repo.prepare('missing', payload, 1)).rejects.toMatchObject({ status: 404 });
    await repo.begin(input);
    await expect(repo.prepare(input.requestId, 'invalid-json', 1)).rejects.toThrow();
    await expect(repo.markPushing(input.requestId, 1)).rejects.toMatchObject({ status: 409 });
    await expect(repo.complete(input.requestId, result, vi.fn(), 1)).rejects.toMatchObject({ status: 409 });
    await expect(repo.markUnknown(input.requestId, '{}', 1)).rejects.toMatchObject({ status: 409 });
    await repo.prepare(input.requestId, payload, 1); await repo.markPushing(input.requestId, 1);
    await expect(repo.failBeforePush(input.requestId, '{}', 1)).rejects.toMatchObject({ status: 409 });
  });
  it.each([mysqlDialect, zdasDialect])('locks actual workflow and active rows on $name (SQL contract only)', async dialect => {
    const statements: string[] = [];
    const wrap = (target: IDatabase): IDatabase => ({
      dbType: dialect.name, dialect,
      query: async (sql, params) => { statements.push(sql); return target.query(sql.replace(/ FOR UPDATE\b/g, ''), params); },
      exec: (sql, params) => target.exec(sql, params),
      transaction: fn => target.transaction(tx => fn(wrap(tx))), close: async () => {},
    });
    const remote = new PackOperationRepository(wrap(db));
    await remote.begin(input); await remote.prepare(input.requestId, payload, 1); await remote.markPushing(input.requestId, 1);
    await remote.complete(input.requestId, result, async () => {}, 1);
    const workflows = statements.filter(sql => /FROM workflow_specs/.test(sql));
    expect(workflows.length).toBeGreaterThanOrEqual(4);
    expect(workflows.every(sql => /FOR UPDATE/.test(sql))).toBe(true);
    expect(statements.filter(sql => /FROM workflow_deploy_history/.test(sql)).every(sql => /FOR UPDATE/.test(sql))).toBe(true);
  });
  it('recognizes only completed save provenance at payload.git.commit for this workflow instance', async () => {
    const commit = 'a'.repeat(40);
    const savedPayload = JSON.stringify({ git: { commit }, specJson: '{}', title: 'saved', packId: 'pack', remoteHash: 'remote' });
    await repo.begin(input); await repo.prepare(input.requestId, savedPayload, 1);
    expect(await repo.findSavedCandidate('wf', commit)).toBe(false);
    await repo.markPushing(input.requestId, 1);
    await repo.markUnknown(input.requestId, '{}', 1);
    expect(await repo.findSavedCandidate('wf', commit)).toBe(false);
    await repo.complete(input.requestId, result, async () => {}, 1);
    expect(await repo.findSavedCandidate('wf', commit)).toBe(true);
    expect(await repo.findSavedCandidate('other', commit)).toBe(false);
    expect(await repo.findSavedCandidate('wf', 'a')).toBe(false);
    expect(await repo.findSavedCandidate('wf', 'b'.repeat(40))).toBe(false);
    await db.exec('UPDATE workflow_specs SET id = 99 WHERE id = 1', []);
    expect(await repo.findSavedCandidate('wf', commit)).toBe(false);
    await db.exec('DELETE FROM workflow_specs WHERE id = 99', []);
    expect(await repo.findSavedCandidate('wf', commit)).toBe(false);
  });
  it.each([
    { kind: 'deploy' as const, body: { git: { commit: 'candidate' } } },
    { kind: 'save' as const, body: { git: { commit: 'candidate-other' }, note: 'candidate' } },
    { kind: 'save' as const, body: { commit: 'candidate', git: null } },
  ])('does not infer saved provenance from $kind or substring matches', async ({ kind, body }) => {
    await repo.begin({ ...input, kind }); await repo.prepare(input.requestId, JSON.stringify(body), 1);
    await repo.markPushing(input.requestId, 1); await repo.complete(input.requestId, result, async () => {}, 1);
    expect(await repo.findSavedCandidate('wf', 'candidate')).toBe(false);
  });
  it('does not replay a completed request as success after workflow deletion or recreation', async () => {
    await pushing(); await repo.complete(input.requestId, result, async () => {}, 1);
    await db.exec('DELETE FROM workflow_specs WHERE id = 1', []);
    await expect(repo.begin(input)).rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
    await db.exec("INSERT INTO workflow_specs VALUES (99, 'wf', '{}', 'pack', 7, 'owner')", []);
    await expect(repo.begin(input)).rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
    expect((await repo.begin({ ...input, requestId: 'new-instance' })).operation.baseline.workflow.id).toBe(99);
    expect((await repo.get(input.requestId))?.phase).toBe('completed'); // audit remains readable
  });
  it('preserves existing 255-character workflow IDs and locks their exact workflow row', async () => {
    const workflowId = 'w'.repeat(255);
    await db.exec('UPDATE workflow_specs SET workflow_id = ? WHERE id = 1', [workflowId]);
    const operation = await repo.begin({ ...input, workflowId });
    expect(operation.operation.workflowId).toBe(workflowId);
    await expect(repo.begin({ ...input, workflowId, requestId: 'another' })).rejects.toMatchObject({ status: 409 });
    await repo.prepare(input.requestId, payload, 1); await repo.markPushing(input.requestId, 1);
    await repo.complete(input.requestId, result, async () => {}, 1);
    expect((await repo.begin({ ...input, workflowId, requestId: 'next' })).created).toBe(true);
  });
  it('does not release an unresolved workflow-name lock after its row is deleted and recreated', async () => {
    await pushing();
    await db.exec("DELETE FROM workflow_specs WHERE id = 1; INSERT INTO workflow_specs VALUES (99, 'wf', '{}', 'pack', 7, 'owner');");
    await expect(repo.begin({ ...input, requestId: 'replacement' })).rejects.toMatchObject({ status: 409 });
  });
  it('rejects case-insensitive workflow matches rather than using a different Git identity', async () => {
    await expect(repo.begin({ ...input, workflowId: 'WF' })).rejects.toMatchObject({ status: 404 });
    await expect(repo.findSavedCandidate('WF', 'commit')).rejects.toMatchObject({ status: 404 });
    await ready(); await db.exec("UPDATE workflow_specs SET workflow_id = 'WF' WHERE id = 1", []);
    await expect(repo.markPushing(input.requestId, 1)).rejects.toMatchObject({ status: 404 });
    expect((await repo.get(input.requestId))?.phase).toBe('prepared');
  });
  it.each(['prepared', 'pushing', 'unknown'] as const)('preflights the full baseline before recovering %s Git operations', async phase => {
    await ready();
    if (phase !== 'prepared') await repo.markPushing(input.requestId, 1);
    if (phase === 'unknown') await repo.markUnknown(input.requestId, '{}', 1);
    expect((await repo.preflightRecovery(input.requestId, 1)).phase).toBe(phase);
    await db.exec('UPDATE workflow_specs SET version = 8 WHERE id = 1', []);
    await expect(repo.preflightRecovery(input.requestId, 1)).rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
    await db.exec('UPDATE workflow_specs SET version = 7 WHERE id = 1', []);
    await db.exec('UPDATE workflow_deploy_history SET is_active = 0 WHERE id = 11', []);
    await expect(repo.preflightRecovery(input.requestId, 1)).rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
  });
  it('preflight returns a completed receipt but rejects preparing/failed operations', async () => {
    await repo.begin(input);
    await expect(repo.preflightRecovery(input.requestId, 1)).rejects.toMatchObject({ status: 409 });
    await repo.failBeforePush(input.requestId, '{}', 1);
    await expect(repo.preflightRecovery(input.requestId, 1)).rejects.toMatchObject({ status: 409 });
    const next = { ...input, requestId: 'complete' };
    await repo.begin(next); await repo.prepare(next.requestId, payload, 1); await repo.markPushing(next.requestId, 1);
    const done = await repo.complete(next.requestId, result, async tx => {
      await tx.exec('UPDATE workflow_specs SET version = 8 WHERE id = 1', []);
    }, 1);
    expect(await repo.preflightRecovery(next.requestId, 1)).toEqual(done);
  });
  it('restarts a confirmed failed request, clears its old payload, and lets it complete', async () => {
    await ready(); await repo.failBeforePush(input.requestId, '{"reason":"prepush"}', 1);
    const restarted = await repo.restartFailed(input, 1);
    expect(restarted).toMatchObject({ created: true, operation: { phase: 'preparing', payloadJson: null, resultJson: null, errorJson: null } });
    expect(restarted.operation.baseline.workflow.id).toBe(1);
    await repo.prepare(input.requestId, '{"git":{"commit":"retry"}}', restarted.operation.attempt); await repo.markPushing(input.requestId, restarted.operation.attempt);
    expect((await repo.complete(input.requestId, result, async () => {}, restarted.operation.attempt)).phase).toBe('completed');
  });
  it.each([{ actorId: 'other' }, { inputHash: 'other' }, { workflowId: 'other' }, { kind: 'deploy' as const }])(
    'never restarts a failed request with changed identity %j', async change => {
      await repo.begin(input); await repo.failBeforePush(input.requestId, '{}', 1);
      await expect(repo.restartFailed({ ...input, ...change }, 1)).rejects.toMatchObject({ status: 409 });
      expect((await repo.get(input.requestId))?.phase).toBe('failed');
    });
  it.each([
    'UPDATE workflow_specs SET id = 99 WHERE id = 1',
    'UPDATE workflow_specs SET version = 9 WHERE id = 1',
    "UPDATE workflow_specs SET owner_id = 'new-owner' WHERE id = 1",
    'UPDATE workflow_deploy_history SET is_active = 0 WHERE id = 11',
  ])('cannot restart a failure after baseline drift: %s', async sql => {
    await repo.begin(input); await repo.failBeforePush(input.requestId, '{}', 1); await db.exec(sql, []);
    await expect(repo.restartFailed(input, 1)).rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
    expect((await repo.get(input.requestId))?.phase).toBe('failed');
  });
  it('cannot restart a failed request while another request owns its workflow lock', async () => {
    await repo.begin(input); await repo.failBeforePush(input.requestId, '{}', 1);
    await repo.begin({ ...input, requestId: 'other-request' });
    await expect(repo.restartFailed(input, 1)).rejects.toMatchObject({ status: 409, code: 'WORKFLOW_BUSY' });
    expect((await repo.get(input.requestId))?.phase).toBe('failed');
  });
  it.each(['prepared', 'pushing', 'unknown', 'completed'] as const)('cannot reset %s with restartFailed', async phase => {
    await ready();
    if (phase !== 'prepared') await repo.markPushing(input.requestId, 1);
    if (phase === 'unknown') await repo.markUnknown(input.requestId, '{}', 1);
    if (phase === 'completed') await repo.complete(input.requestId, result, async () => {}, 1);
    const before = await repo.get(input.requestId);
    await expect(repo.restartFailed(input, 1)).rejects.toMatchObject({ status: 409 });
    expect(await repo.get(input.requestId)).toEqual(before);
  });
  it('allows exactly one simultaneous failed-operation restart', async () => {
    await repo.begin(input); await repo.failBeforePush(input.requestId, '{}', 1);
    const retries = await Promise.allSettled([repo.restartFailed(input, 1), repo.restartFailed(input, 1)]);
    expect(retries.filter(item => item.status === 'fulfilled')).toHaveLength(1);
    expect(retries.find(item => item.status === 'rejected')).toMatchObject({ reason: { status: 409 } });
  });
  it('fences a cached P1 recovery after fail/restart/prepare P2 under the same request ID (real SQLite ABA)', async () => {
    const first = await repo.begin(input);
    expect(first.operation.attempt).toBe(1);
    const p1 = JSON.stringify({ git: { commit: 'P1' }, specJson: '{"title":"P1"}' });
    const p2 = JSON.stringify({ git: { commit: 'P2' }, specJson: '{"title":"P2"}' });
    await repo.prepare(input.requestId, p1, first.operation.attempt);
    const cached = await new PackOperationRepository(db).preflightRecovery(input.requestId, first.operation.attempt);
    await repo.failBeforePush(input.requestId, '{}', first.operation.attempt);
    const restarted = await repo.restartFailed(input, first.operation.attempt);
    expect(restarted.operation.attempt).toBe(2);
    await repo.prepare(input.requestId, p2, restarted.operation.attempt);
    // Restart the connection too: the generation is durable, not process state.
    await db.close(); db = new SqliteDatabase(new Database(filename)); repo = new PackOperationRepository(db);
    const before = await repo.get(input.requestId);
    const oldBusiness = vi.fn(async (tx: IDatabase) => { await tx.exec("UPDATE workflow_specs SET spec_json = 'P1' WHERE id = 1", []); });
    const staleCalls = [
      () => repo.prepare(input.requestId, cached.payloadJson!, cached.attempt),
      () => repo.prepare(input.requestId, p2, cached.attempt), // identical payload must not bypass generation check
      () => repo.markPushing(input.requestId, cached.attempt),
      () => repo.complete(input.requestId, '{"commit":"P1"}', oldBusiness, cached.attempt),
      () => repo.failBeforePush(input.requestId, '{}', cached.attempt),
      () => repo.markUnknown(input.requestId, '{}', cached.attempt),
      () => repo.preflightRecovery(input.requestId, cached.attempt),
      () => repo.restartFailed(input, cached.attempt),
    ];
    for (const invoke of staleCalls) {
      await expect(invoke()).rejects.toMatchObject({ status: 409, code: 'ATTEMPT_STALE' });
      expect(await repo.get(input.requestId)).toEqual(before);
    }
    expect(oldBusiness).not.toHaveBeenCalled();
    expect((await repo.markPushing(input.requestId, restarted.operation.attempt)).claimed).toBe(true);
    await expect(repo.complete(input.requestId, '{"commit":"P1"}', oldBusiness, cached.attempt))
      .rejects.toMatchObject({ status: 409, code: 'ATTEMPT_STALE' });
    const done = await repo.complete(input.requestId, '{"commit":"P2"}', async (tx, current) => {
      expect(current.attempt).toBe(2);
      expect(current.payloadJson).toBe(p2);
      await tx.exec('UPDATE workflow_specs SET spec_json = ? WHERE id = 1', [JSON.parse(current.payloadJson!).specJson]);
    }, restarted.operation.attempt);
    expect(done).toMatchObject({ phase: 'completed', attempt: 2, payloadJson: p2, resultJson: '{"commit":"P2"}' });
    expect(await db.query('SELECT spec_json FROM workflow_specs WHERE id = 1')).toEqual([{ spec_json: '{"title":"P2"}' }]);
    await expect(repo.preflightRecovery(input.requestId, cached.attempt)).rejects.toMatchObject({ code: 'ATTEMPT_STALE' });
    await expect(repo.complete(input.requestId, done.resultJson!, oldBusiness, cached.attempt)).rejects.toMatchObject({ code: 'ATTEMPT_STALE' });
    expect(oldBusiness).not.toHaveBeenCalled();
  });

  it.each([undefined, null, 0, 1.5, '1', NaN])('never infers a generation for missing/invalid expectedAttempt %s', async token => {
    await ready();
    const before = await repo.get(input.requestId);
    const callback = vi.fn();
    const invalid = token as unknown as number;
    for (const invoke of [
      () => repo.prepare(input.requestId, payload, invalid),
      () => repo.markPushing(input.requestId, invalid),
      () => repo.complete(input.requestId, result, callback, invalid),
      () => repo.failBeforePush(input.requestId, '{}', invalid),
      () => repo.markUnknown(input.requestId, '{}', invalid),
      () => repo.preflightRecovery(input.requestId, invalid),
      () => repo.restartFailed(input, invalid),
    ]) await expect(invoke()).rejects.toMatchObject({ status: 409, code: 'ATTEMPT_STALE' });
    expect(await repo.get(input.requestId)).toEqual(before);
    expect(callback).not.toHaveBeenCalled();
  });

  it('rolls back a failed restart without incrementing its persisted attempt or releasing a new lock', async () => {
    await ready(); await repo.failBeforePush(input.requestId, '{}', 1);
    const before = await repo.get(input.requestId);
    await db.exec(`CREATE TRIGGER fail_restart BEFORE UPDATE ON clawweb_pack_operations
      WHEN NEW.attempt > OLD.attempt BEGIN SELECT RAISE(ABORT, 'restart journal failure'); END;`);
    await expect(repo.restartFailed(input, 1)).rejects.toThrow('restart journal failure');
    expect(await repo.get(input.requestId)).toEqual(before);
    expect((await repo.begin({ ...input, requestId: 'other-after-failed-restart' })).created).toBe(true);
  });

  it('propagates database failures', async () => {
    await db.exec('DROP TABLE clawweb_pack_operations');
    await expect(repo.get(input.requestId)).rejects.toThrow('no such table');
    await expect(repo.begin(input)).rejects.toThrow('no such table');
  });
});
