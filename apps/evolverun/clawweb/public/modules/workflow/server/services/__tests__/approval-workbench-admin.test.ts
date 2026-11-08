import { expect, it } from 'vitest'
import { searchApprovalWorkbench, type ApprovalWorkbenchCard } from '../approval-workbench'

const admin = {userId:'100',isAdmin:true}, user = {userId:'100',isAdmin:false}
function card(id:number, changes:Partial<ApprovalWorkbenchCard> = {}):ApprovalWorkbenchCard {
 return {id, flow_id:`f${id}`,node_id:'n',workflow_id:'wf',workflow_title:'流程',message:null,approver_ids:'00123,456',approved_by:'00123',rejected_by:'',status:'pending',delivery_mode:'card-web',created_at:1000-id,run_status:'waiting',is_latest:1,...changes}
}
function store(rows:ApprovalWorkbenchCard[]) {
 return {scan:async (before:{createdAt:number;id:number}|null,limit:number)=>rows.filter(r=>!before||r.created_at<before.createdAt||(r.created_at===before.createdAt&&r.id<before.id)).slice(0,limit)}
}
it('denies all-person scope for non-admins and leaves omitted scope as self',async()=>{
 const db=store([card(1)])
 await expect(searchApprovalWorkbench(db,user,{scope:'all'} as never)).rejects.toMatchObject({code:'FORBIDDEN'})
 expect((await searchApprovalWorkbench(db,admin,{})).items).toEqual([])
})
it('returns a card in both tabs if different approvers are pending and handled',async()=>{
 const db=store([card(1)])
 const pending=await searchApprovalWorkbench(db,admin,{scope:'all',personalStatus:'pending'} as never)
 const handled=await searchApprovalWorkbench(db,admin,{scope:'all',personalStatus:'handled'} as never)
 expect(pending.items).toHaveLength(1);expect(pending.items[0]).toMatchObject({cardId:1,matchedApprovers:[{empId:'456',personalStatus:'pending'}]})
 expect(handled.items).toHaveLength(1);expect(handled.items[0]).toMatchObject({cardId:1,matchedApprovers:[{empId:'00123',personalStatus:'approved'}]})
})
it('deduplicates matching approvers on a card and keeps mixed decisions',async()=>{
 const db=store([card(1,{approver_ids:'00123,123,456',approved_by:'123',rejected_by:'456'})])
 const result=await searchApprovalWorkbench(db,admin,{scope:'all',personalStatus:'handled'} as never)
 expect(result.items).toHaveLength(1)
 expect(result.items[0]).toMatchObject({personalStatus:null,matchedApprovers:[{empId:'00123',personalStatus:'approved'},{empId:'456',personalStatus:'rejected'}]})
})
it('paginates all-person results after personal-status filtering and binds the cursor to scope',async()=>{
 const db=store([card(1),card(2,{approver_ids:'999',approved_by:''}),card(3)])
 const first=await searchApprovalWorkbench(db,admin,{scope:'all',personalStatus:'handled',limit:1} as never)
 const second=await searchApprovalWorkbench(db,admin,{scope:'all',personalStatus:'handled',limit:1,cursor:first.nextCursor!} as never)
 expect(first.items.map(x=>x.cardId)).toEqual([1]);expect(second.items.map(x=>x.cardId)).toEqual([3]);expect(second.nextCursor).toBeNull()
 await expect(searchApprovalWorkbench(db,admin,{personalStatus:'handled',cursor:first.nextCursor!})).rejects.toMatchObject({code:'INVALID_ARGUMENT'})
})
it('rejects combining all-person scope with a specific employee',async()=>{
 await expect(searchApprovalWorkbench(store([]),admin,{scope:'all',empId:'456'} as never)).rejects.toMatchObject({code:'INVALID_ARGUMENT'})
})
it('does not confuse all-person cursors with an employee ID containing the scope marker',async()=>{
 const db=store([card(1,{approver_ids:'*',approved_by:''}),card(2,{approver_ids:'*',approved_by:''})])
 const first=await searchApprovalWorkbench(db,admin,{scope:'all',personalStatus:'pending',limit:1} as never)
 await expect(searchApprovalWorkbench(db,admin,{empId:'*',personalStatus:'pending',limit:1,cursor:first.nextCursor!})).rejects.toMatchObject({code:'INVALID_ARGUMENT'})
})
