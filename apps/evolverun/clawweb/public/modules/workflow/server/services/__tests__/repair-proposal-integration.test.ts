import { afterEach, expect, it, vi } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { createProposalCoordinator, type ProposalModelPort } from '../repair-proposal-integration.js';
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
 await expect(c.prepare('wf-1',[items[2]],baseline)).rejects.toThrow(/基线/);
});
it('never lets a model combine contradictory scalars or omit originals',async()=>{
 const {c,read,classify,advance}=await setup(2);
 f.items.forEach((x,i)=>{x.proposal!.operations=[{op:'replace',nodeId:'query',path:'/timeout',value:i?240:120}]});
 await c.tick();advance(3000);await c.tick();const {items,baseline}=await read('wf-1');
 await expect(c.prepare('wf-1',items,baseline)).rejects.toThrow(/取舍/);
 expect((await c.present('wf-1',items,baseline))).toHaveLength(2);
});
it('retries 429 after Retry-After across instances, but blocks permanent failure without a hot loop',async()=>{
 const {c,classify,advance,read,model}=await setup();
 classify.mockRejectedValueOnce(Object.assign(new Error('limited'),{status:429,retryAfterMs:60000}));
 await c.tick();advance(3000);await Promise.all([c.tick(),createProposalCoordinator({db:f.db,read,model,now:()=>4000}).tick()]);
 expect(classify).toHaveBeenCalledTimes(1);advance(59000);await c.tick();expect(classify).toHaveBeenCalledTimes(1);
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
 expect(classify).toHaveBeenCalledTimes(2); // Shared conservative token budget is exhausted, not a lost batch.
 advance(60000);await c.tick();expect(classify).toHaveBeenCalledTimes(3);
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
 expect(revision.input.integration?.schemaVersion).toBe('workflow-proposal-plan/v1');
 expect(revision.input.integration?.plans[0].originalItemIds).toEqual(['item-0','item-1','item-2']);
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

it('does not generate when critical evidence payloads are absent or modified',async()=>{
 const {c}=await setup(1);
 f.items[0].context!.diagnoses=[{flowId:'run-1',nodeId:'query',evidence:[{eventId:'missing',missing:true}]}];
 const integrated=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort,c);
 const page=await integrated.candidates('wf-1');
 await expect(integrated.create('reviewer',{workflowId:'wf-1',itemIds:['item-0'],inputDigest:page.inputDigest,instructions:'',requestId:'evidence-missing'})).rejects.toThrow(/证据/);
 expect(f.calls).toHaveLength(0);
});

it('serves original queued suggestions through strict bounded JSON before any model completes',async()=>{
 const {c}=await setup();const integrated=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort,c);
 const page=await integrated.candidates('wf-1',{previewSignatures:['issue']});
 expect(page.items).toHaveLength(3);expect(page.items.every(i=>i.aggregation?.status==='queued')).toBe(true);
 expect(JSON.parse(JSON.stringify(page)).items).toHaveLength(3);
});
