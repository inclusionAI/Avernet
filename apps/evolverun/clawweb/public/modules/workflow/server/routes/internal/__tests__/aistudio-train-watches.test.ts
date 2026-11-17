import Database from 'better-sqlite3';
import express from 'express';
import { once } from 'node:events';
import { generateKeyPairSync, sign } from 'node:crypto';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { aistudioTrainDdl } from '@avernet/clawweb-shared/server/schema-aistudio-train';
import { captureAIStudioRawBody, createAIStudioServiceAuthenticator } from '@avernet/clawweb-shared/server/middleware/aistudio-train-signature';
import { AIStudioTrainWatchRepository } from '../../../repositories/aistudio-train-watch-repository.js';
import { createInternalAIStudioTrainWatchesRouter } from '../aistudio-train-watches.js';
import { watchFixture } from '../../../contracts/__tests__/aistudio-train.fixture.js';
const key = generateKeyPairSync('ed25519');
const publicKeyB64 = key.publicKey.export({format:'der',type:'spki'}).toString('base64');
const cleanup: (() => Promise<void>)[] = [];
afterEach(async () => { for (const fn of cleanup.splice(0)) await fn(); });
async function fixture(auth = true) {
  const raw = new Database(':memory:'); for(const ddl of aistudioTrainDdl('sqlite')) raw.exec(ddl);
  raw.exec(`CREATE TABLE flow_runs(flow_id TEXT,workflow_id TEXT,origin_bot_id TEXT,status TEXT);
    INSERT INTO flow_runs VALUES('flow-1','wf-1','bot:owner','running')`);
  const repo = new AIStudioTrainWatchRepository(new SqliteDatabase(raw));
  const app = express(); app.use(express.json({ verify:captureAIStudioRawBody }));
  const findRunTarget=vi.fn((id:string) => repo.findRunTarget(id));
  app.use('/api/internal/aistudio-train-watches',createInternalAIStudioTrainWatchesRouter({store:repo,findRunTarget,
    resolveVerifiedService:auth ? createAIStudioServiceAuthenticator({publicKeyB64}) : undefined }));
  const server=app.listen(0,'127.0.0.1'); await once(server,'listening');
  cleanup.push(async () => { await new Promise<void>(resolve => server.close(() => resolve())); raw.close(); });
  const base=`http://127.0.0.1:${(server.address() as {port:number}).port}/api/internal/aistudio-train-watches`;
  async function request(path:string, body?:unknown, signed = true) {
    const rawBody = body === undefined ? '' : JSON.stringify(body); const timestamp=String(Date.now());
    return fetch(base+path,{ method:body === undefined ? 'GET':'POST',...(body === undefined ? {} : {body:rawBody}),
      headers:{'content-type':'application/json',...(signed ? {'x-timestamp':timestamp,'x-signature':sign(null,Buffer.from(`${timestamp}.${rawBody}`),key.privateKey).toString('base64')} : {'x-user-id':'admin'})} });
  }
  return {raw,repo,request,findRunTarget};
}
describe('internal watch service API conformance', () => {
  it('requires real service authentication, configured by default', async () => {
    const f=await fixture(); expect((await f.request('/create',{watch:watchFixture()},false)).status).toBe(401);
    const disabled=await fixture(false); expect((await disabled.request('/find?flowId=flow-1&nodeId=train')).status).toBe(503);
    expect(await f.repo.find('flow-1','train')).toBeNull();
  });
  it('roundtrips create/find/due/CAS envelopes as ApiClient unwrap expects', async () => {
    const f=await fixture(); const watch=watchFixture();
    const created=await f.request('/create',{watch}); expect(created.status).toBe(200); expect(await created.json()).toEqual({success:true,data:{watch}});
    expect(await (await f.request('/find?flowId=flow-1&nodeId=train&attempt=1')).json()).toEqual({success:true,data:{watch}});
    expect(await (await f.request('/due?ownerBotId=bot%3Aowner&now=700&limit=5')).json()).toEqual({success:true,data:{watches:[watch]}});
    const next={...watch,version:2,leaseOwner:'worker',leaseExpiresAt:800};
    const results=await Promise.all([f.request('/cas',{expectedVersion:1,watch:next}),f.request('/cas',{expectedVersion:1,watch:next})]);
    expect(await Promise.all(results.map(async r => (await r.json()).data.updated))).toEqual(expect.arrayContaining([true,false]));
    expect(await (await f.request('/cas',{expectedVersion:1,watch:{...watch,version:2,phase:'resolved'}})).json()).toEqual({success:true,data:{updated:false}});
  });
  it('find returns a nullable watch envelope for an absent flow/node without requiring a phantom owner', async () => {
    const f=await fixture(); const response=await f.request('/find?flowId=absent&nodeId=none&attempt=1');
    expect(response.status).toBe(200); expect(await response.json()).toEqual({success:true,data:{watch:null}});
  });
  it('rejects cross-owner/workflow targets from persisted run, not supplied headers', async () => {
    const f=await fixture();
    for(const extra of [{ownerBotId:'bot:other'},{workflowId:'other'}]) expect((await f.request('/create',{watch:watchFixture(extra)})).status).toBe(403);
    await f.repo.create(watchFixture()); f.raw.exec("UPDATE flow_runs SET origin_bot_id='bot:other'");
    expect((await f.request('/find?flowId=flow-1&nodeId=train')).status).toBe(403);
    expect((await f.request('/due?ownerBotId=bot%3Aowner&now=700&limit=5')).status).toBe(403);
  });
  it('supports optional expected lease-owner CAS fencing without accepting caller clocks', async () => {
    const f=await fixture(); const w=watchFixture({leaseOwner:'worker',leaseExpiresAt:Math.floor(Date.now()/1000)+3600}); await f.repo.create(w);
    expect(await (await f.request('/cas',{expectedVersion:1,watch:{...w,version:2},leaseOwner:'other'})).json()).toEqual({success:true,data:{updated:false}});
    expect(await (await f.request('/cas',{expectedVersion:1,watch:{...w,version:2},leaseOwner:'worker'})).json()).toEqual({success:true,data:{updated:true}});
    f.raw.exec('UPDATE aistudio_train_watches SET lease_expires_at=unixepoch()');
    expect(await (await f.request('/cas',{expectedVersion:2,watch:{...w,version:3,phase:'resolved'},leaseOwner:'worker'})).json()).toEqual({success:true,data:{updated:false}});
    for(const extra of [{leaseOwner:''},{leaseOwner:'  '},{leaseOwner:null},{leaseOwner:1},{leaseOwner:'a'.repeat(256)},{leaseOwner:'worker',now:0}]) {
      expect((await f.request('/cas',{expectedVersion:2,watch:{...w,version:3},...extra})).status).toBe(400);
    }
  });
  it('supports complete three-field due cursors and rejects partial or malformed cursors', async () => {
    const f=await fixture(); const a=watchFixture({watchId:'a',nodeId:'a'}); const b=watchFixture({watchId:'b',nodeId:'b'});
    await f.repo.create(a); await f.repo.create(b);
    const response=await f.request('/due?ownerBotId=bot%3Aowner&now=700&limit=1&afterManualPriority=1&afterNextPollAt=700&afterWatchId=a');
    expect(response.status).toBe(200); expect(await response.json()).toEqual({success:true,data:{watches:[b]}});
    for(const query of ['afterManualPriority=1','afterManualPriority=2&afterNextPollAt=700&afterWatchId=a',
      'afterManualPriority=1&afterNextPollAt=-1&afterWatchId=a','afterManualPriority=1&afterNextPollAt=700&afterWatchId='+('a'.repeat(65)),
      'afterManualPriority=1&afterNextPollAt=700&afterWatchId=a&afterWatchId=b']) {
      expect((await f.request('/due?ownerBotId=bot%3Aowner&now=700&limit=1&'+query)).status).toBe(400);
    }
  });
  it('isolates same-owner deleted runs before pagination so they cannot poison or starve the signed live batch', async () => {
    const f=await fixture(); const live=watchFixture(); await f.repo.create(live);
    for(let i=0;i<5;i++) {
      f.raw.prepare('INSERT INTO flow_runs VALUES(?,?,?,?)').run(`deleted-${i}`,'wf-1','bot:owner','waiting');
      await f.repo.create(watchFixture({watchId:`orphan-${i}`,flowId:`deleted-${i}`,manualRequestedAt:100,nextPollAt:100}));
    }
    f.raw.exec("DELETE FROM flow_runs WHERE flow_id LIKE 'deleted-%'");
    for(const limit of [1,2,100]) {
      const response=await f.request(`/due?ownerBotId=bot%3Aowner&now=700&limit=${limit}`);
      expect(response.status).toBe(200); expect(await response.json()).toEqual({success:true,data:{watches:[live]}});
    }
    expect(await f.repo.find('deleted-0','train',1)).not.toBeNull();
    f.raw.exec("DELETE FROM flow_runs WHERE flow_id='flow-1'");
    expect(await (await f.request('/due?ownerBotId=bot%3Aowner&now=700&limit=1')).json()).toEqual({success:true,data:{watches:[]}});
  });
  it('skips a run deleted between the due query and ownership verification, but still rejects mismatched owners', async () => {
    const f=await fixture(); const live=watchFixture(); await f.repo.create(live);
    const orphan=watchFixture({watchId:'deleted-race',flowId:'deleted-race'});
    vi.spyOn(f.repo,'findDue').mockResolvedValue([orphan,live]);
    const response=await f.request('/due?ownerBotId=bot%3Aowner&now=700&limit=10');
    expect(response.status).toBe(200); expect(await response.json()).toEqual({success:true,data:{watches:[live]}});
    vi.mocked(f.repo.findDue).mockResolvedValue([watchFixture({ownerBotId:'bot:other'})]);
    expect((await f.request('/due?ownerBotId=bot%3Aowner&now=700&limit=10')).status).toBe(403);
  });
  it('never leaks other owners and refuses changed-owner CAS instead of treating the row as a deleted orphan', async () => {
    const f=await fixture(); const original=watchFixture(); await f.repo.create(original);
    f.raw.exec("INSERT INTO flow_runs VALUES('foreign','wf-1','bot:other','waiting')");
    await f.repo.create(watchFixture({watchId:'foreign',flowId:'foreign',ownerBotId:'bot:other'}));
    expect(await (await f.request('/due?ownerBotId=bot%3Aowner&now=700&limit=10')).json()).toEqual({success:true,data:{watches:[original]}});
    f.raw.exec("UPDATE flow_runs SET origin_bot_id='bot:other' WHERE flow_id='flow-1'");
    expect((await f.request('/due?ownerBotId=bot%3Aowner&now=700&limit=10')).status).toBe(403);
    expect((await f.request('/cas',{expectedVersion:1,watch:{...original,version:2,phase:'resolved'}})).status).toBe(403);
    expect(await f.repo.find('flow-1','train',1)).toEqual(original);
  });
  it('validates every payload and query and refuses DB failures without a success ack', async () => {
    const f=await fixture();
    for(const path of ['/find?flowId=flow-1&nodeId=train&attempt=1x','/due?ownerBotId=bot%3Aowner&now=700&limit=0','/due?ownerBotId=bot&now=700&limit=2']) expect((await f.request(path)).status).toBe(400);
    expect((await f.request('/create',{watch:{...watchFixture(),leaseOwner:7}})).status).toBe(400);
    expect((await f.request('/create',{watch:watchFixture(),sql:'DROP TABLE flow_runs'})).status).toBe(400);
    expect((await f.request('/cas',{expectedVersion:1,watch:watchFixture({version:3})})).status).toBe(400);
    vi.spyOn(f.repo,'create').mockRejectedValue(new Error('private SQL secret'));
    const failed=await f.request('/create',{watch:watchFixture()}); expect(failed.status).toBe(503); expect(await failed.text()).not.toContain('private');
    f.raw.exec('DROP TABLE aistudio_train_watches'); expect((await f.request('/find?flowId=flow-1&nodeId=train')).status).toBe(503);
  });
});
