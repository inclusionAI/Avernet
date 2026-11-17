import Database from 'better-sqlite3';
import express from 'express';
import { once } from 'node:events';
import { generateKeyPairSync, sign } from 'node:crypto';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { aistudioTrainDdl } from '@avernet/clawweb-shared/server/schema-aistudio-train';
import { aistudioTrainManualAuditDdl } from '@avernet/clawweb-shared/server/schema-aistudio-train-manual-audit';
import { captureAIStudioRawBody, createAIStudioServiceAuthenticator } from '@avernet/clawweb-shared/server/middleware/aistudio-train-signature';
import { FlowEventRepository, type FlowEventInsert } from '../../../repositories/event-repository.js';
import { AIStudioTrainWatchRepository } from '../../../repositories/aistudio-train-watch-repository.js';
import { AIStudioTrainManualAuditRepository } from '../../../repositories/aistudio-train-manual-audit-repository.js';
import { createInternalEventsRouter, createInternalAIStudioTrainManualAuditRouter } from '../events.js';
import { watchFixture } from '../../../contracts/__tests__/aistudio-train.fixture.js';
import { manualAuditEvent } from '../../../repositories/__tests__/aistudio-train-manual-audit.fixture.js';
const key = generateKeyPairSync('ed25519');
const publicKeyB64 = key.publicKey.export({ type: 'spki', format: 'der' }).toString('base64');
const cleanups: (() => Promise<void>)[] = [];
afterEach(async () => { for (const cleanup of cleanups.splice(0)) await cleanup(); });
async function fixture() {
  const raw = new Database(':memory:');
  for (const ddl of [...aistudioTrainDdl('sqlite'), ...aistudioTrainManualAuditDdl('sqlite')]) raw.exec(ddl);
  // Deliberately NO event_id uniqueness: this is the real legacy table contract.
  raw.exec(`CREATE TABLE flow_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT,flow_id TEXT,workflow_id TEXT,
    node_id TEXT,event_type TEXT,attempt INTEGER,time BIGINT,data_json TEXT,error_text TEXT,gmt_create INTEGER,gmt_modified INTEGER);
    CREATE TABLE flow_runs(flow_id TEXT,workflow_id TEXT,origin_bot_id TEXT,status TEXT);
    INSERT INTO flow_runs VALUES('flow-1','wf-1','bot:owner','waiting')`);
  const db = new SqliteDatabase(raw); const generic = new FlowEventRepository(db);
  const watches = new AIStudioTrainWatchRepository(db); await watches.create(watchFixture());
  const app = express(); app.use(express.json({ verify: captureAIStudioRawBody }));
  // Some internal hosts expose both ports. Generic must not bypass the marker.
  app.use('/api/internal/events', createInternalEventsRouter(generic));
  app.use('/api/internal/events', createInternalAIStudioTrainManualAuditRouter({
    repository: new AIStudioTrainManualAuditRepository(db),
    resolveVerifiedService: createAIStudioServiceAuthenticator({ publicKeyB64 }),
    findRunTarget: id => watches.findRunTarget(id), findWatch: (f,n,a) => watches.find(f,n,a),
  }));
  const server = app.listen(0,'127.0.0.1'); await once(server,'listening');
  cleanups.push(async () => { await new Promise<void>(resolve => server.close(() => resolve())); raw.close(); });
  const base = `http://127.0.0.1:${(server.address() as {port:number}).port}/api/internal/events`;
  async function post(event: FlowEventInsert, dedicated = false) {
    const body = JSON.stringify({ event_id:event.id, flow_id:event.flowId, workflow_id:event.workflowId,
      node_id:event.nodeId, event_type:event.type, attempt:event.attempt, time:event.time, data_json:JSON.stringify(event.data), error_text:null });
    const timestamp = String(Date.now());
    return fetch(base+(dedicated ? '/aistudio-train-manual-audit' : ''), { method:'POST',body,
      headers:{ 'content-type':'application/json','x-timestamp':timestamp,
        'x-signature':sign(null,Buffer.from(`${timestamp}.${body}`),key.privateKey).toString('base64') } });
  }
  const count = (eventId: string) => raw.prepare('SELECT COUNT(*) AS n FROM flow_events WHERE event_id=?').get(eventId);
  return {raw,db,generic,post,count};
}
describe('generic events cannot bypass the dedicated manual audit namespace', () => {
  it('keeps one event when generic and dedicated writes race, with failed generic ACKs', async () => {
    const f = await fixture(); const event = manualAuditEvent();
    const results = await Promise.all(Array.from({length:12},(_,i) => f.post(event,i%2===0)));
    for (const [index,response] of results.entries()) {
      expect(response.status).toBe(index%2===0 ? 201 : 503);
      expect((await response.json()).success).toBe(index%2===0);
    }
    expect(f.count(event.id)).toEqual({n:1});
    expect(f.raw.prepare('SELECT COUNT(*) AS n FROM aistudio_train_manual_audits').get()).toEqual({n:1});
  });
  it('rejects delayed generic duplicates after dedicated commit and leaves dedicated retries idempotent', async () => {
    const f = await fixture(); const event = manualAuditEvent();
    expect((await f.post(event,true)).status).toBe(201);
    const generic = await f.post(event); expect(generic.status).toBe(503); expect((await generic.json()).success).toBe(false);
    const retry = await f.post({...event,time:999,data:{...event.data,rawStatus:'success'}},true);
    expect(retry.status).toBe(201); expect(await retry.json()).toEqual({success:true,data:{inserted:true}});
    expect(f.count(event.id)).toEqual({n:1});
  });
  it('rejects either reserved IDs or manual-training markers before executing SQL', async () => {
    const f = await fixture(); const sql = vi.spyOn(f.db,'exec');
    const event = manualAuditEvent();
    for (const blocked of [{...event,data:undefined}, {...event,id:'ordinary-looking-id'}]) {
      expect(await f.generic.insert(blocked)).toBe(false);
      const response = await f.post(blocked); expect(response.status).toBe(503); expect((await response.json()).success).toBe(false);
    }
    expect(sql).not.toHaveBeenCalled();
    expect(f.raw.prepare('SELECT COUNT(*) AS n FROM flow_events').get()).toEqual({n:0});
  });
  it('preserves ordinary legacy duplicate IDs and non-manual training events', async () => {
    const f = await fixture();
    for (const data of [{source:'legacy',manual:true},{source:'aistudio-train',manual:false},{source:'aistudio-train'}]) {
      const event = manualAuditEvent({id:'ordinary-'+JSON.stringify(data).length,data});
      for (let i=0;i<2;i++) {
        const response = await f.post(event); expect(response.status).toBe(201);
        expect(await response.json()).toEqual({success:true,data:{inserted:true}});
      }
      expect(f.count(event.id)).toEqual({n:2});
    }
  });
});
