import Database from 'better-sqlite3';
import express from 'express';
import { once } from 'node:events';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { createAIStudioStateFixture, patchWatchFixture } from '../../repositories/__tests__/aistudio-train-state.fixture.js';
import { AIStudioTrainWatchRepository } from '../../repositories/aistudio-train-watch-repository.js';
import { createAIStudioTrainRouter, type AIStudioTrainPublicAuth } from '../aistudio-train.js';
import { watchFixture } from '../../contracts/__tests__/aistudio-train.fixture.js';
const cleanups: (() => Promise<void>)[] = [];
afterEach(async () => { for (const fn of cleanups.splice(0)) await fn(); });
async function fixture(authConfigured = true, originConfigured = true, projection: { resolveDetailUrl?: (recordId: string) => string | null } = {}) {
  const raw = new Database(':memory:'); createAIStudioStateFixture(raw);
  raw.exec(`CREATE TABLE flow_runs(flow_id TEXT,workflow_id TEXT,origin_bot_id TEXT,status TEXT);
    INSERT INTO flow_runs VALUES('flow-1','wf-1','bot:owner','waiting');
    CREATE TABLE node_executions(id INTEGER PRIMARY KEY,flow_id TEXT,node_id TEXT,attempt INTEGER,executor_type TEXT,status TEXT);
    INSERT INTO node_executions VALUES(1,'flow-1','train',1,'aistudio-train','waiting')`);
  const repo=new AIStudioTrainWatchRepository(new SqliteDatabase(raw)); await repo.create(watchFixture());
  const auth: AIStudioTrainPublicAuth = {
    resolveVerifiedPrincipal:vi.fn(async () => ({actorId:'owner'})),
    authorizeRunRead:vi.fn(async () => true),
    authorizeOwnerOperation:vi.fn(async () => true),
    authorizeWorkflowExecute:vi.fn(async () => true),
  };
  const app=express(); app.use(express.json());
  app.use('/api/runs',createAIStudioTrainRouter({store:repo,findRunTarget:id => repo.findRunTarget(id),findNodeTarget:(f,n) => repo.findNodeTarget(f,n),now:() => 200,publicOrigin:originConfigured ? 'https://clawweb.example.org' : undefined,...(authConfigured ? auth : {}),...projection}));
  const server=app.listen(0,'127.0.0.1'); await once(server,'listening');
  cleanups.push(async () => { await new Promise<void>(resolve => server.close(() => resolve())); raw.close(); });
  const base=`http://127.0.0.1:${(server.address() as {port:number}).port}/api/runs/flow-1/nodes/train/aistudio-train`;
  const get=(query='?attempt=1') => fetch(base+query,{headers:{'x-user-id':'forged',cookie:'user=forged'}});
  const post=(body:unknown={attempt:1}, headers:Record<string,string>={}) => fetch(base+'/poll-manual',{method:'POST',headers:{'content-type':'application/json','x-user-id':'forged',origin:'https://clawweb.example.org',...headers},body:JSON.stringify(body)});
  return {raw,repo,auth,get,post};
}
describe('verified public AIStudio watch and mailbox', () => {
  it('defaults to 503 with no IAM verifier, and rejects forged identity when verifier returns null', async () => {
    const f=await fixture(false); expect((await f.get()).status).toBe(503); expect((await f.post()).status).toBe(503);
    const g=await fixture(); vi.mocked(g.auth.resolveVerifiedPrincipal!).mockResolvedValue(null);
    expect((await g.get()).status).toBe(401); expect((await g.post()).status).toBe(401);
  });
  it('requires configured trusted Origin and rejects absent/cross-site/spoofed-Host writes', async () => {
    const f=await fixture();
    for (const headers of [{origin:''},{origin:'https://evil.example.org'}, {'sec-fetch-site':'cross-site'},
      {origin:'https://evil.example.org',host:'evil.example.org','x-forwarded-host':'evil.example.org'}]) {
      expect((await f.post({attempt:1},headers)).status).toBe(403);
    }
    expect((await f.repo.find('flow-1','train',1))!.manualRequestedAt).toBeNull();
    const disabled=await fixture(true,false); expect((await disabled.post()).status).toBe(503);
  });
  it('reads server run ownership and calls ACL with exact bot:owner, never requester-selected scope', async () => {
    const f=await fixture(); const response=await f.get(); expect(response.status).toBe(200);
    expect(f.auth.authorizeRunRead).toHaveBeenCalledWith({actorId:'owner'},{flowId:'flow-1',workflowId:'wf-1',ownerBotId:'bot:owner',status:'waiting'});
    const {watch}=await response.json(); expect(watch.version).toBe(1); expect(watch.pollInterval).toBe(60); expect(watch.manualPollAllowed).toBe(true);
    expect(Object.keys(watch)).not.toEqual(expect.arrayContaining(['ownerBotId','leaseOwner','workflowId']));
    expect((await f.get('?attempt=1&ownerBotId=bot:other')).status).toBe(400);
  });
  it('denies read and exact owner operations independently of workflow execute', async () => {
    const f=await fixture(); vi.mocked(f.auth.authorizeRunRead!).mockResolvedValue(false);
    expect((await f.get()).status).toBe(403); expect((await f.post()).status).toBe(403);
    vi.mocked(f.auth.authorizeRunRead!).mockResolvedValue(true);
    vi.mocked(f.auth.authorizeOwnerOperation!).mockResolvedValue(false);
    expect((await f.post()).status).toBe(403); expect((await (await f.get()).json()).watch.manualPollAllowed).toBe(false);
    vi.mocked(f.auth.authorizeOwnerOperation!).mockResolvedValue(true); vi.mocked(f.auth.authorizeWorkflowExecute!).mockResolvedValue(false);
    expect((await f.post()).status).toBe(403);
  });
  it('refuses same bot ID with a different owner, missing owner, wrong node type, old attempt and missing watch', async () => {
    const f=await fixture(); f.raw.exec("UPDATE flow_runs SET origin_bot_id='bot:other'");
    expect((await f.get()).status).toBe(403); expect((await f.post()).status).toBe(403);
    f.raw.exec('UPDATE flow_runs SET origin_bot_id=NULL'); expect((await f.get()).status).toBe(503);
    f.raw.exec("UPDATE flow_runs SET origin_bot_id='bot:owner'; UPDATE node_executions SET executor_type='script'"); expect((await f.get()).status).toBe(409);
    f.raw.exec("UPDATE node_executions SET executor_type='aistudio-train',attempt=2"); expect((await f.post()).status).toBe(409);
    f.raw.exec("DELETE FROM cm_app_config WHERE config_key LIKE 'aisw:%'"); expect((await f.get('?attempt=2')).status).toBe(404);
  });
  it.each([0,-1,1.5,'1',null])('requires positive integer body attempt %j', async attempt => {
    const f=await fixture(); expect((await f.post({attempt})).status).toBe(400);
    if (attempt !== '1') expect((await f.get(`?attempt=${encodeURIComponent(String(attempt))}`)).status).toBe(400);
  });
  it('rejects extra target fields and malformed GET attempts', async () => {
    const f=await fixture(); expect((await f.post({attempt:1,recordId:'forged'})).status).toBe(400);
    expect((await f.get('?attempt=1abc')).status).toBe(400); expect((await f.get('?attempt=1&attempt=2')).status).toBe(400);
  });
  it('queues only mailbox fields, does not query AIS, and only one concurrent request is accepted', async () => {
    const f=await fixture(); const before=(await f.repo.find('flow-1','train',1))!;
    const results=await Promise.all([f.post(),f.post()]); expect(results.map(r => r.status).sort()).toEqual([202,409]);
    const accepted=results.find(r => r.status===202)!; const body=await accepted.json();
    expect(body.watch.version).toBe(2); expect(body.ok).toBe(true); expect(body.resolved).toBe(false); expect(body.watch.manualPollPending).toBe(true);
    expect(await f.repo.find('flow-1','train',1)).toEqual({...before,manualRequestedAt:200,manualRequestedBy:'owner',lastManualRequestedAt:200,version:2});
  });
  it('enforces durable 60 second cooldown after pending was consumed, with Retry-After', async () => {
    const f=await fixture(); patchWatchFixture(f.raw,{lastManualRequestedAt:199});
    const res=await f.post(); expect(res.status).toBe(429); expect(res.headers.get('retry-after')).toBe('59');
    expect((await res.json()).manualPollRetryAt).toBe(new Date(259000).toISOString());
  });
  it('does not permit terminal/resolving/canceled/time-expired watches or terminal node/run operations', async () => {
    const f=await fixture();
    for (const update of [{phase:'resolved'},{phase:'resolving'},{canceled:true},{timeoutAt:199}]) {
      patchWatchFixture(f.raw,{phase:'waiting',canceled:false,timeoutAt:3700,...update});
      expect((await f.post()).status).toBe(409);
    }
    patchWatchFixture(f.raw,{phase:'resolved'});
    expect((await f.get()).status).toBe(200); expect((await (await f.get()).json()).watch.nextPollAt).toBeNull();
    patchWatchFixture(f.raw,{phase:'waiting',canceled:false,timeoutAt:3700}); f.raw.exec("UPDATE node_executions SET status='success'");
    expect((await f.post()).status).toBe(409);
  });
  it('propagates writes/readiness failure as 503, not 202, and redacts SQL details', async () => {
    const f=await fixture(); vi.spyOn(f.repo,'requestManualPoll').mockRejectedValue(new Error('secret SQL token'));
    const res=await f.post(); expect(res.status).toBe(503); expect(await res.text()).not.toContain('secret');
    f.raw.exec('DROP TABLE cm_app_config'); expect((await f.get()).status).toBe(503);
  });
  it('fences a concurrent newer node attempt at the atomic mailbox update', async () => {
    const f=await fixture(); const before=(await f.repo.find('flow-1','train',1))!;
    f.raw.exec("INSERT INTO node_executions VALUES(2,'flow-1','train',2,'aistudio-train','waiting')");
    expect(await f.repo.requestManualPoll(1,{...before,version:2,manualRequestedAt:200,manualRequestedBy:'owner',lastManualRequestedAt:200})).toBe(false);
    expect(await f.repo.find('flow-1','train',1)).toEqual(before);
  });
});


describe('host-owned response-only task detail URL projection', () => {
  it.each(['GET', 'POST'])('uses the canonical resolver and provider encoding for %s without persisting the presentation URL', async method => {
    const resolveDetailUrl=vi.fn((recordId: string) => `https://aistudio.example.org/record/detail/${encodeURIComponent(recordId)}`);
    const f=await fixture(true,true,{resolveDetailUrl});
    patchWatchFixture(f.raw,{recordId:'record:42',detailUrl:'https://example.org/logs/record-42'});
    const before=(await f.repo.find('flow-1','train',1))!;
    const beforeRows=f.raw.prepare('SELECT * FROM cm_app_config').all();
    const res=await (method === 'GET' ? f.get() : f.post());
    expect(res.status).toBe(method === 'GET' ? 200 : 202);
    const {watch}=await res.json();
    expect(resolveDetailUrl).toHaveBeenCalledExactlyOnceWith('record:42');
    expect(watch.detailUrl).toBe('https://aistudio.example.org/record/detail/record%3A42');
    expect(watch.version).toBe(method === 'GET' ? before.version : before.version+1);
    expect(await f.repo.find('flow-1','train',1)).toEqual(method === 'GET' ? before : {
      ...before,manualRequestedAt:200,manualRequestedBy:'owner',lastManualRequestedAt:200,version:before.version+1,
    });
    if (method === 'GET') expect(f.raw.prepare('SELECT * FROM cm_app_config').all()).toEqual(beforeRows);
  });
  it.each([null,'https://example.org/logs/record-42'])('preserves recorded safe URL/null without a provider (%s)', async detailUrl => {
    const f=await fixture(); patchWatchFixture(f.raw,{detailUrl});
    const before=(await f.repo.find('flow-1','train',1))!;
    expect((await (await f.get()).json()).watch.detailUrl).toBe(detailUrl);
    const accepted=await f.post(); expect(accepted.status).toBe(202);
    expect((await accepted.json()).watch.detailUrl).toBe(detailUrl);
    expect((await f.repo.find('flow-1','train',1))!.detailUrl).toBe(before.detailUrl);
  });
  it('projects a canonical URL even when the recorded watch has no URL', async () => {
    const f=await fixture(true,true,{resolveDetailUrl:recordId => `https://aistudio.example.org/detail/${recordId}`});
    patchWatchFixture(f.raw,{detailUrl:null});
    expect((await (await f.get()).json()).watch.detailUrl).toBe('https://aistudio.example.org/detail/record-1');
    const accepted=await f.post(); expect(accepted.status).toBe(202);
    expect((await accepted.json()).watch.detailUrl).toBe('https://aistudio.example.org/detail/record-1');
    expect((await f.repo.find('flow-1','train',1))!.detailUrl).toBeNull();
  });
  it.each([null,'javascript:alert(1)','http://example.org/detail/42','https://user:pass@example.org/detail/42',
    'https://example.org/detail/42?token=secret','https://example.org/detail/42#secret','https://127.0.0.1/detail/42',
    'https://example.internal/detail/42','https://example.org:8443/detail/42','//example.org/detail/42'])('treats a supplied provider as authoritative and sanitizes %s for both responses', async detailUrl => {
    const resolveDetailUrl=vi.fn(() => detailUrl);
    const f=await fixture(true,true,{resolveDetailUrl});
    const before=(await f.repo.find('flow-1','train',1))!;
    const read=await f.get(); expect(read.status).toBe(200); expect((await read.json()).watch.detailUrl).toBeNull();
    expect(await f.repo.find('flow-1','train',1)).toEqual(before);
    const accepted=await f.post(); expect(accepted.status).toBe(202); expect((await accepted.json()).watch.detailUrl).toBeNull();
    expect(resolveDetailUrl).toHaveBeenCalledTimes(2);
    expect((await f.repo.find('flow-1','train',1))!.detailUrl).toBe(before.detailUrl);
  });
});
