// @vitest-environment node
import { afterEach, expect, it } from 'vitest';
import { canonicalRepairJson,digestRepairJson } from '../../contracts/repair-batch.js';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
let fixture: Awaited<ReturnType<typeof repairFixture>>;
afterEach(async()=>{await fixture?.db.close();});
async function setup(count=18){
 fixture=await repairFixture(count);
 for(const migration of repairReleaseMigrations)for(const sql of migration.sql)await fixture.db.exec(fixture.db.dialect.renderDdl(sql));
 for(const item of fixture.items)item.context={nodeId:'report',diagnoses:[{flowId:`run-${item.itemId}`,reasoning:'checked',analysisContext:{facts:['observed failure']},evidence:[{eventId:`event-${item.itemId}`,payload:{report:'x'.repeat(300000)}}]}]};
 const service=createRepairWorkbenchService(fixture.db,fixture.sourcePort,fixture.executionPort);
 const request={workflowId:'wf-1',itemIds:fixture.items.map(item=>item.itemId),inputDigest:(await service.candidates('wf-1')).inputDigest,instructions:'preserve every selected goal',requestId:'large-selection'};
 return {service,request};
}
it('freezes all large evidence separately, dispatches every original and reads immutable per-item material',async()=>{
 const {service,request}=await setup();const revision=await service.create('human',request);
 expect(Buffer.byteLength(JSON.stringify(revision.input))).toBeLessThan(1024*1024);
 expect(revision.input.items.map(item=>item.itemId)).toEqual(request.itemIds.slice().sort());
 expect(revision.input.instructions).toBe(request.instructions);
 const dispatched=fixture.calls[0] as {input:typeof revision.input};
 expect(dispatched.input.items).toHaveLength(18);
 expect(JSON.stringify(dispatched.input.items[0].context)).not.toContain('x'.repeat(300000));
 expect(dispatched.input.materials).toBeDefined();
 fixture.items[0].context={replacement:'new live source'};
 const material=await service.material(revision.taskId,1,'item-0');
 expect(JSON.stringify(material.context)).toContain('x'.repeat(300000));
 expect(material.context).not.toHaveProperty('replacement');
 expect(await service.material(revision.taskId,1,'item-0')).toEqual(material);
 await expect(service.material(revision.taskId,1,'outside-selection')).rejects.toMatchObject({code:'ITEM_NOT_FOUND'});
 expect((await service.task(revision.taskId)).latestAttempt.input).toEqual(revision.input);
});
it('reuses the exact frozen material after a failed generation retry',async()=>{
 const {service,request}=await setup();const first=await service.create('human',request);
 await service.report({workflowId:'wf-1',taskId:first.taskId,revision:1,expectedStateVersion:0,status:'failed',error:{code:'AIS_FAILED'}});
 fixture.setRemoteStatus({status:'failed',rawStatus:'failed',errorMessage:'controlled failure'});
 fixture.items[0].context={replacement:'new live source'};
 const retry=await service.retryGeneration('human',first.taskId,{expectedRevision:1,requestId:'retry-large'});
 expect(retry.revision).toBe(2);
 const calls=fixture.calls as Array<{input:typeof first.input}>;
 expect(calls[1].input.items).toEqual(calls[0].input.items);
 expect(await service.material(first.taskId,2,'item-0')).toEqual(await service.material(first.taskId,1,'item-0'));
});
it('rolls back task creation and claims if the frozen material write fails',async()=>{
 const {service,request}=await setup();await fixture.db.exec('DROP TABLE workflow_repair_release_records');
 await expect(service.create('human',request)).rejects.toThrow();
 expect(fixture.calls).toHaveLength(0);
 expect(await fixture.db.query('SELECT * FROM workflow_repair_revisions')).toHaveLength(0);
 expect((await service.item('wf-1','item-0')).state).toBe('pending');
});

it('rejects corrupted frozen material before another execution is dispatched',async()=>{
 const {service,request}=await setup();const first=await service.create('human',request);
 await service.report({workflowId:'wf-1',taskId:first.taskId,revision:1,expectedStateVersion:0,status:'failed',error:{code:'AIS_FAILED'}});
 fixture.setRemoteStatus({status:'failed',rawStatus:'failed',errorMessage:'controlled failure'});
 await fixture.db.exec("UPDATE workflow_repair_release_records SET body_json = '{}' WHERE record_kind = 'repair_material'");
 await expect(service.retryGeneration('human',first.taskId,{expectedRevision:1,requestId:'corrupt-retry'})).rejects.toMatchObject({code:'CONTENT_MISMATCH',details:{reasonCode:'FROZEN_MATERIAL_INVALID'}});
 expect(fixture.calls).toHaveLength(1);
 expect((await service.task(first.taskId)).revisions).toHaveLength(1);
});

it('separates a single large source report without enlarging the inline core budget',async()=>{
 const {service,request}=await setup();fixture.items[0].context={diagnoses:[{flowId:'large-run',severity:'critical',evidence:[{eventId:'large-event',payload:{report:'large report\n'.repeat(100000)}}]}]};
 const revision=await service.create('human',request);
 expect(Buffer.byteLength(JSON.stringify(revision.input))).toBeLessThan(1024*1024);
 expect(JSON.stringify((await service.material(revision.taskId,1,'item-0')).context)).toContain('large report\\n');
});

it('rejects oversized executable goals rather than clipping them or leaving an orphan material record',async()=>{
 const {service,request}=await setup();fixture.items[0].proposal={summary:'large goal',operations:[{op:'replace',nodeId:'report',path:'/executor/prompt',value:'goal line\n'.repeat(120000)}]};
 await expect(service.create('human',request)).rejects.toMatchObject({code:'PAYLOAD_TOO_LARGE',details:{stage:'frozen_batch',sizeBreakdown:{proposalBytes:expect.any(Number),coreBytes:expect.any(Number)}}});
 expect(fixture.calls).toHaveLength(0);
 expect(await fixture.db.query("SELECT * FROM workflow_repair_release_records WHERE record_kind='repair_material'")).toHaveLength(0);
 expect(await fixture.db.query('SELECT * FROM workflow_repair_revisions')).toHaveLength(0);
});

it.each([[23,418],[1,5000]])('keeps %i goals and key evidence small with %i source references per item',async(count,references)=>{
 const {service,request}=await setup(count);
 for(const item of fixture.items){
  item.instruction='核对节点输出类型，保留已有业务约束';
  item.sources=Array.from({length:references},(_,i)=>({kind:'diagnosis_candidate' as const,signature:'s'.repeat(64),inputDigest:'d'.repeat(64),candidateId:'c'.repeat(64),analysisId:`analysis-${i}`,diagnosisId:`diagnosis-${i}`,flowId:`run-${i}`}));
  item.context={nodeId:'report',diagnoses:[{flowId:'run-0',severity:'high',reasoning:'Observed object/string mismatch',evidenceEventIds:Array.from({length:references},(_,i)=>`event-${i}`),evidence:[{eventId:'event-0',payload:{error:'expected string, actual object'}}]}]};
 }
 request.inputDigest=(await service.candidates('wf-1')).inputDigest;
 const revision=await service.create('human',request);
 expect(Buffer.byteLength(canonicalRepairJson(revision.input))).toBeLessThan(128*1024);
 expect(revision.input.items).toHaveLength(count);
 expect(revision.input.materials).toMatchObject({flowCount:references});
 const downloaded=await service.material(revision.taskId,1,revision.input.items[0].itemId);
 expect(downloaded.sources).toHaveLength(references);
 for(const item of revision.input.items){
  const full=fixture.items.find(source=>source.itemId===item.itemId)!;
  expect(full.sources).toHaveLength(references);
  expect(item.sources).toHaveLength(3);
  expect(item.instruction).toBe(full.instruction);
  expect(item.proposal).toEqual(full.proposal);
  expect(item.context?.evidenceSummary).toMatchObject({sourceCount:references,sourceRefsSha256:digestRepairJson(full.sources),flowCount:references,eventCount:references});
  expect((item.context?.evidenceSummary as any).flowIds).toHaveLength(3);
  expect((item.context?.evidenceSummary as any).eventIds).toHaveLength(3);
  expect(JSON.stringify(item.context)).toContain('expected string, actual object');
 }
 expect(fixture.calls).toHaveLength(1);
});

it('keeps an already valid large goal inline when evidence projection would only add overhead',async()=>{
 const {service,request}=await setup(1);
 fixture.items[0].context={};
 fixture.items[0].proposal={summary:'Preserve complete goal',operations:[{op:'replace',nodeId:'report',path:'/prompt',value:'x'.repeat(300000)}]};
 request.inputDigest=(await service.candidates('wf-1')).inputDigest;
 const revision=await service.create('human',request);
 expect(revision.input.materials).toBeUndefined();
 expect(revision.input.items[0].proposal).toEqual(fixture.items[0].proposal);
});
