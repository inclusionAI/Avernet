// @vitest-environment node
import {afterEach,describe,expect,it} from 'vitest';
import {repairFixture} from './repair-batch-fixtures.js';
import {createRepairWorkbenchService} from '../repair-batch-service.js';
import {workflowSnapshotKey, type WorkflowSnapshotBinding} from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';
import {createRepairReleaseService} from '../repair-release-service.js';
import {repairReleaseMigrations} from '@avernet/clawweb-shared/server/schema-repair-release';
import {digestRepairJson} from '../../contracts/repair-batch.js';
import {RepairBatchRepository} from '../../repositories/repair-batch-repository.js';
const opened:Awaited<ReturnType<typeof repairFixture>>[]=[];
afterEach(async()=>{for(const f of opened.splice(0))await f.db.close();});
const source=(version:number,deploy:number,digest:string):WorkflowSnapshotBinding=>({status:'resolved',snapshot:{kind:'deployment',workflowId:'wf-1',workflowVersion:version,deployNumber:deploy,configDigest:digest.repeat(64)}});
async function setup(){
 const f=await repairFixture(2);opened.push(f);let active=source(40,35,'a');
 f.sourcePort.activeSnapshot=async()=>active;
 f.executionPort.integrationVersion='workflow-proposal-plan/v1';
 const original=f.executionPort.resolveBaseline;f.executionPort.resolveBaseline=async wf=>({...await original(wf),activeDeployNumber:35,proposalSpecDigest:'a'.repeat(64)});
 f.items.forEach((item,index)=>{item.context={sourceSnapshot:index?source(39,34,'e'):active};item.proposal={workflowId:'wf-1',schemaVersion:'workflow-patch/v1',baseSpecDigest:(index?'e':'a').repeat(64),summary:index?'Historical constraint':'Current constraint',operations:[{op:'replace',nodeId:'fetch',path:'/executor/prompt',value:index?'Keep historical constraint':'Keep current constraint'}]};});
 const service=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort);
 const page=await service.candidates('wf-1',{versionScope:'all',includeHistorical:true});
 const request={workflowId:'wf-1',itemIds:['item-0','item-1'],historicalItemIds:['item-1'],targetSnapshotKey:workflowSnapshotKey(active.snapshot!),inputDigest:page.inputDigest,selection:Object.fromEntries(page.items.map(i=>[i.itemId,i.selectionDigest!])),instructions:'Preserve both compatible constraints',requestId:'history'};
 return {...f,service,request,setActive:(value:WorkflowSnapshotBinding)=>{active=value;}};
}
describe('explicit historical reference generation',()=>{
 it('hydrates selected historical originals with the current target and keeps declared roles checked on feedback',async()=>{
   const f=await setup();
   const fresh=await f.service.selectionState('wf-1',['item-0','item-1'],false);
   expect(fresh.activeSnapshot).toMatchObject({status:'resolved',snapshot:{deployNumber:35}});
   expect(fresh.items.map(item=>item.itemId).sort()).toEqual(['item-0','item-1']);
   const revision=await f.service.create('human',f.request);
   await expect(f.service.revise('human',revision.taskId,{...f.request,requestId:'feedback',historicalItemIds:['item-0','item-1'],expectedAttemptRevision:1,parentCandidateCommit:null,feedback:'Check again'})).rejects.toMatchObject({code:'SOURCE_CHANGED'});
 });

 it('freezes old goals unchanged and integrates against one current target without owning the old item',async()=>{
   const f=await setup();const revision=await f.service.create('human',f.request);
   expect(revision.input.integration).toMatchObject({schemaVersion:'workflow-proposal-plan/v1',plans:[{originalItemIds:['item-0','item-1']}]});
   expect(revision.input.items[1]).toMatchObject({proposal:{baseSpecDigest:'e'.repeat(64)},context:{referenceOnly:true,targetSnapshot:{workflowVersion:40},sourceSnapshot:{status:'resolved',snapshot:{workflowVersion:39}}}});
   expect(await f.db.query("SELECT item_id FROM workflow_repair_items WHERE state='processing'")).toEqual([{item_id:'item-0'}]);
   expect(f.calls).toHaveLength(1);
   expect((await f.service.create('human',f.request)).taskId).toBe(revision.taskId);
   expect(f.calls).toHaveLength(1);
 });
 it('publishes a history-only candidate in the local fixture without reopening the historical item',async()=>{
   const f=await setup();
   for(const m of repairReleaseMigrations)for(const sql of m.sql)await f.db.exec(f.db.dialect.renderDdl(sql));
   await f.db.transaction(tx=>new RepairBatchRepository(tx).materializeItem(tx,{workflowId:'wf-1',episodeKey:'initial',item:f.items[1]}));
   await f.db.exec("UPDATE workflow_repair_items SET state='verified',active_task_id='old-task',active_revision=1 WHERE item_id='item-1'");
   const revision=await f.service.create('human',{...f.request,itemIds:['item-1'],historicalItemIds:['item-1'],selection:{'item-1':f.request.selection['item-1']}});
   const draft={candidateCommit:'d'.repeat(40),parentCandidateCommit:revision.input.baseline.packCommit,packDigest:'e'.repeat(40),itemResults:[{itemId:'item-1',status:'changed',reason:'Adapted goal to current files'}]};
   await f.service.report({workflowId:'wf-1',taskId:revision.taskId,revision:1,expectedStateVersion:0,status:'succeeded',draft,
     checks:{schemaVersion:'workflow-repair-checks/v1',candidateCommit:draft.candidateCommit,packDigest:draft.packDigest,candidateDigest:digestRepairJson(draft),static:{status:'passed'},mock:{status:'passed'},coverage:[{itemId:'item-1'}]}});
   f.executionPort.diff=async request=>({...request,files:[{path:'workflows/wf-1.yaml',status:'M',before:'old',after:'new',binary:false,truncated:false,patch:'@@ -1 +1 @@\n-old\n+new'}],truncated:false});
   const release=createRepairReleaseService({db:f.db,workbench:f.service,publisher:{targets:async()=>[{id:'target',botId:'bot',ownerId:'owner',environment:'pre',label:'fixture'}],activeBaseline:async()=>revision.input.baseline,dispatch:async()=>({status:'running'}),reconcile:async d=>({status:'succeeded',receipt:{requestId:d.requestId,candidateCommit:d.candidateCommit,packDigest:d.packDigest,deployNumber:36,releaseVersion:41,tagName:'deploy/wf-1/#36',targetId:d.target.id}})}});
   const approval=await release.confirm('human',revision.taskId,{revision:1,expectedStateVersion:1,requestId:'confirm',targetId:'target',acknowledgeUncoveredChecks:true});
   const publication=await release.deploy('human',revision.taskId,{revision:1,expectedApprovalVersion:approval.stateVersion,requestId:'publish'});
   expect((await release.reconcile('human',revision.taskId,publication.id)).status).toBe('succeeded');
   expect(await f.db.query("SELECT state,active_task_id FROM workflow_repair_items WHERE item_id='item-1'")).toEqual([{state:'verified',active_task_id:'old-task'}]);
 });
 it('rejects target changes before submission and retains the original selection',async()=>{
   const f=await setup();f.setActive(source(41,36,'a'));
   await expect(f.service.create('human',f.request)).rejects.toMatchObject({details:{reasonCode:'TARGET_VERSION_CHANGED'}});
   expect(f.calls).toHaveLength(0);expect(f.request.itemIds).toEqual(['item-0','item-1']);
 });
 it('does not accept undeclared historical references or permit old patches through a legacy consumer',async()=>{
   const f=await setup();
   await expect(f.service.create('human',{...f.request,historicalItemIds:[]})).rejects.toMatchObject({details:{reasonCode:'HISTORICAL_REFERENCE_CONFIRMATION_REQUIRED'}});
   delete f.executionPort.integrationVersion;
   await expect(f.service.create('human',f.request)).rejects.toMatchObject({details:{reasonCode:'HISTORICAL_REFERENCE_UNSUPPORTED'}});
   expect(f.calls).toHaveLength(0);
 });
 it('can reference a verified old item and cancellation preserves its old state and ownership',async()=>{
   const f=await setup();
   await f.db.transaction(tx=>new RepairBatchRepository(tx).materializeItem(tx,{workflowId:'wf-1',episodeKey:'initial',item:f.items[1]}));
   await f.db.exec("UPDATE workflow_repair_items SET state='verified',active_task_id='old-task',active_revision=1 WHERE item_id='item-1'");
   const revision=await f.service.create('human',f.request);
   await f.service.cancel('human',revision.taskId,1);
   expect(await f.db.query("SELECT state,active_task_id FROM workflow_repair_items WHERE item_id='item-1'")).toEqual([{state:'verified',active_task_id:'old-task'}]);
 });
 it('fences concurrent history-only requests with different IDs without reserving historical items',async()=>{
   const f=await setup();
   const request={...f.request,itemIds:['item-1'],historicalItemIds:['item-1'],selection:{'item-1':f.request.selection['item-1']}};
   const results=await Promise.allSettled([f.service.create('human',{...request,requestId:'history-a'}),f.service.create('human',{...request,requestId:'history-b'})]);
   expect(results.filter(r=>r.status==='fulfilled')).toHaveLength(1);
   const rejected=results.find(r=>r.status==='rejected') as PromiseRejectedResult;
   expect(rejected.reason).toMatchObject({code:'ITEM_BUSY',details:{reasonCode:'HISTORICAL_TASK_EXISTS'}});
   expect(f.calls).toHaveLength(1);
   expect(await f.db.query('SELECT * FROM workflow_repair_items')).toEqual([]);
   const created=(results.find(r=>r.status==='fulfilled') as PromiseFulfilledResult<Awaited<ReturnType<typeof f.service.create>>>).value;
   await f.service.cancel('human',created.taskId,1);
   expect((await f.service.create('human',{...request,requestId:'after-cancel'})).taskId).not.toBe(created.taskId);
   expect(f.calls).toHaveLength(2);
 });

 it('keeps upstream manual-goal behavior for historical text suggestions without patch preconditions',async()=>{
   const f=await setup();
   f.items[1].proposal={summary:'Keep this historical requirement'};
   f.items[1].instruction='Preserve the old constraint where applicable';
   const result=await f.service.create('human',{...f.request,itemIds:['item-1'],historicalItemIds:['item-1'],selection:{'item-1':f.request.selection['item-1']}});
   expect(result.input.items[0]).toMatchObject({proposal:f.items[1].proposal,instruction:f.items[1].instruction,context:{referenceOnly:true}});
   expect(f.calls).toHaveLength(1);
 });
 it('fences publication of legacy duplicate historical tasks before a second external dispatch',async()=>{
   const f=await setup();
   for(const m of repairReleaseMigrations)for(const sql of m.sql)await f.db.exec(f.db.dialect.renderDdl(sql));
   const request={...f.request,itemIds:['item-1'],historicalItemIds:['item-1'],selection:{'item-1':f.request.selection['item-1']}};
   const first=await f.service.create('human',{...request,requestId:'legacy-a'});
   // Reconstruct two tasks accepted before the generation fence existed.
   await f.service.cancel('human',first.taskId,1);
   const second=await f.service.create('human',{...request,requestId:'legacy-b'});
   await f.db.exec("UPDATE workflow_repair_revisions SET phase='drafting',state_version=0 WHERE task_id=?",[first.taskId]);
   for(const revision of [first,second]) {
     const draft={candidateCommit:'d'.repeat(40),parentCandidateCommit:revision.input.baseline.packCommit,packDigest:'e'.repeat(40),itemResults:[{itemId:'item-1',status:'changed',reason:'Adapted historical goal'}]};
     await f.service.report({workflowId:'wf-1',taskId:revision.taskId,revision:1,expectedStateVersion:0,status:'succeeded',draft,
       checks:{schemaVersion:'workflow-repair-checks/v1',candidateCommit:draft.candidateCommit,packDigest:draft.packDigest,candidateDigest:digestRepairJson(draft),static:{status:'passed'},mock:{status:'passed'},coverage:[{itemId:'item-1'}]}});
   }
   f.executionPort.diff=async request=>({...request,files:[{path:'workflows/wf-1.yaml',status:'M',before:'old',after:'new',binary:false,truncated:false,patch:'@@ -1 +1 @@\n-old\n+new'}],truncated:false});
   let dispatched=0;
   const release=createRepairReleaseService({db:f.db,workbench:f.service,publisher:{targets:async()=>[{id:'target',botId:'bot',ownerId:'owner',environment:'pre',label:'fixture'}],activeBaseline:async()=>first.input.baseline,
     dispatch:async()=>{dispatched++;return {status:'running'};},reconcile:async()=>({status:'running'})}});
   const approvals=await Promise.all([first,second].map(revision=>release.confirm('human',revision.taskId,{revision:1,expectedStateVersion:1,requestId:`confirm-${revision.taskId}`,targetId:'target',acknowledgeUncoveredChecks:true})));
   const results=await Promise.allSettled([first,second].map((revision,index)=>release.deploy('human',revision.taskId,{revision:1,expectedApprovalVersion:approvals[index].stateVersion,requestId:`deploy-${revision.taskId}`})));
   expect(results.filter(r=>r.status==='fulfilled')).toHaveLength(1);
   expect((results.find(r=>r.status==='rejected') as PromiseRejectedResult).reason).toMatchObject({code:'ITEM_BUSY',details:{reasonCode:'HISTORICAL_TASK_EXISTS'}});
   expect(dispatched).toBe(1);
   expect(await f.db.query('SELECT * FROM workflow_repair_items')).toEqual([]);
 });

});
