import { expect, it } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { CallbackCompensationService } from '../service.js';
import { fixture, key, NOW } from './fixture.js';
it.each(['queued','sending'] as const)('real file SQLite closes/reopens and safely recovers %s',async state=>{
  const directory=mkdtempSync(join(tmpdir(),'cb-compensation-'));const path=join(directory,'durable.sqlite');
  const f=fixture(path);let restarted:CallbackCompensationService|undefined;let reopened:SqliteDatabase|undefined;
  try {
    f.run();f.config();await f.authorize();const {batch}=await f.queued();
    if(state==='sending')f.raw.prepare("UPDATE workflow_callback_compensation_items SET state='sending',sending_until_ms=?").run(NOW-1);
    await f.close();const raw=new Database(path);reopened=new SqliteDatabase(raw);let posts=0;
    restarted=new CallbackCompensationService(reopened,{environment:'pre',protectionKey:key,evidence:f.evidence,now:()=>NOW+16*60_000,
      transport:async()=>{posts++;return{status:204};}});
    await restarted.tick();await restarted.tick();const result=await restarted.get('wf','admin',batch.batchId);
    expect(result.items[0].state).toBe(state==='queued'?'received':'unknown');expect(posts).toBe(state==='queued'?1:0);
    expect(raw.prepare('SELECT COUNT(*) n FROM http_callback_logs').get()).toEqual({n:1});
  }finally{await restarted?.close();await reopened?.close();await f.close();rmSync(directory,{recursive:true,force:true});}
});
