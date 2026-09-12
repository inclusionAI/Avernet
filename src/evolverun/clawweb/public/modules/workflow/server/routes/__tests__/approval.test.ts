import { afterEach, expect, it } from 'vitest';
import express from 'express';
import type { Server } from 'node:http';
import { createApprovalRouter } from '../approval';
import type { ApprovalCardRow } from '../../repositories/approval-card-repository';
const servers: Server[]=[];
afterEach(async()=>{await Promise.all(servers.splice(0).map(s=>new Promise<void>((resolve,reject)=>s.close(err=>err?reject(err):resolve()))));});
async function fixture(content: unknown) {
 const row: ApprovalCardRow={id:1,flow_id:'flow-1',node_id:'review',workflow_id:'test',workflow_title:'流程',approval_type:'HUMAN_CONFIRM',message:'确认',card_fields_json:JSON.stringify(content),approver_ids:'reviewer',approver_names:'审核人',approval_policy:'any',approved_by:'',rejected_by:'',status:'pending',delivery_mode:'card-web',created_at:100,resolved_at:null,comment:null};
 const db={query:async(_sql:string,p:unknown[])=>p?.[0]===1?[{...row}]:[],exec:async(_sql:string,p:unknown[])=>{[row.approved_by,row.rejected_by,row.status,row.resolved_at,row.comment]=p as any;return {affectedRows:1};}};
 const app=express();app.use(express.json());app.use('/approval',createApprovalRouter(db as any));
 const server=app.listen(0,'127.0.0.1');servers.push(server);await new Promise<void>(resolve=>server.once('listening',resolve));
 const base=`http://127.0.0.1:${(server.address() as any).port}/approval/1`;
 return {row,base};
}
it('serves legacy fields and configured display envelopes through the migrated API',async()=>{
 for(const content of [[{label:'任务',value:'T-1'}],{fields:[{label:'任务',value:'T-1'}],display:{title:'复核',confirmLabel:'执行处置',footer:''}}]){
  const {base}=await fixture(content);const data=await(await fetch(`${base}?empId=reviewer`)).json();
  expect(data.cardFields).toEqual([{label:'任务',value:'T-1'}]);expect(data.isApprover).toBe(true);
  if(!Array.isArray(content))expect(data.display).toEqual(content.display);else expect(data.display).toBeUndefined();
 }
});
it('retains action authorization, resolution time and duplicate protection',async()=>{
 const {base}=await fixture([]);
 const submit=(empId:string)=>fetch(`${base}/resolve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({empId,action:'approve',comment:'checked'})});
 expect((await submit('stranger')).status).toBe(403);
 expect((await submit('reviewer')).status).toBe(200);
 const status=await(await fetch(`${base}/status`)).json();expect(status.status).toBe('approved');expect(status.resolvedAt).toBeGreaterThan(100);expect(status.approvedBy).toEqual(['reviewer']);
 expect((await submit('reviewer')).status).toBe(409);
});


it('retains missing and invalid approval responses', async () => {
  const {base}=await fixture([]);
  expect((await fetch(base.replace(/\/1$/, '/999'))).status).toBe(404);
  expect((await fetch(base.replace(/\/1$/, '/invalid'))).status).toBe(400);
});

it('keeps data resource section decisions in detail across resolve and detail reads', async () => {
 const sections=[{id:'assetSelection',title:'资产确认',items:[{id:'authAsset',label:'确认确权资产',expected:'recommended.table',customizable:true,actions:[{key:'accept',label:'使用推荐'},{key:'customize',label:'自定义'}]}]}];
 const {base,row}=await fixture({fields:[{label:'申请',value:'data resource'}],sections,display:{confirmLabel:'确认资产'}});
 const before=await(await fetch(`${base}?empId=reviewer`)).json();expect(before.sections).toEqual(sections);
 const detail={assetSelection:{authAsset:{action:'customize',value:'project.table'}}};
 const response=await fetch(`${base}/resolve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({empId:'reviewer',action:'approve',comment:'已核对',detail})});
 expect(response.status).toBe(200);expect(row.delivery_mode).toBe('card-web');
 expect(JSON.parse(row.comment!)).toEqual({note:'已核对',detail});
 const after=await(await fetch(`${base}?empId=reviewer`)).json();expect(after.detail).toEqual(detail);expect(after.sections).toEqual(sections);
});
