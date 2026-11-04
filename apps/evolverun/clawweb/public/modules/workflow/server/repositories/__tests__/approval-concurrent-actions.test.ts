import Database from 'better-sqlite3'
import { expect,it } from 'vitest'
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database'
import { ApprovalCardRepository } from '../approval-card-repository'
it('preserves both decisions when two approvers act concurrently',async()=>{
 const raw=new Database(':memory:');raw.exec(`CREATE TABLE approval_cards (id INTEGER PRIMARY KEY, flow_id TEXT, node_id TEXT, workflow_id TEXT, workflow_title TEXT, approval_type TEXT, message TEXT, card_fields_json TEXT, approver_ids TEXT, approver_names TEXT, approval_policy TEXT, approved_by TEXT NOT NULL, rejected_by TEXT NOT NULL, status TEXT, delivery_mode TEXT, created_at INTEGER, resolved_at INTEGER, comment TEXT); INSERT INTO approval_cards VALUES(1,'f','n','wf',NULL,NULL,NULL,NULL,'123,456','A,B','all','','','pending','card-web',1,NULL,NULL);`)
 const db=new SqliteDatabase(raw);try{
  const repo=new ApprovalCardRepository(db)
  const results=await Promise.all([repo.recordAction(1,'123','approve'),repo.recordAction(1,'456','approve')])
  const row=await repo.findById(1);expect(results.every(result=>result.ok)).toBe(true)
  expect(row?.approved_by.split(',').sort()).toEqual(['123','456']);expect(row?.status).toBe('approved')
 }finally{await db.close()}
})
it('returns a conflict after repeated compare-and-swap failures instead of losing an action',async()=>{
 const card={id:1,approver_ids:'123',approved_by:'',rejected_by:'',status:'pending',approval_policy:'any'}
 const db={query:async()=>[card],exec:async()=>({affectedRows:0})}
 const result=await new ApprovalCardRepository(db as never).recordAction(1,'123','approve')
 expect(result).toMatchObject({ok:false,error:'审批状态已变更，请刷新后重试'})
})
