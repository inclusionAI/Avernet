import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { CallbackCompensationService } from '../service.js';
import type { CompensationTransport, EvidenceQuery } from '../types.js';
import { key } from './fixture.js';
import { CallbackCompensationSource } from '../../../repositories/callback-compensation-source.js';
import { fixture, NOW } from './fixture.js';
import { fileURLToPath } from 'node:url';
import Database from 'better-sqlite3';
import { callbackCompensationDdl, callbackCompensationMigrations } from '@avernet/clawweb-shared/server/schema-callback-compensation';
import { collectMigrations } from '@avernet/clawweb-shared/server/db/migrations-registry';
import { mysqlDialect, sqliteDialect, zdasDialect } from '@avernet/clawweb-shared/server/db/dialect';

it('v159 registered exactly once after distributed locks v158; no existing version collision',()=>{
  const migrations=collectMigrations();const versions=migrations.map(m=>m.version);
  expect(versions.filter(v=>v===159)).toHaveLength(1);expect(versions).toContain(158);
  expect(callbackCompensationMigrations[0].version).toBe(159);expect(Math.max(...versions)).toBe(159);expect(versions.filter(v=>v===158)).toHaveLength(1);
});
it('canonical schema is SQLite-idempotent and all delivery/request uniqueness is actually enforced',()=>{
  const raw=new Database(':memory:');
  try {
    for(let pass=0;pass<2;pass++)for(const ddl of callbackCompensationDdl)raw.exec(sqliteDialect.renderDdl(ddl));
    raw.exec(`INSERT INTO workflow_callback_compensation_items
      (item_id,batch_id,environment_key,delivery_key,flow_id,state,frozen_cipher)
      VALUES ('one','b','e','same-delivery','f','queued','cipher')`);
    expect(()=>raw.exec(`INSERT INTO workflow_callback_compensation_items
      (item_id,batch_id,environment_key,delivery_key,flow_id,state,frozen_cipher)
      VALUES ('two','b','e','same-delivery','f','queued','cipher')`)).toThrow(/UNIQUE/);
    raw.exec(`INSERT INTO workflow_callback_compensation_batches
      (batch_id,environment_key,scope_key,request_key,request_hash,metadata_json)
      VALUES ('b','e','s','same-request','hash','{}')`);
    expect(()=>raw.exec(`INSERT INTO workflow_callback_compensation_batches
      (batch_id,environment_key,scope_key,request_key,request_hash,metadata_json)
      VALUES ('b2','e','s','same-request','hash','{}')`)).toThrow(/UNIQUE/);
  }finally{raw.close();}
});
it.each([mysqlDialect,zdasDialect])('$name rendering uses short full unique keys, inline indexes, BIGINT millis and protected LONGTEXT',dialect=>{
  const sql=callbackCompensationDdl.map(d=>dialect.renderDdl(d)).join('\n');
  expect(sql).not.toMatch(/AUTOINCREMENT|unixepoch|CREATE INDEX/);expect(sql).toMatch(/AUTO_INCREMENT/);
  expect(sql).toMatch(/UNIQUE\(delivery_key\)/);expect(sql).toMatch(/UNIQUE\(request_key\)/);
  expect(sql).toMatch(/expires_ms BIGINT NOT NULL/);expect(sql).toMatch(/sending_until_ms BIGINT/);
  expect(sql).toMatch(/frozen_cipher LONGTEXT/);expect(sql).toMatch(/flow_id TEXT/);
  // Widest composite index: (64 + 16) * four bytes = 320 < historical 767-byte limit.
  expect((64+16)*4).toBeLessThan(767);expect(sql).toContain('idx_cb_comp_work(environment_key, state)');
  expect(sql).not.toMatch(/UNIQUE\([^)]*(?:config|url|generation|receiver)/);
});
it('managed MySQL SQL exactly matches normal migration rendering, with InnoDB',()=>{
  const path=fileURLToPath(new URL('../../../../../../../scripts/sql/callback-compensation-v159.mysql.sql',import.meta.url));
  const supplied=readFileSync(path,'utf8').split('\n').filter(line=>!line.startsWith('--')).join('\n').trim();
  const generated=callbackCompensationDdl.map(d=>mysqlDialect.renderDdl(d)+' ENGINE=InnoDB;').join('\n\n');
  expect(supplied).toBe(generated);
});

it.each([mysqlDialect,zdasDialect])('$name source binds numeric Unix seconds and preserves inclusive completion boundaries',async dialect=>{
  const f=fixture();
  const start=NOW/1000-60, end=NOW/1000;
  for(const [flowId,completed_at] of [['before',start-1],['at-start',start],['middle',start+1],
    ['at-end',end],['after',end+1],['unfinished',null]] as const)f.run(flowId,{completed_at});
  f.run('other-workflow',{workflow_id:'other',completed_at:start});
  const parameters:unknown[][]=[];
  const database:IDatabase={dbType:dialect.name==='zdas'?'zdas':'mysql',dialect,
    async query<T>(sql:string,params?:unknown[]){parameters.push(params??[]);return f.db.query<T>(sql,params);},
    exec:f.db.exec.bind(f.db),transaction:f.db.transaction.bind(f.db),close:async()=>{}};
  try{
    const source=new CallbackCompensationSource(database);
    expect(await source.candidates('wf',{configId:'callback-string-id',
      from:new Date(start*1000).toISOString(),to:new Date(end*1000).toISOString()}))
      .toEqual(['at-start','middle','at-end']);
    expect(parameters[0]).toEqual(['wf',start,end]);
    expect(parameters[0].slice(1).every(value=>typeof value==='number')).toBe(true);
    // Fractional ISO bounds must not be rounded outward to include adjacent integer seconds.
    expect(await source.candidates('wf',{configId:'callback-string-id',
      from:new Date(start*1000+1).toISOString(),to:new Date(end*1000-1).toISOString()})).toEqual(['middle']);
    expect(parameters[1]).toEqual(['wf',start+0.001,end-0.001]);
  }finally{await f.close();}
});
it.each([mysqlDialect,zdasDialect])('$name source allows exactly 100 matches; rejects 101 without silent truncation',async dialect=>{
  const f=fixture();
  const database:IDatabase={dbType:dialect.name==='zdas'?'zdas':'mysql',dialect,
    query:f.db.query.bind(f.db),exec:f.db.exec.bind(f.db),transaction:f.db.transaction.bind(f.db),close:async()=>{}};
  try{
    for(let i=0;i<100;i++)f.run(`flow-${String(i).padStart(3,'0')}`);
    const source=new CallbackCompensationSource(database);
    const input={configId:'callback-string-id',from:new Date(NOW-60_000).toISOString(),to:new Date(NOW).toISOString()};
    expect(await source.candidates('wf',input)).toHaveLength(100);
    f.run('flow-100');
    await expect(source.candidates('wf',input)).rejects.toMatchObject({code:'RANGE_TOO_LARGE',status:422});
  }finally{await f.close();}
});

it.each([mysqlDialect,zdasDialect])('$name accepts canonical BIGINT completion/retry strings without trusting them as generation',async dialect=>{
  const f=fixture();f.run();f.config();const proof=await f.authorize();
  const database:IDatabase={dbType:dialect.name==='zdas'?'zdas':'mysql',dialect,
    async query<T>(sql:string,params?:unknown[]){
      const rows=await f.db.query<T>(sql,params);
      if(sql.includes('FROM flow_runs'))for(const row of rows){
        const r=row as Record<string,unknown>;
        for(const field of ['completed_at','retry_epoch'])if(r[field]!=null)r[field]=String(r[field]);
      }
      return rows;
    },exec:f.db.exec.bind(f.db),transaction:f.db.transaction.bind(f.db),close:async()=>{}};
  const queries:EvidenceQuery[]=[];
  const transport:CompensationTransport=async request=>{f.calls.push(request);return {status:204};};
  const service=new CallbackCompensationService(database,{environment:'pre',protectionKey:key,transport,now:()=>NOW,
    evidence:{async resolve(q){queries.push(q);return {...proof,snapshotHash:q.snapshotHash,retryEpoch:q.retryEpoch};}}});
  try{
    const source=new CallbackCompensationSource(database),snapshot=(await source.snapshot('wf','flow-1'))!;
    expect(snapshot.run.retry_epoch).toBe(0);expect(source.completedAt(snapshot)).toBe(NOW/1000-10);
    const preview=await service.preview('wf','admin',{configId:'callback-string-id',flowIds:['flow-1']});
    expect(preview.items[0].status).toBe('eligible');
    expect(queries[0]).toMatchObject({retryEpoch:0,completedAt:new Date(NOW-10_000).toISOString()});
    // A canonical retry fence is still not an independent generation attestation.
    const denied=new CallbackCompensationService(database,{environment:'pre',protectionKey:key,transport,now:()=>NOW});
    try{expect((await denied.preview('wf','admin',{configId:'callback-string-id',flowIds:['flow-1']})).items[0].status).toBe('review');}
    finally{await denied.close();}
  }finally{await service.close();await f.close();}
});
it.each([mysqlDialect,zdasDialect])('$name completion parsing rejects noncanonical/unsafe BIGINT values locally',async dialect=>{
  const f=fixture();const source=new CallbackCompensationSource({...f.db,dialect} as IDatabase);
  try{
    for(const value of [null,undefined,'',' ','01','1e9','1.0','-1',0.5,-1,NaN,Infinity,
      '9007199254740992',Number.MAX_SAFE_INTEGER+1,'8640000000001','2026-11-28T04:00:00Z'])
      expect(source.completedAt({run:{completed_at:value},nodes:[],trial:false,snapshotHash:''})).toBeNull();
    for(const value of [0,'0',NOW/1000,String(NOW/1000)])
      expect(source.completedAt({run:{completed_at:value},nodes:[],trial:false,snapshotHash:''})).toBe(Number(value));
  }finally{await f.close();}
});
