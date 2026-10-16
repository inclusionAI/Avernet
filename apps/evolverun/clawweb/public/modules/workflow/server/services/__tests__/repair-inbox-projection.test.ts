// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { inboxPages, inboxSummary, inboxText } from '../repair-inbox-projection.js';
import { selectionConflicts } from '../../contracts/repair-selection.js';
import { expandProposalCards, proposalChecked, proposalPending } from '../../contracts/repair-proposal.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { createProposalCoordinator } from '../repair-proposal-integration.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { repairFixture } from './repair-batch-fixtures.js';
import type { RepairInboxItem } from '../../contracts/repair-workbench.js';
import type { RepairRevision } from '../../contracts/repair-batch.js';
import { RepairBatchRepository } from '../../repositories/repair-batch-repository.js';

const fixtures: Awaited<ReturnType<typeof repairFixture>>[] = [];
afterEach(async () => { vi.restoreAllMocks(); for (const f of fixtures.splice(0)) await f.db.close(); });
const item = (id: string, operation: Record<string,unknown>, large = false): RepairInboxItem => ({
  itemId:id,groupKey:'g',proposalKey:'p',contentRevision:1,previousItemId:null,
  proposal:{summary:'修复输入',operations:[operation]},instruction:'',sources:[],context:{diagnoses:[],note:large?'证'.repeat(5000):''},
  workflowId:'wf-1',episodeKey:'initial',state:'pending',stateVersion:0,sourceAvailable:true,
  activeTaskId:null,activeRevision:null,disposition:null,updatedAtMs:0,
});
it.each([
  [{op:'replace',path:'/prompt',value:'same'}, {op:'replace',path:'/prompt',value:'same'}, false],
  [{op:'replace',path:'/prompt',value:'first'}, {op:'replace',path:'/prompt',value:'second'}, true],
  [{op:'replace',path:'/prompt',value:{}}, {op:'replace',path:'/prompt/child',value:'v'}, true],
  [{op:'remove',path:'/prompt'}, {op:'remove',path:'/prompt'}, false],
  [{op:'replace',path:'/prompt'}, {op:'replace',path:'/prompt',value:null}, true],
  [{op:'test',path:'/prompt',value:'v'}, {op:'replace',path:'/prompt',value:'other'}, false],
  [{path:'/config',value:{Z:1,a:2,'中文':3}}, {path:'/config',value:{'中文':3,a:2,Z:1}}, false],
  [{path:'/config',value:{Z:1,a:2}}, {path:'/config',value:{Z:1,a:3}}, true],
])('keeps mixed full/summary structural conflict decisions: %j', (left,right,conflicts) => {
  const projected = inboxSummary(item('a',left,true)), full = item('b',right);
  expect(!!selectionConflicts([projected,full]).length).toBe(conflicts);
  expect(!!selectionConflicts([projected,inboxSummary({...full,context:{note:'x'.repeat(5000)}})]).length).toBe(conflicts);
});
it('bounds multibyte display text without corrupting unicode', () => {
  const text = inboxText('中文🙂'.repeat(1000),100);
  expect(Buffer.byteLength(text)).toBeLessThanOrEqual(100);
  expect(text).not.toContain('\uFFFD');
  expect(text.endsWith('…')).toBe(true);
  expect(inboxText('')).toBe('');
});
it('paginates by bytes without removing rows or partially clipping a group', () => {
  const rows = Array.from({length:30},(_,i)=>item(String(i),{path:'/x',value:'x'.repeat(2000)}));
  const pages = inboxPages(rows,20,10000);
  expect(pages.length).toBeGreaterThan(2);
  expect(pages.flat().map(row=>row.itemId)).toEqual(rows.map(row=>row.itemId));
  expect(pages.every(page=>Buffer.byteLength(JSON.stringify(page))<=10000)).toBe(true);
  expect(inboxPages([],20)).toEqual([[]]);
});
it('retains every member and frozen original when a real exact plan contains large source contexts',async()=>{
  const f=await repairFixture(10);fixtures.push(f);
  for(const m of repairReleaseMigrations) for(const sql of m.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
  f.items.forEach((row,i)=>{row.proposal={summary:'same',baseSpecDigest:'a'.repeat(64),operations:[{op:'replace',nodeId:'query',path:'/prompt',value:'exact prompt'}]};row.context={signature:'timeout',diagnoses:[{flowId:`run-${i}`,reasoning:'证'.repeat(10000)}]};});
  const raw=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort);
  const coordinator=createProposalCoordinator({db:f.db,read:workflowId=>raw.proposalSnapshot(workflowId)});
  const service=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort,coordinator);
  const page=await service.candidates('wf-1');
  expect(page.page.total).toBe(1);expect(Buffer.byteLength(JSON.stringify(page))).toBeLessThan(32*1024);
  const card=page.items[0],selected=expandProposalCards([card]);
  expect(selected.map(row=>row.itemId).sort()).toEqual(f.items.map(row=>row.itemId).sort());
  expect(card.readSummary?.runCount).toBe(10);expect(proposalChecked(card,selected.map(row=>row.itemId))).toBe(true);
  expect(proposalPending(selected)).toBe(false);
  const revision=await service.create('test',{workflowId:'wf-1',inputDigest:page.inputDigest,itemIds:selected.map(row=>row.itemId),instructions:'',requestId:'complete'});
  expect(revision.input.items).toHaveLength(10);
  expect(revision.input.items.every(row=>!('readSummary' in row))).toBe(true);
  expect((revision.input.items[0].context?.diagnoses as Array<{reasoning:string}>)[0].reasoning).toBe('证'.repeat(10000));
  expect(revision.input.integration?.plans[0].originalItemIds).toHaveLength(10);
});
it('pages complete metadata and retains trusted raw reads for legacy automatic selection',async()=>{
  const f=await repairFixture(125);fixtures.push(f);
  f.items.forEach((row,i)=>{row.context={signature:`issue-${i}`,nodeId:`node-${i}`,failureMode:`mode-${i}`,diagnoses:[{severity:'critical',flowId:`run-${i}`,reasoning:'x'.repeat(5000)}]};});
  const service=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort);
  f.items[0].proposal!.summary='中文'.repeat(100000);
  vi.spyOn(RepairBatchRepository.prototype,'listRevisions').mockResolvedValue(Array.from({length:180},(_,i)=>({taskId:`task-${i}`,revision:1,phase:'review',updatedAtMs:i,input:{items:[f.items[0]]}})) as RepairRevision[]);
  const pages=await Promise.all([1,2,3,4].map(metadataPage=>service.candidates('wf-1',{metadataPage,pageSize:1})));
  expect(pages[0].metadata).toMatchObject({totalPages:4,tasks:180,nodes:125,failureModes:125,signatureKeys:125});
  expect(new Set(pages.flatMap(page=>page.tasks.map(task=>task.taskId))).size).toBe(180);
  expect(pages.flatMap(page=>page.tasks).every(task=>(task.title?.length??0)<=72)).toBe(true);
  expect(new Set(pages.flatMap(page=>page.repairSignatureKeys)).size).toBe(125);
  expect(new Set(pages.flatMap(page=>page.facets.nodes)).size).toBe(125);
  expect(new Set(pages.map(page=>page.metadata?.digest)).size).toBe(1);
  const raw=await service.candidates('wf-1',{pageSize:1,rawItems:true});
  expect(raw.items[0].readSummary).toBeUndefined();
  expect(raw.items[0].context?.diagnoses).toEqual(f.items[0].context?.diagnoses);
});
