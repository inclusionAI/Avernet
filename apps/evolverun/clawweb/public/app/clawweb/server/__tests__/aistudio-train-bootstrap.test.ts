import Database from 'better-sqlite3';
import express from 'express';
import { once } from 'node:events';
import { generateKeyPairSync, sign } from 'node:crypto';
import type { Server } from 'node:http';
import { afterEach, expect, it, vi } from 'vitest';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import type { ClawWebLifecycle } from '../bootstrap.js';
import { watchFixture } from './aistudio-train.fixture.js';
const state=vi.hoisted(() => ({db:null as IDatabase | null,publicKeyB64:''}));
vi.mock('@avernet/clawweb-shared/server/db',async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(),
  initDatabase:async () => ({db:state.db}),closeDatabase:vi.fn(),resolveSignatureConfig:() => ({publicKeyB64:state.publicKeyB64}),
}));
vi.mock('@avernet/clawweb-shared/server/env',async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/env')>(),getClawWebPublicBaseUrl:() => 'https://clawweb.example.org',
}));
vi.mock('@avernet/clawevolve',async () => {
  const {Router}=await import('express');
  return {createClawevolveModule:() => ({start:async () => {},stop:async () => {},publicRouter:Router(),benchRouter:Router()})};
});
vi.mock('@avernet/clawinsight',async () => {
  const {Router}=await import('express');
  return {createInsightRuntime:() => ({service:{}}),createInsightRouter:() => Router(),createInsightManagementRuntime:() => ({router:Router()}),createSessionRecoveryRouter:() => Router()};
});
vi.mock('../repair-ais-execution.js',() => ({resolveRepairAisConfiguration:async () => null}));
import { createClawWebBootstrap } from '../public-bootstrap.js';
let lifecycle: ClawWebLifecycle | void;
afterEach(async () => { await lifecycle?.stop?.(); lifecycle=undefined; await state.db?.close(); state.db=null; vi.restoreAllMocks(); vi.unstubAllEnvs(); });
it('actual public bootstrap authenticates internal raw-body traffic and keeps forged public identities disabled',async () => {
  const key=generateKeyPairSync('ed25519'); state.publicKeyB64=key.publicKey.export({type:'spki',format:'der'}).toString('base64');
  const raw=new Database(':memory:'); state.db=new SqliteDatabase(raw); await runMigrations(state.db,'sqlite');
  raw.exec("INSERT INTO flow_runs(flow_id,workflow_id,status,started_at,origin_bot_id) VALUES('flow-1','wf-1','waiting',100,'bot:owner')");
  vi.stubEnv('PORT','0'); let server: Server | undefined; const listen=express.application.listen;
  vi.spyOn(express.application,'listen').mockImplementation(function (this:express.Application,...args:Parameters<typeof listen>) {server=listen.apply(this,args);return server;});
  lifecycle=await createClawWebBootstrap().start({environment:'dev',profile:'public',config:{auth:{admins:['owner']}},configSources:[]});
  if (!server!.listening) await once(server!,'listening');
  const base=`http://127.0.0.1:${(server!.address() as {port:number}).port}`;
  const internal=base+'/api/internal/aistudio-train-watches';
  const forged={'x-user-id':'dev_local',cookie:'staff_id=owner; user=owner'};
  expect((await fetch(internal+'/find?flowId=flow-1&nodeId=train',{headers:forged})).status).toBe(401);
  const watch=watchFixture(); const body=JSON.stringify({watch}); const timestamp=String(Date.now());
  const response=await fetch(internal+'/create',{method:'POST',body,headers:{'content-type':'application/json','x-timestamp':timestamp,
    'x-signature':sign(null,Buffer.from(`${timestamp}.${body}`),key.privateKey).toString('base64')}});
  expect(response.status).toBe(200); expect(await response.json()).toEqual({success:true,data:{watch}});
  const auditPath=base+'/api/internal/events/aistudio-train-manual-audit';
  const auditBody=JSON.stringify({event_id:'aistudio_manual_watch-1_200',flow_id:'flow-1',workflow_id:'wf-1',node_id:'train',
    event_type:'node_progress',attempt:1,time:201,data_json:JSON.stringify({source:'aistudio-train',manual:true,pollSource:'manual',
      userId:'owner',watchId:'watch-1',requestedAt:200,rawStatus:'running'})});
  expect((await fetch(auditPath,{method:'POST',body:auditBody,headers:{...forged,'content-type':'application/json'}})).status).toBe(401);
  const audit=await fetch(auditPath,{method:'POST',body:auditBody,headers:{'content-type':'application/json','x-timestamp':timestamp,
    'x-signature':sign(null,Buffer.from(`${timestamp}.${auditBody}`),key.privateKey).toString('base64')}});
  expect(audit.status).toBe(201);expect(await audit.json()).toEqual({success:true,data:{inserted:true}});
  expect((await fetch(base+'/api/internal/events',{method:'POST',body:'{}',headers:{'content-type':'application/json'}})).status).toBe(404);
  const path=base+'/api/runs/flow-1/nodes/train/aistudio-train';
  expect((await fetch(path+'?attempt=1',{headers:forged})).status).toBe(503);
  expect((await fetch(path+'/poll-manual',{method:'POST',body:'{"attempt":1}',headers:{...forged,'content-type':'application/json',origin:'https://clawweb.example.org'}})).status).toBe(503);
});
