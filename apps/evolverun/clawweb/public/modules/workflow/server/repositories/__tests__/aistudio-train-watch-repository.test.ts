import Database from 'better-sqlite3';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dialectFor, mysqlDialect } from '@avernet/clawweb-shared/server/db/dialect';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { createAIStudioStateFixture } from './aistudio-train-state.fixture.js';
import { AIStudioTrainWatchRepository } from '../aistudio-train-watch-repository.js';
import { watchFixture } from '../../contracts/__tests__/aistudio-train.fixture.js';

function stateRow(w:ReturnType<typeof watchFixture>) {
  return {config_key:'aisw:oxltucqye7ieocpvlq3grsnfqlc5dlsle2dtkzaprqmrftqgywqa',config_yaml:JSON.stringify(w),version:w.version,enabled:0,description:'aistudio-train-watch:v1:active:bot:owner'};
}
const connections: Database.Database[] = [];
export function fixture() {
  const raw = new Database(':memory:'); connections.push(raw);
  createAIStudioStateFixture(raw);
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
    const transaction=db.transaction.bind(db);
    vi.spyOn(db,'transaction').mockImplementation(fn => transaction(tx => {
      vi.spyOn(tx,'exec').mockRejectedValue(new Error('disk write failed')); return fn(tx);
    }));
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
    const {repo,raw,db}=fixture(); let databaseNow=1000; raw.function('unixepoch',() => databaseNow);
    const w=await repo.create(watchFixture({leaseOwner:'worker-A',leaseExpiresAt:1001}));
    expect(await repo.compareAndSwap(1,{...w,version:2,lastStatus:'running'},'worker-a')).toBe(false);
    expect(await repo.compareAndSwap(1,{...w,version:2,lastStatus:'running'},'worker-A')).toBe(true);
    const current=(await repo.find(w.flowId,w.nodeId,1))!;
    const transaction=db.transaction.bind(db);
    vi.spyOn(db,'transaction').mockImplementationOnce(fn => transaction(tx => {
      const exec=tx.exec.bind(tx);
      vi.spyOn(tx,'exec').mockImplementationOnce((sql,params) => {
        databaseNow=1001; return exec(sql,params);
      });
      return fn(tx);
    }));
    expect(await repo.compareAndSwap(2,{...current,version:3,phase:'resolving'},'worker-A')).toBe(false);
    expect(databaseNow).toBe(current.leaseExpiresAt);
    expect(await repo.find(w.flowId,w.nodeId,1)).toEqual(current);
    // Normal release/claim CAS remains backward compatible and does not require a live lease.
    expect(await repo.compareAndSwap(2,{...current,version:3,leaseOwner:null,leaseExpiresAt:null})).toBe(true);
  });
  it.each(['mysql','zdas'] as const)('renders %s lease fencing with database time and binary expected owner; rejects malformed guards', async dbType => {
    const exec=vi.fn(async (_sql:string,_params?:unknown[]) => ({affectedRows:1}));
    const old=watchFixture({leaseOwner:'worker',leaseExpiresAt:Math.floor(Date.now()/1000)+3600});
    const fake:any={dbType,dialect:dialectFor(dbType),exec,query:vi.fn(async (sql:string) => sql.includes("information_schema.STATISTICS")
        ? [{index_name:"config_key",column_name:"config_key",prefix_length:null}] : [stateRow(old)]),transaction:async (fn:any) => fn(fake)};
    const repo=new AIStudioTrainWatchRepository(fake);
    const w=watchFixture({version:2});
    expect(await repo.compareAndSwap(1,w,'worker')).toBe(true);
    expect(exec.mock.calls[0][0]).toContain('> UNIX_TIMESTAMP()');
    expect(fake.query.mock.calls.some(([sql]:[string]) => sql.includes('FOR UPDATE'))).toBe(true);
    expect(exec.mock.calls[0][1]).toContain(old.leaseExpiresAt);
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
    expect(query.mock.calls[0][0]).toContain("LIKE 'aisw:%'");
    expect(query.mock.calls[0][0]).not.toMatch(/LIMIT/i);
  });
  it('uses case-sensitive atomic target ownership checks even when legacy MySQL run tables use CI collations', async () => {
    const exec=vi.fn(async (_sql:string,_params?:unknown[]) => ({affectedRows:1}));
    const old=watchFixture(); const fake:any={dbType:'mysql',dialect:mysqlDialect,exec,
      query:vi.fn(async (sql:string) => sql.includes("information_schema.STATISTICS")
        ? [{index_name:"config_key",column_name:"config_key",prefix_length:null}] : [stateRow(old)]),transaction:async (fn:any) => fn(fake)};
    const repo=new AIStudioTrainWatchRepository(fake);
    const w=watchFixture({manualRequestedAt:200,manualRequestedBy:'owner',lastManualRequestedAt:200,version:2});
    expect(await repo.requestManualPoll(1,w)).toBe(true);
    expect(exec.mock.calls[0][0]).toContain('BINARY r.origin_bot_id = BINARY ?');
  });
  it('refuses a partial managed schema that is missing required audit columns', async () => {
    const {repo,raw}=fixture(); raw.exec('ALTER TABLE cm_app_config DROP COLUMN gmt_modified');
    expect(await repo.isReady()).toBe(false);
  });
  it('reports readiness from actual columns and propagates failures', async () => {
    const { repo, raw } = fixture(); expect(await repo.isReady()).toBe(true);
    raw.exec('DROP TABLE cm_app_config'); expect(await repo.isReady()).toBe(false);
    await expect(repo.create(watchFixture())).rejects.toThrow(/no such table/);
  });
  it('stores full-hash 57-character keys in disabled v71 rows and survives repository restart', async () => {
    const {repo,raw,db}=fixture(); const w=await repo.create(watchFixture());
    const row=raw.prepare('SELECT * FROM cm_app_config').get() as any;
    expect(row.config_key).toBe('aisw:oxltucqye7ieocpvlq3grsnfqlc5dlsle2dtkzaprqmrftqgywqa');
    expect(row.config_key).toHaveLength(57); expect(row.enabled).toBe(0);
    expect(row.description).toBe('aistudio-train-watch:v1:active:bot:owner'); expect(JSON.parse(row.config_yaml)).toEqual(w);
    expect(await new AIStudioTrainWatchRepository(db).find(w.flowId,w.nodeId,w.attempt)).toEqual(w);
    expect(raw.prepare("SELECT name FROM sqlite_master WHERE name LIKE 'aistudio_train_%'").all()).toEqual([]);
  });
  it('fails closed on malformed JSON, wrong discriminator, key identity or row/payload version mismatch', async () => {
    for(const patch of [{config_yaml:'{'},{description:'other-owner'}, {enabled:1}, {version:2},
      {config_yaml:JSON.stringify(watchFixture({flowId:'other'}))}]) {
      const {repo,raw}=fixture(); await repo.create(watchFixture());
      for(const [key,value] of Object.entries(patch)) raw.prepare(`UPDATE cm_app_config SET ${key}=?`).run(value);
      await expect(repo.find('flow-1','train',1)).rejects.toThrow();
      await expect(repo.findDue('bot:owner',700,1)).rejects.toThrow();
      await expect(repo.compareAndSwap(1,watchFixture({version:2}))).rejects.toThrow();
    }
  });
  it('filters other owners before page limit, even with more than 1000 earlier mailbox rows', async () => {
    const {repo,raw}=fixture(); const live=watchFixture(); await repo.create(live);
    // Insert real valid other-owner records without O(n²) repository collision scans.
    const {createHash}=await import('node:crypto');
    const alphabet='abcdefghijklmnopqrstuvwxyz234567';
    for(let i=0;i<1001;i++) {
      const w=watchFixture({watchId:`other-${i}`,nodeId:`other-${i}`,ownerBotId:'bot:other',manualRequestedAt:1});
      let bits=0,value=0,hash='';
      for(const byte of createHash('sha256').update(JSON.stringify([w.flowId,w.nodeId,w.attempt])).digest()) {
        value=(value<<8)|byte; bits+=8; while(bits>=5) {bits-=5;hash+=alphabet[(value>>>bits)&31];}
      }
      if(bits) hash+=alphabet[(value<<(5-bits))&31];
      raw.prepare('INSERT INTO cm_app_config(config_key,config_yaml,version,enabled,description) VALUES(?,?,1,0,?)')
        .run('aisw:'+hash,JSON.stringify(w),'aistudio-train-watch:v1:active:bot:other');
    }
    expect(await repo.findDue('bot:owner',700,1)).toEqual([live]);
  });
  it('manual requests preserve all non-mailbox fields and fence cooldown, exact owner and latest attempt/type/status', async () => {
    const {repo,raw}=fixture(); raw.exec(`CREATE TABLE node_executions(id INTEGER PRIMARY KEY,flow_id TEXT,node_id TEXT,attempt INTEGER,executor_type TEXT,status TEXT);
      INSERT INTO node_executions VALUES(1,'flow-1','train',1,'aistudio-train','waiting')`);
    const old=await repo.create(watchFixture());
    const next={...old,version:2,manualRequestedAt:200,lastManualRequestedAt:200,manualRequestedBy:'owner',lastStatus:'forged'};
    raw.exec("UPDATE flow_runs SET origin_bot_id='bot:Owner'"); expect(await repo.requestManualPoll(1,next)).toBe(false);
    raw.exec("UPDATE flow_runs SET origin_bot_id='bot:owner'; INSERT INTO node_executions VALUES(2,'flow-1','train',1,'script','waiting')");
    expect(await repo.requestManualPoll(1,next)).toBe(false);
    raw.exec('DELETE FROM node_executions WHERE id=2'); expect(await repo.requestManualPoll(1,next)).toBe(true);
    const stored=(await repo.find('flow-1','train',1))!;
    expect(stored).toEqual({...old,version:2,manualRequestedAt:200,lastManualRequestedAt:200,manualRequestedBy:'owner'});
    expect(await repo.compareAndSwap(2,{...stored,version:3,manualRequestedAt:null,manualRequestedBy:null})).toBe(true);
    expect(await repo.requestManualPoll(3,{...next,version:4,manualRequestedAt:259,lastManualRequestedAt:259})).toBe(false);
    expect(await repo.requestManualPoll(3,{...next,version:4,manualRequestedAt:260,lastManualRequestedAt:260})).toBe(true);
  });

  it('hydrates only this owner active JSON during due scans and atomically retires terminal metadata', async () => {
    const {repo,raw,db}=fixture(); const live=await repo.create(watchFixture());
    const terminal=await repo.create(watchFixture({watchId:'terminal',nodeId:'terminal',phase:'resolved'}));
    await repo.create(watchFixture({watchId:'foreign',nodeId:'foreign',ownerBotId:'bot:other'}));
    const terminalKey=raw.prepare("SELECT config_key FROM cm_app_config WHERE description LIKE '%:inactive:%'").get() as any;
    expect(terminalKey).toBeTruthy();
    // Cold rows are not hydrated/parsed on the hot polling path. Direct reads still fail closed.
    raw.prepare('UPDATE cm_app_config SET config_yaml=? WHERE config_key=?').run('{',terminalKey.config_key);
    const query=vi.spyOn(db,'query'); expect(await repo.findDue('bot:owner',700,1)).toEqual([live]);
    const payloadQueries=query.mock.calls.filter(([sql]) => sql.includes('config_yaml'));
    expect(payloadQueries).toHaveLength(1); expect(payloadQueries[0][0]).toContain('description');
    expect(payloadQueries[0][1]).toEqual(['aistudio-train-watch:v1:active:bot:owner']);
    await expect(repo.find(terminal.flowId,terminal.nodeId,terminal.attempt)).rejects.toThrow();
    raw.prepare('DELETE FROM cm_app_config WHERE config_key=?').run(terminalKey.config_key);
    expect(await repo.compareAndSwap(1,{...live,phase:'resolved',version:2})).toBe(true);
    expect(await repo.findDue('bot:owner',700,1)).toEqual([]);
    const retired=raw.prepare("SELECT version,description FROM cm_app_config WHERE description LIKE '%:inactive:%'").get();
    expect(retired).toEqual({version:2,description:'aistudio-train-watch:v1:inactive:bot:owner'});
  });

  it('uses the old lease expiry at SQL execution time, never a renewed next-payload lease or JS clock', async () => {
    const {repo,raw,db}=fixture(); let databaseNow=1000; raw.function('unixepoch',() => databaseNow);
    const w=await repo.create(watchFixture({leaseOwner:'worker',leaseExpiresAt:1001}));
    const transaction=db.transaction.bind(db);
    vi.spyOn(db,'transaction').mockImplementation(fn => transaction(tx => {
      const exec=tx.exec.bind(tx);
      vi.spyOn(tx,'exec').mockImplementation((sql,params) => {databaseNow=1001;return exec(sql,params);});
      return fn(tx);
    }));
    expect(await repo.compareAndSwap(1,{...w,version:2,leaseExpiresAt:9999},'worker')).toBe(false);
    expect(await repo.find(w.flowId,w.nodeId,1)).toEqual(w);
  });

});
