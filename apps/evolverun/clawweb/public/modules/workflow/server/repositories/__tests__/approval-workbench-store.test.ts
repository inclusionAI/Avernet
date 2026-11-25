import Database from 'better-sqlite3'
import { afterEach, expect, it } from 'vitest'
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database'
import { createApprovalWorkbenchStore } from '../approval-workbench-store'
import { ApprovalCardRepository } from '../approval-card-repository'
import { searchApprovalWorkbench } from '../../services/approval-workbench'
const connections: SqliteDatabase[]=[]
afterEach(async()=>{await Promise.all(connections.splice(0).map(db=>db.close()))})
it('queries sqlite with parameterized candidate filters and verifies exact membership before pagination', async()=>{
 const raw=new Database(':memory:');raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT); INSERT INTO flow_runs VALUES ('flow1','waiting'),('flow2','waiting'),('flow3','waiting'),('flow4','waiting'); CREATE TABLE approval_cards (id INTEGER PRIMARY KEY,flow_id TEXT,node_id TEXT,workflow_id TEXT,workflow_title TEXT,message TEXT,approver_ids TEXT,approved_by TEXT,rejected_by TEXT,status TEXT,delivery_mode TEXT,created_at INTEGER)`)
 const insert=raw.prepare(`INSERT INTO approval_cards VALUES (?, ?, 'node', ?, '标题', '消息', ?, '', '', 'pending', 'card-web', ?)`)
 insert.run(1,'flow1','wf','9123',100);insert.run(2,'flow2','wf','00123',99);insert.run(3,'flow3','wf','123',98);insert.run(4,'flow4','other','123',97)
 const db=new SqliteDatabase(raw);connections.push(db);const store=createApprovalWorkbenchStore(db)
 const result=await searchApprovalWorkbench(store,{userId:'123',isAdmin:false},{workflowId:'wf',personalStatus:'pending',limit:1})
 expect(result.items.map(x=>x.cardId)).toEqual([2]);expect(result.nextCursor).toBeTruthy()
 const next=await searchApprovalWorkbench(store,{userId:'123',isAdmin:false},{workflowId:'wf',personalStatus:'pending',limit:1,cursor:result.nextCursor!})
 expect(next.items.map(x=>x.cardId)).toEqual([3]);expect(next.nextCursor).toBeNull()
})
it('rejects actions on terminal or superseded workflow cards and only resolves for an actual approver', async()=>{
 const raw=new Database(':memory:');raw.exec(`CREATE TABLE flow_runs(flow_id TEXT PRIMARY KEY,status TEXT); INSERT INTO flow_runs VALUES('f','waiting'),('done','cancelled'); CREATE TABLE approval_cards(id INTEGER PRIMARY KEY,flow_id TEXT,node_id TEXT,workflow_id TEXT,workflow_title TEXT,approval_type TEXT,message TEXT,card_fields_json TEXT,approver_ids TEXT,approver_names TEXT,approval_policy TEXT,approved_by TEXT NOT NULL,rejected_by TEXT NOT NULL,status TEXT,delivery_mode TEXT,created_at INTEGER,resolved_at INTEGER,comment TEXT); INSERT INTO approval_cards VALUES (1,'f','n','wf',NULL,NULL,NULL,NULL,'123','A','any','','','pending','card-web',1,NULL,NULL),(2,'f','n','wf',NULL,NULL,NULL,NULL,'123','A','any','','','pending','card-web',2,NULL,NULL),(3,'done','n','wf',NULL,NULL,NULL,NULL,'123','A','any','','','pending','card-web',3,NULL,NULL)`)
 const db=new SqliteDatabase(raw);connections.push(db);const store=createApprovalWorkbenchStore(db)
 expect((await store.resolveActive!(1,'123','approve')).error).toMatch(/过期/)
 expect((await store.resolveActive!(3,'123','approve')).error).toMatch(/结束/)
 expect((await store.resolveActive!(2,'999','approve')).error).toMatch(/不是/)
 expect((await store.resolveActive!(2,'123','approve')).status).toBe('approved')
})

it.each([
 ['approve', '00123', '', 'approve'],
 ['approve', '00123', '', 'reject'],
 ['reject', '', '00123', 'reject'],
 ['reject', '', '00123', 'approve'],
] as const)('rejects a repeated workbench decision after %s, including %s/%s -> %s', async (_first, approved, rejected, action) => {
 const raw = new Database(':memory:')
 raw.exec(`CREATE TABLE flow_runs(flow_id TEXT PRIMARY KEY,status TEXT); INSERT INTO flow_runs VALUES('f','waiting'); CREATE TABLE approval_cards(id INTEGER PRIMARY KEY,flow_id TEXT,node_id TEXT,workflow_id TEXT,workflow_title TEXT,approval_type TEXT,message TEXT,card_fields_json TEXT,approver_ids TEXT,approver_names TEXT,approval_policy TEXT,approved_by TEXT NOT NULL,rejected_by TEXT NOT NULL,status TEXT,delivery_mode TEXT,created_at INTEGER,resolved_at INTEGER,comment TEXT)`)
 raw.prepare(`INSERT INTO approval_cards VALUES (1,'f','n','wf',NULL,NULL,NULL,NULL,'00123,456','A,B','all',?,?,'pending','card-web',1,NULL,'original')`).run(approved, rejected)
 const db = new SqliteDatabase(raw); connections.push(db)
 const result = await createApprovalWorkbenchStore(db).resolveActive!(1, '123', action, 'replacement')
 expect(result).toMatchObject({ok:false, error:'您已处理此审批，请刷新工作台'})
 expect(raw.prepare('SELECT approved_by,rejected_by,status,comment FROM approval_cards WHERE id = 1').get()).toEqual({approved_by:approved,rejected_by:rejected,status:'pending',comment:'original'})
})

it('serializes competing workbench decisions from the same approver', async () => {
 const raw = new Database(':memory:')
 raw.exec(`CREATE TABLE flow_runs(flow_id TEXT PRIMARY KEY,status TEXT); INSERT INTO flow_runs VALUES('f','waiting'); CREATE TABLE approval_cards(id INTEGER PRIMARY KEY,flow_id TEXT,node_id TEXT,workflow_id TEXT,workflow_title TEXT,approval_type TEXT,message TEXT,card_fields_json TEXT,approver_ids TEXT,approver_names TEXT,approval_policy TEXT,approved_by TEXT NOT NULL,rejected_by TEXT NOT NULL,status TEXT,delivery_mode TEXT,created_at INTEGER,resolved_at INTEGER,comment TEXT); INSERT INTO approval_cards VALUES (1,'f','n','wf',NULL,NULL,NULL,NULL,'00123,456','A,B','all','','','pending','card-web',1,NULL,NULL)`)
 const db = new SqliteDatabase(raw); connections.push(db)
 const store = createApprovalWorkbenchStore(db)
 const [first, second] = await Promise.all([store.resolveActive!(1,'00123','approve','first'),store.resolveActive!(1,'123','reject','second')])
 expect(first).toMatchObject({ok:true,status:'pending'})
 expect(second).toMatchObject({ok:false,error:'您已处理此审批，请刷新工作台'})
 expect(raw.prepare('SELECT approved_by,rejected_by,status,comment FROM approval_cards WHERE id = 1').get()).toEqual({approved_by:'00123',rejected_by:'',status:'pending',comment:'first'})
})


it('allows a different approver while preserving legacy vote switching', async () => {
 const raw = new Database(':memory:')
 raw.exec(`CREATE TABLE flow_runs(flow_id TEXT PRIMARY KEY,status TEXT); INSERT INTO flow_runs VALUES('f','waiting'); CREATE TABLE approval_cards(id INTEGER PRIMARY KEY,flow_id TEXT,node_id TEXT,workflow_id TEXT,workflow_title TEXT,approval_type TEXT,message TEXT,card_fields_json TEXT,approver_ids TEXT,approver_names TEXT,approval_policy TEXT,approved_by TEXT NOT NULL,rejected_by TEXT NOT NULL,status TEXT,delivery_mode TEXT,created_at INTEGER,resolved_at INTEGER,comment TEXT); INSERT INTO approval_cards VALUES (1,'f','n','wf',NULL,NULL,NULL,NULL,'00123,456,789','A,B,C','all','00123','','pending','card-web',1,NULL,NULL)`)
 const db = new SqliteDatabase(raw); connections.push(db)
 const store = createApprovalWorkbenchStore(db)
 expect(await store.resolveActive!(1,'456','approve')).toMatchObject({ok:true,status:'pending',approvedBy:['00123','456']})
 expect(await store.resolveActive!(1,'999','reject')).toMatchObject({ok:false,error:'您不是此审批的授权审批人'})
 const legacy = await new ApprovalCardRepository(db).recordAction(1,'123','reject')
 expect(legacy).toMatchObject({ok:true,status:'rejected',approvedBy:['456'],rejectedBy:['00123']})
})

it.each(['mysql','zdas'] as const)('locks the run then the card before taking a consistent read on %s', async dbType => {
 const raw = new Database(':memory:')
 raw.exec(`CREATE TABLE flow_runs(flow_id TEXT PRIMARY KEY,status TEXT); INSERT INTO flow_runs VALUES('f','waiting'); CREATE TABLE approval_cards(id INTEGER PRIMARY KEY,flow_id TEXT,node_id TEXT,workflow_id TEXT,workflow_title TEXT,approval_type TEXT,message TEXT,card_fields_json TEXT,approver_ids TEXT,approver_names TEXT,approval_policy TEXT,approved_by TEXT NOT NULL,rejected_by TEXT NOT NULL,status TEXT,delivery_mode TEXT,created_at INTEGER,resolved_at INTEGER,comment TEXT); INSERT INTO approval_cards VALUES (1,'f','n','wf',NULL,NULL,NULL,NULL,'123,456','A,B','all','123','','pending','card-web',1,NULL,NULL)`)
 const db = new SqliteDatabase(raw); connections.push(db)
 const statements: string[] = []
 const wrapped = { ...db, dbType, query: db.query.bind(db), transaction: <T>(fn: (tx: any) => Promise<T>) => db.transaction(tx => fn({
  dbType, query: (sql: string, params?: unknown[]) => { statements.push(sql); return tx.query(sql.replace(/ FOR UPDATE$/, ''), params) },
  exec: tx.exec.bind(tx),
 })) }
 const result = await createApprovalWorkbenchStore(wrapped as never).resolveActive!(1,'123','reject')
 expect(result).toMatchObject({ok:false,error:'您已处理此审批，请刷新工作台'})
 expect(statements[0]).toMatch(/FROM flow_runs .* FOR UPDATE$/)
 expect(statements[1]).toMatch(/^SELECT \* FROM approval_cards WHERE id = \? FOR UPDATE$/)
})
it('queries all-person scope without a candidate employee and filters each person before paging', async()=>{
 const raw=new Database(':memory:');raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT); INSERT INTO flow_runs VALUES ('f1','waiting'),('f2','waiting'),('f3','cancelled'); CREATE TABLE approval_cards (id INTEGER PRIMARY KEY,flow_id TEXT,node_id TEXT,workflow_id TEXT,workflow_title TEXT,message TEXT,approver_ids TEXT,approved_by TEXT,rejected_by TEXT,status TEXT,delivery_mode TEXT,created_at INTEGER); INSERT INTO approval_cards VALUES (1,'f1','n','wf','流程','审批','00123,456','00123','','pending','card-web',100),(2,'f2','n','wf','流程','审批','789','','','pending','card-web',99),(3,'f3','n','wf','流程','审批','222','','','pending','card-web',98)`)
 const db=new SqliteDatabase(raw);connections.push(db);const store=createApprovalWorkbenchStore(db);const admin={userId:'999',isAdmin:true}
 const first=await searchApprovalWorkbench(store,admin,{scope:'all',personalStatus:'pending',limit:1})
 expect(first.items).toMatchObject([{cardId:1,matchedApprovers:[{empId:'456',personalStatus:'pending'}]}])
 const second=await searchApprovalWorkbench(store,admin,{scope:'all',personalStatus:'pending',limit:1,cursor:first.nextCursor!})
 expect(second.items).toMatchObject([{cardId:2,matchedApprovers:[{empId:'789',personalStatus:'pending'}]}]);expect(second.nextCursor).toBeNull()
 const handled=await searchApprovalWorkbench(store,admin,{scope:'all',personalStatus:'handled'})
 expect(handled.items).toMatchObject([{cardId:1,matchedApprovers:[{empId:'00123',personalStatus:'approved'}]}])
})
