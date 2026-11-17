import Database from 'better-sqlite3';
import express from 'express';
import { once } from 'node:events';
import { generateKeyPairSync,sign } from 'node:crypto';
import { afterEach,describe,expect,it } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { createAIStudioStateFixture } from '../../../repositories/__tests__/aistudio-train-state.fixture.js';
import { captureAIStudioRawBody,createAIStudioServiceAuthenticator } from '@avernet/clawweb-shared/server/middleware/aistudio-train-signature';
import { AIStudioTrainWatchRepository } from '../../../repositories/aistudio-train-watch-repository.js';
import { AIStudioTrainManualAuditRepository } from '../../../repositories/aistudio-train-manual-audit-repository.js';
import { createInternalAIStudioTrainManualAuditRouter } from '../events.js';
import { watchFixture } from '../../../contracts/__tests__/aistudio-train.fixture.js';
import { manualAuditEvent } from '../../../repositories/__tests__/aistudio-train-manual-audit.fixture.js';
const key=generateKeyPairSync('ed25519'); const publicKeyB64=key.publicKey.export({type:'spki',format:'der'}).toString('base64');
const cleanup:(()=>Promise<void>)[]=[];
afterEach(async () => {for(const fn of cleanup.splice(0)) await fn();});
function wireEvent() {
  const e=manualAuditEvent(); return {event_id:e.id,flow_id:e.flowId,workflow_id:e.workflowId,node_id:e.nodeId,event_type:e.type,
    attempt:e.attempt,time:e.time,data_json:JSON.stringify(e.data),error_text:null};
}
async function fixture(configured=true) {
  const raw=new Database(':memory:'); createAIStudioStateFixture(raw);
  raw.exec(`CREATE TABLE flow_runs(flow_id TEXT,workflow_id TEXT,origin_bot_id TEXT,status TEXT);
    INSERT INTO flow_runs VALUES('flow-1','wf-1','bot:owner','waiting');
    CREATE TABLE flow_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT,flow_id TEXT,workflow_id TEXT,node_id TEXT,event_type TEXT,
      attempt INTEGER,time BIGINT,data_json TEXT,error_text TEXT,gmt_create INTEGER,gmt_modified INTEGER)`);
  const db=new SqliteDatabase(raw); const watches=new AIStudioTrainWatchRepository(db);
  await watches.create(watchFixture({manualRequestedAt:200,manualRequestedBy:'owner',lastManualRequestedAt:200}));
  const app=express(); app.use(express.json({verify:captureAIStudioRawBody}));
  app.use('/api/internal/events',createInternalAIStudioTrainManualAuditRouter({repository:new AIStudioTrainManualAuditRepository(db),
    findRunTarget:id => watches.findRunTarget(id),findWatch:(f,n,a) => watches.find(f,n,a),
    resolveVerifiedService:configured ? createAIStudioServiceAuthenticator({publicKeyB64}) : undefined}));
  const server=app.listen(0,'127.0.0.1'); await once(server,'listening');
  cleanup.push(async () => {await new Promise<void>(resolve => server.close(() => resolve()));raw.close();});
  const base=`http://127.0.0.1:${(server.address() as {port:number}).port}/api/internal/events`;
  async function request(body:unknown=wireEvent(),signed=true,path='/aistudio-train-manual-audit') {
    const rawBody=JSON.stringify(body); const timestamp=String(Date.now());
    return fetch(base+path,{method:'POST',body:rawBody,headers:{'content-type':'application/json',
      ...(signed ? {'x-timestamp':timestamp,'x-signature':sign(null,Buffer.from(`${timestamp}.${rawBody}`),key.privateKey).toString('base64')} : {'x-user-id':'owner',cookie:'staff_id=owner'})}});
  }
  return {raw,request};
}
describe('signed atomic manual audit endpoint', () => {
  it('requires configured real Ed25519 service auth, including dev, and does not expose the legacy events writer', async () => {
    const f=await fixture(); expect((await f.request(wireEvent(),false)).status).toBe(401);
    const disabled=await fixture(false); expect((await disabled.request()).status).toBe(503);
    expect((await f.request(wireEvent(),true,'/')).status).toBe(404);
  });
  it('acknowledges one committed event across concurrent HTTP retries, 1001 new events, and changed query time/status', async () => {
    const f=await fixture();
    const results=await Promise.all(Array.from({length:6},()=>f.request()));
    for(const result of results) {expect(result.status).toBe(201);expect(await result.json()).toEqual({success:true,data:{inserted:true}});}
    const insert=f.raw.prepare("INSERT INTO flow_events(event_id,flow_id,event_type,time) VALUES(?,'flow-1','node_progress',999)");
    for(let i=0;i<1001;i++) insert.run(`noise-${i}`);
    const again=wireEvent(); again.time=999; again.data_json=JSON.stringify({...manualAuditEvent().data,rawStatus:'success'});
    expect((await f.request(again)).status).toBe(201);
    expect(f.raw.prepare('SELECT COUNT(*) AS n FROM flow_events WHERE event_id=?').get(again.event_id)).toEqual({n:1});
  });
  it('validates exact stable event identity and server-owned run/watch target, never caller ownership', async () => {
    const f=await fixture();
    for(const patch of [{event_id:'forged'},{event_type:'node_failed'},{attempt:0},{time:Date.now()},
      {data_json:'{}'},{data_json:JSON.stringify({...manualAuditEvent().data,manual:false})},{sql:'DROP TABLE flow_events'}]) {
      expect((await f.request({...wireEvent(),...patch})).status).toBe(400);
    }
    expect((await f.request({...wireEvent(),workflow_id:'other'})).status).toBe(403);
    const wrong=wireEvent();wrong.data_json=JSON.stringify({...manualAuditEvent().data,watchId:'another'});wrong.event_id='aistudio_manual_another_200';
    expect((await f.request(wrong)).status).toBe(403);
    f.raw.exec("UPDATE flow_runs SET origin_bot_id='bot:other'"); expect((await f.request()).status).toBe(403);
  });
  it('refuses DB failure, marker-only ACK or missing schema; retry recovers without leaving a poisoned marker', async () => {
    const f=await fixture();
    f.raw.exec("CREATE TRIGGER fail_event BEFORE INSERT ON flow_events BEGIN SELECT RAISE(FAIL,'private failure');END");
    const failed=await f.request();expect(failed.status).toBe(503);expect(await failed.text()).not.toContain('private failure');
    expect(f.raw.prepare("SELECT COUNT(*) AS n FROM cm_app_config WHERE config_key LIKE 'aisa:%'").get()).toEqual({n:0});
    f.raw.exec('DROP TRIGGER fail_event');expect((await f.request()).status).toBe(201);
    f.raw.exec('DELETE FROM flow_events');expect((await f.request()).status).toBe(503);
    f.raw.exec('DROP TABLE cm_app_config');expect((await f.request()).status).toBe(503);
  });
});
