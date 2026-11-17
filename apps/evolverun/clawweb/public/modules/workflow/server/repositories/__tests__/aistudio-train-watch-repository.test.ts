import Database from 'better-sqlite3';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mysqlDialect } from '@avernet/clawweb-shared/server/db/dialect';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { aistudioTrainDdl } from '@avernet/clawweb-shared/server/schema-aistudio-train';
import { AIStudioTrainWatchRepository } from '../aistudio-train-watch-repository.js';
import { watchFixture } from '../../contracts/__tests__/aistudio-train.fixture.js';

const connections: Database.Database[] = [];
export function fixture() {
  const raw = new Database(':memory:'); connections.push(raw);
  for (const ddl of aistudioTrainDdl('sqlite')) raw.exec(ddl);
  raw.exec("CREATE TABLE flow_runs(flow_id TEXT,workflow_id TEXT,origin_bot_id TEXT,status TEXT); INSERT INTO flow_runs VALUES('flow-1','wf-1','bot:owner','waiting')");
  const db = new SqliteDatabase(raw); const repo = new AIStudioTrainWatchRepository(db);
  return { raw, db, repo };
}
afterEach(() => { for (const raw of connections.splice(0)) raw.close(); });
describe('durable watch SQL contract', () => {
  it('roundtrips all fields, selects latest attempt, and rejects NoOp', async () => {
    const { repo } = fixture(); const w = watchFixture();
    expect(await repo.create(w)).toEqual(w); expect(await repo.find(w.flowId,w.nodeId,1)).toEqual(w);
    const newer = watchFixture({ watchId:'watch-2', attempt:2 }); await repo.create(newer);
    expect(await repo.find(w.flowId,w.nodeId)).toEqual(newer); expect(await repo.find('missing','n')).toBeNull();
    expect(() => new AIStudioTrainWatchRepository({ dbType:'noop' } as any)).toThrow(/durable/i);
  });
  it('reuses only a unique-conflict row for same flow/node/attempt and record, never swallows other errors', async () => {
    const { db, repo } = fixture(); const w = watchFixture(); await repo.create(w);
    expect(await repo.create({ ...w, watchId:'new-id' })).toEqual(w);
    await expect(repo.create({ ...w, watchId:'other', recordId:'other' })).rejects.toThrow();
    await expect(repo.create({ ...w, ownerBotId:'bot:other' })).rejects.toThrow();
    await expect(repo.create({ ...w, nodeId:'other' })).rejects.toThrow();
    vi.spyOn(db, 'exec').mockRejectedValue(new Error('disk write failed'));
    await expect(repo.create(w)).rejects.toThrow('disk write failed');
  });
  it('only one CAS wins; immutable identity and stale lease-holder writes are fenced', async () => {
    const { repo } = fixture(); const w = await repo.create(watchFixture());
    const first = { ...w, version:2, leaseOwner:'one', leaseExpiresAt:1000 };
    expect((await Promise.all([repo.compareAndSwap(1,first),repo.compareAndSwap(1,{ ...first, leaseOwner:'two' })])).filter(Boolean)).toHaveLength(1);
    const current = (await repo.find(w.flowId,w.nodeId))!;
    expect(await repo.compareAndSwap(2,{ ...current, version:3, recordId:'forged' })).toBe(false);
    expect(await repo.compareAndSwap(1,{ ...w, version:2, phase:'resolved' })).toBe(false);
    await expect(repo.compareAndSwap(2,{ ...current, version:4 })).rejects.toThrow(/version/);
  });
  it('finds owner-scoped due, timeout and manual requests but not leased, terminal, resolving or canceled', async () => {
    const { repo } = fixture();
    for (const [id, extra] of Object.entries({
      due:{}, timeout:{ firstPollAt:900, nextPollAt:900, timeoutAt:500 },
      manual:{ firstPollAt:900, nextPollAt:900, manualRequestedAt:600, manualRequestedBy:'owner' },
      later:{ firstPollAt:900, nextPollAt:900 }, other:{ ownerBotId:'bot:other' },
      leased:{ leaseOwner:'worker', leaseExpiresAt:800 }, resolved:{ phase:'resolved' },
      resolving:{ phase:'resolving' }, canceled:{ canceled:true },
    })) await repo.create(watchFixture({ ...extra, watchId:id, nodeId:id } as any));
    expect((await repo.findDue('bot:owner',700,10)).map(w => w.watchId)).toEqual(['manual','due','timeout']);
    expect((await repo.findDue('bot:owner',700,1)).map(w => w.watchId)).toEqual(['manual']);
    await expect(repo.findDue('bot',700,10)).rejects.toThrow();
    await expect(repo.findDue('bot:owner',700,0)).rejects.toThrow();
  });
  it('checks expected lease owner and the database clock atomically, including lease expiry at actual UPDATE execution', async () => {
    const {repo,raw,db}=fixture(); const future=Math.floor(Date.now()/1000)+3600;
    const w=await repo.create(watchFixture({leaseOwner:'worker-A',leaseExpiresAt:future}));
    expect(await repo.compareAndSwap(1,{...w,version:2,lastStatus:'running'},'worker-a')).toBe(false);
    expect(await repo.compareAndSwap(1,{...w,version:2,lastStatus:'running'},'worker-A')).toBe(true);
    const current=(await repo.find(w.flowId,w.nodeId,1))!;
    const exec=db.exec.bind(db);
    vi.spyOn(db,'exec').mockImplementation(async (sql,params) => {
      raw.exec('UPDATE aistudio_train_watches SET lease_expires_at=unixepoch()-1');
      return exec(sql,params);
    });
    expect(await repo.compareAndSwap(2,{...current,version:3,phase:'resolving'},'worker-A')).toBe(false);
    expect((await repo.find(w.flowId,w.nodeId,1))!.version).toBe(2);
    // Normal release/claim CAS remains backward compatible and does not require a live lease.
    expect(await repo.compareAndSwap(2,{...current,version:3,leaseOwner:null,leaseExpiresAt:null})).toBe(true);
  });
  it('renders MySQL lease fencing with database time and binary expected owner; rejects malformed guards', async () => {
    const exec=vi.fn(async (_sql:string,_params?:unknown[]) => ({affectedRows:1}));
    const repo=new AIStudioTrainWatchRepository({dbType:'mysql',dialect:mysqlDialect,exec} as any);
    const w=watchFixture({version:2});
    expect(await repo.compareAndSwap(1,w,'worker')).toBe(true);
    expect(exec.mock.calls[0][0]).toContain('AND BINARY lease_owner = BINARY ? AND lease_expires_at > UNIX_TIMESTAMP()');
    expect(exec.mock.calls[0][1]!.at(-1)).toBe('worker');
    for(const owner of ['', '   ', 'a'.repeat(256), 1, null]) await expect(repo.compareAndSwap(1,w,owner as any)).rejects.toThrow();
  });
  it('paginates due watches by strict stable keyset without losing manual-priority or equal-time rows', async () => {
    const {repo}=fixture();
    const rows=[watchFixture({watchId:'manual-a',nodeId:'ma',manualRequestedAt:100,nextPollAt:900}),
      watchFixture({watchId:'manual-b',nodeId:'mb',manualRequestedAt:100,nextPollAt:900}),
      watchFixture({watchId:'auto-a',nodeId:'aa'}),watchFixture({watchId:'auto-b',nodeId:'ab'})];
    for(const row of rows) await repo.create(row);
    const seen:string[]=[]; let after:any=undefined;
    for(let i=0;i<5;i++) {
      const page=await repo.findDue('bot:owner',700,1,after); if(!page.length) break;
      const w=page[0]; seen.push(w.watchId); after={manualPriority:w.manualRequestedAt===null?1:0,nextPollAt:w.nextPollAt,watchId:w.watchId};
    }
    expect(seen).toEqual(['manual-a','manual-b','auto-a','auto-b']);
    for(const cursor of [{manualPriority:2,nextPollAt:700,watchId:'x'}, {manualPriority:0,nextPollAt:-1,watchId:'x'},
      {manualPriority:0,nextPollAt:700,watchId:'x'.repeat(65)}]) await expect(repo.findDue('bot:owner',700,1,cursor as any)).rejects.toThrow();
  });
  it('filters deleted run orphans before ordering and limit so manual-priority orphans cannot starve live watches', async () => {
    const {repo}=fixture(); const live=watchFixture(); await repo.create(live);
    for(let i=0;i<5;i++) await repo.create(watchFixture({watchId:`orphan-${i}`,flowId:`deleted-${i}`,manualRequestedAt:100,nextPollAt:100}));
    expect(await repo.findDue('bot:owner',700,1)).toEqual([live]);
    expect(await repo.findDue('bot:owner',700,100)).toEqual([live]);
    expect(await repo.find('deleted-0','train',1)).not.toBeNull();
  });
  it('uses a case-sensitive stored flow association on MySQL before applying the due limit', async () => {
    const query=vi.fn(async (_sql:string,_params?:unknown[]) => []);
    const repo=new AIStudioTrainWatchRepository({dbType:'mysql',dialect:mysqlDialect,query} as any);
    expect(await repo.findDue('bot:owner',700,1)).toEqual([]);
    expect(query.mock.calls[0][0]).toContain('BINARY r.flow_id = BINARY aistudio_train_watches.flow_id');
  });
  it('uses case-sensitive atomic target ownership checks even when legacy MySQL run tables use CI collations', async () => {
    const exec=vi.fn(async (_sql:string,_params?:unknown[]) => ({affectedRows:1}));
    const repo=new AIStudioTrainWatchRepository({dbType:'mysql',dialect:mysqlDialect,exec} as any);
    const w=watchFixture({manualRequestedAt:200,manualRequestedBy:'owner',lastManualRequestedAt:200,version:2});
    expect(await repo.requestManualPoll(1,w)).toBe(true);
    expect(exec.mock.calls[0][0]).toContain('BINARY r.origin_bot_id = BINARY aistudio_train_watches.owner_bot_id');
  });
  it('refuses a partial managed schema that is missing required audit columns', async () => {
    const {repo,raw}=fixture(); raw.exec('ALTER TABLE aistudio_train_watches DROP COLUMN gmt_modified');
    expect(await repo.isReady()).toBe(false);
  });
  it('reports readiness from actual columns and propagates failures', async () => {
    const { repo, raw } = fixture(); expect(await repo.isReady()).toBe(true);
    raw.exec('DROP TABLE aistudio_train_watches'); expect(await repo.isReady()).toBe(false);
    await expect(repo.create(watchFixture())).rejects.toThrow(/no such table/);
  });
});
