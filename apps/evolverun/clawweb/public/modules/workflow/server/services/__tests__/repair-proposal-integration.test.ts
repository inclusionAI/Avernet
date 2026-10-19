import { afterEach, expect, it, vi } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { createProposalCoordinator, type ProposalModelPort } from '../repair-proposal-integration.js';
import { WorkflowAutomationRepository } from '../../repositories/workflow-automation-repository.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
let f: Awaited<ReturnType<typeof repairFixture>>;
afterEach(() => f?.raw.close());
async function setup(count = 3) {
 f = await repairFixture(count);
 for (const m of repairReleaseMigrations) for (const sql of m.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
 for (const [i,item] of f.items.entries()) { item.context = { signature:'issue', nodeId:'query' };
   item.proposal = { workflowId:'wf-1',baseSpecDigest:'a'.repeat(64),summary:`目标${i}`, operations:[{op:'replace',nodeId:'query',path:'/executor/prompt',value:`目标全文${i}`}] };
   item.sources = [{kind:'diagnosis_candidate',signature:'issue',inputDigest:'a'.repeat(64),candidateId:`c${i}`,analysisId:'a',diagnosisId:`d${i}`,flowId:i<2?'run-1':'run-2'}];
 }
 const workbench = createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort);
 let time=1000; const classify=vi.fn(async(input:any) => ({ plans:[{itemIds:input.items.map((i:any)=>i.itemId),title:'完善查询输入、调用与输出约束',changes:['前置校验输入','明确工具调用','限制输出'],relation:'complementary'}],conflicts:[] }));
 const model: ProposalModelPort = { key:'local', classify };
 const read=async(wf:string) => workbench.proposalSnapshot(wf);
 const c=createProposalCoordinator({db:f.db,read,model,now:()=>time,random:()=>0});
 return {c,workbench,classify,read,advance:(n:number)=>time+=n,model};
}
it('backfills existing sources without new analyses or page reads and shares durable results after restart',async()=>{
 const {c,read,model,classify,advance}=await setup();
 await c.tick(); advance(3000); await c.tick();
 expect(classify).toHaveBeenCalledTimes(1);
 const {items,baseline}=await read('wf-1');
 const cards=await c.present('wf-1',items,baseline);
 expect(cards).toHaveLength(1);expect(cards[0].aggregation?.itemIds).toEqual(['item-0','item-1','item-2']);
 expect(cards[0].aggregation?.flowIds).toEqual(['run-1','run-2']);
 const restarted=createProposalCoordinator({db:f.db,read,model,now:()=>5000});
 expect((await restarted.present('wf-1',items,baseline))[0].aggregation?.id).toBe(cards[0].aggregation?.id);
 const plan=await c.prepare('wf-1',items,baseline);
 expect(plan.instructions).toBe('');expect(plan.integration?.plans[0].originalItemIds).toContain('item-2');
 expect(items[0].proposal?.operations).toEqual(f.items[0].proposal?.operations);
});
it('deduplicates exact patches but never merges another baseline or loses source identity',async()=>{
 const {c,read}=await setup();f.items[1].proposal=structuredClone(f.items[0].proposal);
 f.items[2].proposal!.baseSpecDigest='e'.repeat(64);
 const {items,baseline}=await read('wf-1');const cards=await c.present('wf-1',items,baseline);
 expect(cards).toHaveLength(2);expect(cards[0].aggregation?.itemIds).toEqual(['item-0','item-1']);
 await expect(c.prepare('wf-1',[items[2]],baseline)).rejects.toMatchObject({details:{reasonCode:'PROPOSAL_BASELINE_CHANGED'}});
 expect(items[2].proposal?.baseSpecDigest).toBe('e'.repeat(64));
});
it('returns bounded current-plan authorization when rechecking selected original identities',async()=>{
 const {c,read,advance,classify}=await setup(2);
 await c.tick();advance(3000);await c.tick();
 const service=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort,c);
 const before=await read('wf-1'),cards=await c.present('wf-1',before.items,before.baseline);
 const state=await service.selectionState('wf-1',['item-0','item-1'],false);
 expect(state.plans[0]).toMatchObject({id:cards[0].aggregation!.id,fingerprint:cards[0].aggregation!.fingerprint,status:'ready',relation:'complementary',itemIds:['item-0','item-1']});
 expect(state.plans[0]).not.toHaveProperty('originals');
 expect(state.items.every(item=>!!item.selectionDigest)).toBe(true);
 expect(classify).toHaveBeenCalledTimes(1);
 expect(await f.db.query('SELECT * FROM workflow_repair_revisions')).toHaveLength(0);
});
it('keeps changed-baseline originals as manual draft goals without requiring classification authorization',async()=>{
 const {c,advance,classify}=await setup(2);
 f.items.forEach(item=>{item.proposal!.baseSpecDigest='e'.repeat(64)});
 await c.tick();advance(3000);await c.tick();
 expect(classify).toHaveBeenCalledTimes(1);
 expect(classify.mock.calls[0][0].baseline.specDigest).toBe('a'.repeat(64));
 expect(classify.mock.calls[0][0].items.every((item:any)=>item.proposal.baseSpecDigest==='e'.repeat(64))).toBe(true);
 const service=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort,c);
 const page=await service.candidates('wf-1');
 expect(page.items[0].aggregation).toMatchObject({status:'ready',relation:'complementary',reevaluation:true,itemIds:['item-0','item-1']});
 const state=await service.selectionState('wf-1',['item-0','item-1'],false);
 const created=await service.create('human',{workflowId:'wf-1',itemIds:['item-0','item-1'],inputDigest:page.inputDigest,selection:Object.fromEntries(state.items.map(item=>[item.itemId,item.selectionDigest!])),instructions:'preserve current constraints',requestId:'stale-pair'});
 expect(created.input.items.map(item=>item.itemId)).toEqual(['item-0','item-1']);
 expect(f.items.every(item=>item.proposal!.baseSpecDigest==='e'.repeat(64))).toBe(true);
 expect(f.calls).toHaveLength(1);
});
it('generates one selected original without inheriting an unselected plan member',async()=>{
 const {c,advance}=await setup(2);
 f.items.forEach(item=>{item.proposal!.baseSpecDigest='e'.repeat(64)});
 await c.tick();advance(3000);await c.tick();
 const service=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort,c);
 const state=await service.selectionState('wf-1',['item-1'],false);
 expect(state.plans[0].itemIds).toEqual(['item-0','item-1']);
 const created=await service.create('human',{workflowId:'wf-1',itemIds:['item-1'],inputDigest:state.inputDigest,selection:{'item-1':state.items[0].selectionDigest!},instructions:'keep the original goal',requestId:'one-original'});
 expect(created.input.items.map(item=>item.itemId)).toEqual(['item-1']);
 expect(f.calls).toHaveLength(1);
});
it.each([false,true])('never lets a model combine contradictory scalars or omit originals (stale=%s)',async(stale)=>{
 const {c,read,classify,advance}=await setup(2);
 f.items.forEach((x,i)=>{if(stale)x.proposal!.baseSpecDigest='e'.repeat(64);x.proposal!.operations=[{op:'replace',nodeId:'query',path:'/timeout',value:i?240:120}]});
 await c.tick();advance(3000);await c.tick();const {items,baseline}=await read('wf-1');
 await expect(c.prepare('wf-1',items,baseline)).rejects.toThrow(stale?/基线/:/取舍/);
 expect((await c.present('wf-1',items,baseline))).toHaveLength(2);
});
it('retries 429 after Retry-After across instances, but blocks permanent failure without a hot loop',async()=>{
 const {c,classify,advance,read,model}=await setup();
 classify.mockRejectedValueOnce(Object.assign(new Error('limited'),{status:429,retryAfterMs:60000}));
 await c.tick();advance(3000);await Promise.all([c.tick(),createProposalCoordinator({db:f.db,read,model,now:()=>4000}).tick()]);
 expect(classify).toHaveBeenCalledTimes(1);
 const budget=(await f.db.query<{body_json:string}>("SELECT body_json FROM workflow_repair_release_records WHERE record_kind = 'proposal_budget'"))[0];
 expect(JSON.parse(budget.body_json)).toMatchObject({leaseToken:null,leaseUntilMs:0,nextAtMs:64000});
 advance(59000);await c.tick();expect(classify).toHaveBeenCalledTimes(1);
 advance(2000);await c.tick();expect(classify).toHaveBeenCalledTimes(2);
});
it('discards expired model output when sources change mid-flight',async()=>{
 const {c,classify,advance,read}=await setup();let finish!:(v:any)=>void;
 classify.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}));
 await c.tick();advance(3000);const running=c.tick();await vi.waitFor(()=>expect(finish).toBeTypeOf('function'));
 f.items[0].contentRevision++;f.items[0].proposal!.operations=[{op:'replace',nodeId:'query',path:'/executor/prompt',value:'new'}];
 finish({plans:[{itemIds:f.items.map(i=>i.itemId),title:'stale',changes:['old'],relation:'complementary'}],conflicts:[]});await running;
 const {items,baseline}=await read('wf-1');expect((await c.present('wf-1',items,baseline)).every(i=>i.aggregation?.status!=='ready')).toBe(true);
 await expect(c.prepare('wf-1',items,baseline)).rejects.toThrow(/整合/);
});

it('resumes bounded historical batches and source revisions after restart without truncating all sources',async()=>{
 const {c,read,model,classify,advance}=await setup(205);
 await c.tick();advance(3000);await c.tick();
 const restart=createProposalCoordinator({db:f.db,read,model,now:()=>10000});
 await restart.tick();advance(10000);await c.tick();await c.tick();
 const {items,baseline}=await read('wf-1');const cards=await c.present('wf-1',items,baseline);
 expect(new Set(cards.flatMap(card=>card.aggregation!.itemIds)).size).toBe(205);
 expect(classify.mock.calls.every(call=>call[0].items.length<=100)).toBe(true);
 // Queue order can put either a 100-source or a 5-source unit first. A large
 // unit exhausts this window; the separate budget tests assert exact deadlines.
 expect(classify.mock.calls.length).toBeGreaterThanOrEqual(1);
 expect(classify.mock.calls.length).toBeLessThan(3);
 for(let window=0;window<2;window++){advance(60000);await c.tick();await c.tick();}
 expect(classify).toHaveBeenCalledTimes(3);
 const final=await read('wf-1');expect((await c.present('wf-1',final.items,final.baseline)).every(i=>i.aggregation?.status==='ready')).toBe(true);
});
it('blocks a permanent provider failure across jobs until explicit recovery',async()=>{
 const {c,read,classify,advance}=await setup();classify.mockRejectedValueOnce(Object.assign(new Error('unauthorized'),{status:401}));
 await c.tick();advance(3000);await c.tick();advance(600000);await c.tick();await c.tick();
 expect(classify).toHaveBeenCalledTimes(1);
 const snapshot=await read('wf-1');expect((await c.present('wf-1',snapshot.items,snapshot.baseline)).every(i=>i.aggregation?.status==='unavailable')).toBe(true);
 await c.retry('wf-1');advance(2000);await c.tick();expect(classify).toHaveBeenCalledTimes(2);
});
it('freezes all original sources and the actual consumer protocol in one generation input',async()=>{
 const {c,advance}=await setup();await c.tick();advance(3000);await c.tick();
 const integrated=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort,c);
 const page=await integrated.candidates('wf-1');expect(page.items).toHaveLength(1);
 const revision=await integrated.create('reviewer',{workflowId:'wf-1',itemIds:page.items[0].aggregation!.itemIds,inputDigest:page.inputDigest,instructions:'keep business behavior',requestId:'integrated-generation'});
 expect(revision.input.instructions).toBe('keep business behavior');
 expect(revision.input.items.map(i=>i.itemId)).toEqual(['item-0','item-1','item-2']);
 expect(revision.input.integration).toBeUndefined();
 expect(revision.input.items.every(item=>item.context?.evidencePacket)).toBe(true);
 expect(f.calls).toHaveLength(1);
});
it('a model omitting or inventing a source fails safely without hiding original suggestions',async()=>{
 const {c,classify,advance,read}=await setup();classify.mockResolvedValueOnce({plans:[{itemIds:['invented'],title:'unsafe',changes:[],relation:'equivalent'}],conflicts:[]});
 await c.tick();advance(3000);await c.tick();const {items,baseline}=await read('wf-1');
 expect((await c.present('wf-1',items,baseline)).flatMap(i=>i.aggregation?.itemIds??[]).sort()).toEqual(['item-0','item-1','item-2']);
 await expect(c.prepare('wf-1',items,baseline)).rejects.toThrow(/整合/);
});

it('preserves supported vs unknown cause references and rejects invented evidence',async()=>{
 const {c,classify,advance,read}=await setup(2);
 f.items[0].context!.diagnoses=[{flowId:'run-1',nodeId:'query',reasoning:'input missing',evidence:[{eventId:'e1',payload:{error:'missing input'}}],analysisContext:{facts:['input missing'],inferences:[],unknowns:[]}}];
 classify.mockResolvedValueOnce({plans:[{itemIds:['item-0','item-1'],title:'整合查询约束',changes:['校验输入'],relation:'complementary'}],conflicts:[],causes:[{title:'输入缺失',certainty:'supported',itemIds:['item-0'],evidenceIds:['invented']}],unknowns:[]});
 await c.tick();advance(3000);await c.tick();
 const {items,baseline}=await read('wf-1');const cards=await c.present('wf-1',items,baseline);
 expect(cards.every(card=>card.aggregation?.status==='unavailable')).toBe(true);
});

it('records missing evidence for review without blocking a manual draft',async()=>{
 const {c}=await setup(1);
 f.items[0].context!.diagnoses=[{flowId:'run-1',nodeId:'query',evidence:[{eventId:'missing',missing:true}]}];
 const integrated=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort,c);
 const page=await integrated.candidates('wf-1');
 const created=await integrated.create('reviewer',{workflowId:'wf-1',itemIds:['item-0'],inputDigest:page.inputDigest,instructions:'',requestId:'evidence-missing'});
 expect(created.input.items[0].context?.evidencePacket).toMatchObject({missingEvidence:true});
 expect(f.calls).toHaveLength(1);
});

it('serves original suggestions without pretending they are queued before a job exists',async()=>{
 const {c}=await setup();const integrated=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort,c);
 const page=await integrated.candidates('wf-1',{previewSignatures:['issue']});
 expect(page.items).toHaveLength(3);expect(page.items.every(i=>i.aggregation?.status==='uncertain')).toBe(true);
 expect(JSON.parse(JSON.stringify(page)).items).toHaveLength(3);
});

it.each(['requests','tokens'] as const)('releases the %s budget lease and another instance resumes exactly at the next window',async(limit)=>{
 const {c,read,model,classify,advance}=await setup();
 const repo=new WorkflowAutomationRepository(f.db);
 const budget=await repo.ensure('wf-1','proposal_budget',model.key,{window:1000,tokens:limit==='tokens'?200000:0,requests:limit==='requests'?20:0},1000);
 await c.tick();advance(3000);await c.tick();
 expect(classify).not.toHaveBeenCalled();
 const cooldown=await repo.get(budget.id);
 expect(cooldown).toMatchObject({leaseToken:null,leaseUntilMs:0,nextAtMs:61000});
 let restartTime=60999;
 const restarted=createProposalCoordinator({db:f.db,read,model,now:()=>restartTime});
 await restarted.tick();expect(classify).not.toHaveBeenCalled();
 restartTime=61000;await restarted.tick();expect(classify).toHaveBeenCalledTimes(1);
});

it('retains a reviewed shared cause in every affected frozen plan without adding unselected sources',async()=>{
 const {c,read,classify,advance}=await setup();
 f.items.forEach((item,i)=>{(item.proposal!.operations as Array<Record<string,unknown>>)[0].nodeId=`node-${i}`});
 const shared={title:'共同输入缺失',certainty:'hypothesis',itemIds:['item-0','item-1'],evidenceIds:[]};
 classify.mockResolvedValueOnce({plans:f.items.map(item=>({itemIds:[item.itemId],title:`修复 ${item.itemId}`,changes:['校验输入'],relation:'complementary'})),conflicts:[],causes:[shared],unknowns:[]});
 await c.tick();advance(3000);await c.tick();
 const snapshot=await read('wf-1');
 const reviewed=(await c.present('wf-1',snapshot.items,snapshot.baseline)).filter(card=>card.itemId!=='item-2');
 expect(reviewed.every(card=>card.aggregation?.causes?.[0].itemIds.join(',')==='item-0,item-1')).toBe(true);
 const integrated=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort,c);
 const page=await integrated.candidates('wf-1');
 const revision=await integrated.create('reviewer',{workflowId:'wf-1',itemIds:['item-0','item-1'],inputDigest:page.inputDigest,instructions:'',requestId:'shared-cause'});
 expect(revision.input.items.map(item=>item.itemId)).toEqual(['item-0','item-1']);
 expect(revision.input.integration).toBeUndefined();
 expect(revision.input.items.map(item=>item.proposal)).toEqual(f.items.slice(0,2).map(item=>item.proposal));
});

it.each([false,true])('resolves every original member to the same aggregation details (hydration=%s)',async(hydrate)=>{
 const {c,advance}=await setup();await c.tick();advance(3000);await c.tick();
 if(hydrate)f.sourcePort.hydrate=async(_db,_wf,items)=>items.map(item=>({...item,context:{...item.context,detailEvidence:'inline body'}}));
 const integrated=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort,c);
 const page=await integrated.candidates('wf-1');const expected=page.items[0].aggregation!;
 for(const id of expected.itemIds){
  const detail=await integrated.item('wf-1',id);
  expect(detail.itemId).toBe(id);
  expect(detail.aggregation?.id).toBe(expected.id);
  expect(detail.aggregation?.itemIds).toEqual(expected.itemIds);
  expect(detail.aggregation?.title).toBe(expected.title);
  expect(detail.aggregation?.status).toBe('ready');
  expect(detail.aggregation?.originals.map(item=>item.itemId)).toEqual(expected.itemIds);
  expect(detail.aggregation?.originals.every(item=>!!item.readSummary)).toBe(true);
  if(hydrate)expect((await integrated.item('wf-1',id,true)).context?.detailEvidence).toBe('inline body');
 }
});

it.each([false,true])('accepts supported evidence only from a cause member (cross-item cause=%s)',async(shared)=>{
 const {c,read,classify,advance}=await setup(2);
 f.items[0].context!.diagnoses=[{flowId:'run-0',evidence:[{eventId:'event-a',payload:{observation:'A'}}]}];
 f.items[1].context!.diagnoses=[{flowId:'run-1',evidence:[{eventId:'event-b',payload:{observation:'B'}}]}];
 const cause={title:'B evidence',certainty:'supported',itemIds:shared?['item-0','item-1']:['item-0'],evidenceIds:['event-b']};
 classify.mockResolvedValueOnce({plans:[{itemIds:['item-0','item-1'],title:'Integrate both goals',changes:['both goals'],relation:'complementary'}],conflicts:[],causes:[cause],unknowns:[]});
 await c.tick();advance(3000);await c.tick();
 const snapshot=await read('wf-1');const cards=await c.present('wf-1',snapshot.items,snapshot.baseline);
 if(shared){expect(cards).toHaveLength(1);expect(cards[0].aggregation?.causes).toEqual([cause]);}
 else {
  expect(cards.every(card=>card.aggregation?.status==='unavailable')).toBe(true);
  expect(cards.flatMap(card=>card.aggregation?.causes??[])).toEqual([]);
  await expect(c.prepare('wf-1',snapshot.items,snapshot.baseline)).rejects.toThrow(/整合/);
 }
});

it('organizes version-bound sources without reading Git or using the active deployment as their fingerprint',async()=>{
  f=await repairFixture(2);
  for(const m of repairReleaseMigrations)for(const sql of m.sql)await f.db.exec(f.db.dialect.renderDdl(sql));
  const binding={status:'resolved' as const,snapshot:{kind:'deployment' as const,workflowId:'wf-1',workflowVersion:39,deployNumber:34,configDigest:'a'.repeat(64)}};
  f.items.forEach(item=>{item.context={sourceSnapshot:binding};});
  f.sourcePort.activeSnapshot=async()=>binding;
  f.executionPort.resolveBaseline=async()=>{throw new Error('Git manifest is unavailable');};
  let time=1000;let service:ReturnType<typeof createRepairWorkbenchService>;
  const classify=vi.fn(async({items}:any)=>({plans:[{itemIds:items.map((i:any)=>i.itemId),title:'Compatible goals',changes:['Preserve both goals'],relation:'complementary'}],conflicts:[]}));
  const c=createProposalCoordinator({db:f.db,now:()=>time,read:workflowId=>service.proposalSnapshot(workflowId),model:{key:'local',classify}});
  service=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort,c);
  await c.tick();time+=3000;await c.tick();
  expect(classify).toHaveBeenCalledTimes(1);
  const result=await service.candidates('wf-1');
  expect(result.items).toHaveLength(1);
  expect(result.items[0].aggregation?.status).toBe('ready');
  expect(result.items[0].aggregation?.originals).toHaveLength(2);
});
