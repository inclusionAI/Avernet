// @vitest-environment node
/** Opt-in cross-repository contract: CLAWMIND_ROOT=/path/to/ClawMind Node>=22 vitest run this file.
 * Uses real runtime/store/ApiClient and server router/repository/SQLite transaction adapter. */
import { beforeEach, afterEach, describe, expect, it } from 'vitest';
import express from 'express';
import { spawn } from 'node:child_process';
import { generateKeyPairSync } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Server } from 'node:http';
import { SqliteDatabase } from '../../../../../../shared/server/db/sqlite-database.js';
import { sqliteDialect } from '../../../../../../shared/server/db/dialect.js';
import { recoveryClockMigration } from '../../../../../../shared/server/recovery-clock-schema.js';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../runs.js';

const clientRoot = process.env.CLAWMIND_ROOT;
describe.skipIf(!clientRoot)('real ClawMind recovery runtime HTTP contract', () => {
  let Runtime: any, Store: any, Client: any;
  let db: SqliteDatabase, repo: FlowRunRepository, server: Server;
  let path: string, directory: string, baseUrl: string, mono: number;
  const stores: any[] = [];
  let client: any, clientConfig: any;
  beforeEach(async () => {
    ({ WorkflowRecoveryRuntime: Runtime } = await import(/* @vite-ignore */ `/@fs/${clientRoot}/src/recovery/runtime.ts`));
    ({ RecoveryStore: Store } = await import(/* @vite-ignore */ `/@fs/${clientRoot}/src/recovery/store.ts`));
    ({ ApiClient: Client } = await import(/* @vite-ignore */ `/@fs/${clientRoot}/src/db/api-client.ts`));
    const { DatabaseSync } = await import('node:sqlite');
    // DatabaseSync implements the same prepare/exec subset consumed by the actual
    // adapter; this avoids mixing better-sqlite3 native ABIs between Node20 and22.
    const raw = new DatabaseSync(':memory:');
    raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, started_at INTEGER,
      completed_at INTEGER, result_json TEXT, current_phase TEXT, total_duration_ms INTEGER);
      INSERT INTO flow_runs VALUES ('flow','running',123,NULL,NULL,NULL,NULL)`);
    for (const sql of recoveryClockMigration.sql) raw.exec(sqliteDialect.renderDdl(sql));
    db = new SqliteDatabase(raw as never);
    repo = new FlowRunRepository(db);
    const app = express(); app.use(express.json());
    app.use('/api/internal/runs', createInternalRunsRouter(repo));
    await new Promise<void>(resolve => { server = app.listen(0, '127.0.0.1', resolve); });
    baseUrl = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
    const { privateKey } = generateKeyPairSync('ed25519');
    clientConfig = { baseUrl, privateKeyB64: privateKey.export({type:'pkcs8',format:'der'}).toString('base64'), maxRetries: 1 };
    client = new Client(clientConfig);
    directory = mkdtempSync(join(tmpdir(), 'recovery-wire-'));
    path = join(directory, 'runtime.sqlite'); mono = 0;
  });
  afterEach(async () => {
    for (const store of stores.splice(0)) store.close();
    if (server) await new Promise<void>(resolve => server.close(() => resolve()));
    if (db) await db.close();
    if (directory) rmSync(directory, { recursive: true, force: true });
  });
  function open() {
    const store = new Store(path, { monotonicNow: () => mono }); stores.push(store);
    return new Runtime(store, client);
  }
  it('unwraps flat GET/PUT through ApiClient and resumes with no downtime charge', async () => {
    const first = open();
    await first.ensure('flow'); mono += 1_200_000; await first.sync();
    const before = await repo.getRecoveryClock('flow');
    expect((await client.get('/runs/flow/recovery-clock')).data.clock).toEqual(before);
    first.close(); mono += 1_800_000;
    const second = open(); await second.recovering('flow'); await second.activate('flow');
    const after = await repo.getRecoveryClock('flow');
    expect(after!.elapsedMs).toBe(1_200_000);
    expect(after!.bootId).not.toBe(before!.bootId);
    expect(after!.seq).toBeGreaterThan(before!.seq);
    expect(after!.epoch).toBe(0);
  });
  it('reconciles an accepted claim again in the same boot', async () => {
    const runtime = open(); await runtime.recovering('flow');
    await runtime.recovering('flow'); await runtime.activate('flow');
    expect(runtime.isReady('flow')).toBe(true);
  });
  it('resumes a retry accepted remotely before local activation', async () => {
    const first = open(); await first.ensure('flow');
    await first.prepareRetry('flow', 'request', '{}'); first.close();
    const second = open(); await second.recovering('flow');
    await second.finishRetry('flow', 'request', []);
    expect(await repo.getRecoveryClock('flow')).toEqual(expect.objectContaining({ epoch: 1, phase: 'active' }));
    expect(await db.query('SELECT status, started_at FROM flow_runs')).toEqual([expect.objectContaining({ status: 'running', started_at: 123 })]);
  });
  it('reconciles local active with remote retry_pending after a crash before publish', async () => {
    const first = open(); await first.ensure('flow');
    await first.prepareRetry('flow', 'request', '{}');
    first.store.activate('flow'); // durable part of activate, before its HTTP publish
    first.close();
    const second = open(); await second.recovering('flow');
    await second.finishRetry('flow', 'request', []);
    expect(await repo.getRecoveryClock('flow')).toEqual(expect.objectContaining({ epoch: 1, phase: 'active' }));
  });
  it('replays retry preparation after a transport failure before the server accepts it', async () => {
    const first = open(); await first.ensure('flow');
    const put = client.put.bind(client);
    client.put = async (route: string, body: any) => body.mode === 'retry'
      ? { ok: false, status: 0, error: 'simulated disconnect', data: null } : put(route, body);
    await expect(first.prepareRetry('flow', 'request', '{}')).rejects.toThrow('sync_failed');
    client.put = put; first.close();
    const second = open(); await second.recovering('flow');
    await second.prepareRetry('flow', 'request', '{}');
    await second.finishRetry('flow', 'request', []);
    expect(await repo.getRecoveryClock('flow')).toEqual(expect.objectContaining({ epoch: 1, phase: 'active' }));
  });
  it('reconciles an accepted retry whose HTTP response was lost without opening another epoch', async () => {
    const first = open(); await first.ensure('flow');
    const put = client.put.bind(client);
    client.put = async (route: string, body: any) => {
      const result = await put(route, body);
      return body.mode === 'retry' ? { ok: false, status: 0, error: 'lost response', data: null } : result;
    };
    await expect(first.prepareRetry('flow', 'request', '{}')).rejects.toThrow('sync_failed');
    client.put = put; first.close();
    const second = open(); await second.recovering('flow');
    await second.prepareRetry('flow', 'request', '{}');
    await second.finishRetry('flow', 'request', []);
    await second.prepareRetry('flow', 'request', '{}');
    expect((await repo.getRecoveryClock('flow'))!.epoch).toBe(1);
  });
  it('persists fractional monotonic increments as integer milliseconds accepted by the API', async () => {
    const runtime = open(); await runtime.ensure('flow');
    for (let i = 0; i < 10; i++) { mono += 0.25; await runtime.sync(); }
    expect((await repo.getRecoveryClock('flow'))!.elapsedMs).toBe(2);
  });
  it.each(['retry_pending', 'active'])('recovers after SIGKILL with local %s and remote pending', async phase => {
    const source = `
      const {RecoveryStore} = await import(process.env.CLIENT_ROOT + '/src/recovery/store.ts');
      const {WorkflowRecoveryRuntime} = await import(process.env.CLIENT_ROOT + '/src/recovery/runtime.ts');
      const {ApiClient} = await import(process.env.CLIENT_ROOT + '/src/db/api-client.ts');
      const store = new RecoveryStore(process.env.STORE_PATH, {monotonicNow: () => 0});
      const runtime = new WorkflowRecoveryRuntime(store, new ApiClient(JSON.parse(process.env.CLIENT_CONFIG)));
      await runtime.ensure('flow');
      await runtime.prepareRetry('flow','killed-request','{}');
      if (process.env.LOCAL_PHASE === 'active') store.activate('flow');
      process.send('persisted');
      setInterval(() => {}, 1000);
    `;
    const child = spawn(process.execPath, ['--import', `${clientRoot}/node_modules/tsx/dist/loader.mjs`, '--input-type=module', '-e', source], {
      env: { ...process.env, CLIENT_ROOT: clientRoot!, STORE_PATH: path, CLIENT_CONFIG: JSON.stringify(clientConfig), LOCAL_PHASE: phase },
      stdio: ['ignore', 'ignore', 'pipe', 'ipc'],
    });
    let errors = ''; child.stderr?.on('data', chunk => { errors += String(chunk); });
    try {
      await new Promise<void>((resolve, reject) => {
        child.once('message', () => resolve());
        child.once('exit', code => reject(new Error(`Child exited ${code}: ${errors.slice(-1000)}`)));
        child.once('error', reject);
      });
      const exit = new Promise<void>(resolve => child.once('exit', () => resolve()));
      child.kill('SIGKILL'); await exit;
      const recovered = open(); await recovered.recovering('flow');
      await recovered.prepareRetry('flow', 'killed-request', '{}');
      await recovered.finishRetry('flow', 'killed-request', []);
      expect(await repo.getRecoveryClock('flow')).toEqual(expect.objectContaining({ epoch: 1, phase: 'active' }));
      expect(recovered.isReady('flow')).toBe(true);
    } finally { child.kill('SIGKILL'); }
  }, 15_000);

});
