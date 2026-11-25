import { describe, expect, it } from 'vitest'
import { searchApprovalWorkbench, type ApprovalWorkbenchCard } from '../approval-workbench'

function card(id: number, overrides: Partial<ApprovalWorkbenchCard> = {}): ApprovalWorkbenchCard {
  return { id, flow_id: `flow-${id}`, node_id: 'approval', workflow_id: 'expense', workflow_title: '费用审批', message: '审批',
    approver_ids: '00123,456', approved_by: '', rejected_by: '', status: 'pending', delivery_mode: 'card-web', is_latest: 1, created_at: 1000 - id, ...overrides }
}
function store(cards: ApprovalWorkbenchCard[]) {
  const sorted = [...cards].sort((a,b) => b.created_at-a.created_at || b.id-a.id)
  return { async scan(before: {createdAt: number; id: number} | null, limit: number) {
    return sorted.filter(c => !before || c.created_at < before.createdAt || (c.created_at === before.createdAt && c.id < before.id)).slice(0,limit)
  }}
}
const user = { userId: '123', isAdmin: false }

describe('approval workbench search', () => {
  it('defaults to current user pending across workflows and matches exact normalized employee IDs', async () => {
    const cards = [card(1), card(2,{workflow_id:'other'}), card(3,{approver_ids:'9123'}), card(4,{approved_by:'123'}), card(5,{status:'approved'})]
    const result = await searchApprovalWorkbench(store(cards),user,{personalStatus:'pending'})
    expect(result.items.map(x => x.cardId)).toEqual([1,2])
    expect(result.items[0].approverIds).toEqual(['00123','456'])
  })
  it('counts a person as handled while an all-approval card remains pending', async () => {
    const result = await searchApprovalWorkbench(store([card(1,{approved_by:'00123'})]), user, {personalStatus:'handled'})
    expect(result.items[0]).toMatchObject({personalStatus:'approved',status:'pending'})
  })
  it('rejects a normal user requesting another user even if a matching card exists', async () => {
    await expect(searchApprovalWorkbench(store([card(1)]), user, {empId:'456'})).rejects.toMatchObject({code:'FORBIDDEN'})
  })
  it('allows only administrators to query other people and still defaults admin to self', async () => {
    const admin = {userId:'123',isAdmin:true}
    expect((await searchApprovalWorkbench(store([card(1,{approver_ids:'456'})]),admin,{})).items).toHaveLength(0)
    expect((await searchApprovalWorkbench(store([card(1,{approver_ids:'456'})]),admin,{empId:'456'})).items).toHaveLength(1)
  })
  it('filters before pagination and supports stable next-page iteration', async () => {
    const cards = Array.from({length:430},(_,i)=>card(i+1,{approver_ids:i%2?'123':'9'}))
    const first = await searchApprovalWorkbench(store(cards),user,{limit:3})
    expect(first.items.map(x=>x.cardId)).toEqual([2,4,6]); expect(first.nextCursor).toBeTruthy()
    const second = await searchApprovalWorkbench(store(cards),user,{limit:3,cursor:first.nextCursor!})
    expect(second.items.map(x=>x.cardId)).toEqual([8,10,12])
  })
  it('applies workflow and half-open created-time filters', async () => {
    const result=await searchApprovalWorkbench(store([card(1),card(2),card(3,{workflow_id:'other'})]),user,{workflowId:'expense',createdFrom:998,createdTo:999})
    expect(result.items.map(x=>x.cardId)).toEqual([2])
  })
})
it('supports a 1-based result range after all filters', async () => {
 const cards=Array.from({length:40},(_,i)=>card(i+1,{approver_ids:i%2?'123':'9'}))
 const result=await searchApprovalWorkbench(store(cards),user,{start:3,end:5})
 expect(result.items.map(x=>x.cardId)).toEqual([6,8,10])
})
it('rejects a cursor combined with a numbered range',async()=>{
 await expect(searchApprovalWorkbench(store([card(1)]),user,{start:1,end:3,cursor:'anything'})).rejects.toMatchObject({code:'INVALID_ARGUMENT'})
})
it('passes a normalized employee candidate to storage without weakening exact matching',async()=>{
 let scanned=''
 const db={async scan(_before:unknown,_limit:number,candidate?:{empId:string}){scanned=candidate?.empId??'';return []}}
 await searchApprovalWorkbench(db,user,{})
 expect(scanned).toBe('123')
})
it('rejects reusing a pagination cursor with a different target or filter',async()=>{
 const cards=[card(1),card(2),card(3)]
 const admin={userId:'123',isAdmin:true}
 const first=await searchApprovalWorkbench(store(cards),admin,{limit:1,personalStatus:'pending'})
 await expect(searchApprovalWorkbench(store(cards),admin,{empId:'456',limit:1,personalStatus:'pending',cursor:first.nextCursor!})).rejects.toMatchObject({code:'INVALID_ARGUMENT'})
 await expect(searchApprovalWorkbench(store(cards),admin,{limit:1,personalStatus:'handled',cursor:first.nextCursor!})).rejects.toMatchObject({code:'INVALID_ARGUMENT'})
})
it('does not present terminal workflows as pending, and marks missing workflow status as unverified',async()=>{
 const cards=[card(1,{run_status:'cancelled'}),card(2,{run_status:'waiting'}),card(3,{run_status:null})]
 const result=await searchApprovalWorkbench(store(cards),user,{personalStatus:'pending'})
 expect(result.items.map(x=>x.cardId)).toEqual([2,3])
 expect(result.items.map(x=>x.actionability)).toEqual(['available','unknown'])
})
it('omits an old pending card superseded by another card while retaining already-handled history',async()=>{
 const cards=[card(1,{run_status:'waiting',is_latest:0}),card(2,{run_status:'waiting',is_latest:1}),card(3,{run_status:'waiting',is_latest:0,approved_by:'123'})]
 const pending=await searchApprovalWorkbench(store(cards),user,{personalStatus:'pending'})
 expect(pending.items.map(x=>x.cardId)).toEqual([2])
 const handled=await searchApprovalWorkbench(store(cards),user,{personalStatus:'handled'})
 expect(handled.items.map(x=>x.cardId)).toEqual([3])
})
