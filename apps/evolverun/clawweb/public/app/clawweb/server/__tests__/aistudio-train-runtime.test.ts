import Database from 'better-sqlite3';
import express from 'express';
import { once } from 'node:events';
import { generateKeyPairSync, sign } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { afterEach, expect, it } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { aistudioTrainDdl } from '@avernet/clawweb-shared/server/schema-aistudio-train';
import { captureAIStudioRawBody, createAIStudioTrainRuntime } from '../aistudio-train-runtime.js';
import { watchFixture } from './aistudio-train.fixture.js';
const key=generateKeyPairSync('ed25519'); const publicKeyB64=key.publicKey.export({format:'der',type:'spki'}).toString('base64');
const cleanup: (() => Promise<void>)[]=[];
afterEach(async () => { for(const fn of cleanup.splice(0)) await fn(); });
async function fixture(verified = false) {
  const raw=new Database(':memory:'); for(const ddl of aistudioTrainDdl('sqlite')) raw.exec(ddl);
  raw.exec(`CREATE TABLE flow_runs(flow_id TEXT,workflow_id TEXT,origin_bot_id TEXT,status TEXT);
    INSERT INTO flow_runs VALUES('flow-1','wf-1','bot:owner','waiting');
    CREATE TABLE node_executions(id INTEGER PRIMARY KEY,flow_id TEXT,node_id TEXT,attempt INTEGER,executor_type TEXT,status TEXT);
    INSERT INTO node_executions VALUES(1,'flow-1','train',1,'aistudio-train','waiting')`);
  const runtime=createAIStudioTrainRuntime(new SqliteDatabase(raw),{publicKeyB64,publicOrigin:'https://clawweb.example.org',
    ...(verified ? {resolveVerifiedPrincipal:async () => ({actorId:'owner'}),authorizeRunRead:async () => true,
      authorizeOwnerOperation:async () => true,authorizeWorkflowExecute:async () => true} : {})});
  const app=express(); app.use(express.json({verify:captureAIStudioRawBody}));
  app.use('/api/internal/aistudio-train-watches',runtime.internalRouter); app.use('/api/runs',runtime.publicRouter);
  const server=app.listen(0,'127.0.0.1'); await once(server,'listening');
  cleanup.push(async () => { await new Promise<void>(resolve => server.close(() => resolve())); raw.close(); });
  return {base:`http://127.0.0.1:${(server.address() as {port:number}).port}`,raw};
}
it('exports a default-deny host factory and wires exact raw-body verification before JSON parsing', async () => {
  const {base}=await fixture();
  expect((await fetch(base+'/api/runs/flow-1/nodes/train/aistudio-train?attempt=1',{headers:{'x-user-id':'owner'}})).status).toBe(503);
  expect((await fetch(base+'/api/internal/aistudio-train-watches/find?flowId=flow-1&nodeId=train')).status).toBe(401);
  const bootstrap=readFileSync(new URL('../public-bootstrap.ts',import.meta.url),'utf8');
  expect(bootstrap).toMatch(/express.json\(\{[^}]*verify: captureAIStudioRawBody/);
  expect(bootstrap).toContain('"/api/internal/aistudio-train-watches", aistudioTrain.internalRouter');
  expect(bootstrap).toContain('"/api/runs", aistudioTrain.publicRouter');
  expect(bootstrap).toContain('resolveSignatureConfig().publicKeyB64');
});
it('connects signed internal create to public GET/manual mailbox without external AIS or owner URL lookup', async () => {
  const {base,raw}=await fixture(true); const now=Math.floor(Date.now()/1000);
  const watch=watchFixture({submittedAt:now,firstPollAt:now+600,nextPollAt:now+600,timeoutAt:now+3600});
  const body=JSON.stringify({watch}); const timestamp=String(Date.now());
  const created=await fetch(base+'/api/internal/aistudio-train-watches/create',{method:'POST',body,headers:{'content-type':'application/json',
    'x-timestamp':timestamp,'x-signature':sign(null,Buffer.from(`${timestamp}.${body}`),key.privateKey).toString('base64')}});
  expect(created.status).toBe(200);
  const path=base+'/api/runs/flow-1/nodes/train/aistudio-train';
  expect((await fetch(path+'?attempt=1')).status).toBe(200);
  const response=await fetch(path+'/poll-manual',{method:'POST',body:'{"attempt":1}',headers:{'content-type':'application/json',origin:'https://clawweb.example.org'}});
  expect(response.status).toBe(202); expect((await response.json()).resolved).toBe(false);
  expect(raw.prepare('SELECT manual_requested_by FROM aistudio_train_watches').get()).toEqual({manual_requested_by:'owner'});
});
