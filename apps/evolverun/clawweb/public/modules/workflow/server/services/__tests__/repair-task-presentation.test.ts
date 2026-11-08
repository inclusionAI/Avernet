// @vitest-environment node
import { afterEach, expect, it } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
const fixtures: Awaited<ReturnType<typeof repairFixture>>[] = [];
afterEach(async () => { for (const f of fixtures.splice(0)) await f.db.close(); });
it('uses a stable original task title and source in list and detail after feedback changes the scope', async () => {
  const f = await repairFixture(2); fixtures.push(f);
  f.items[0].proposal!.summary = '修复报告输出格式';
  f.items[1].proposal!.summary = '增加搜索超时';
  const w = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const r = await w.create('human', { workflowId:'wf-1', itemIds:['item-0'], inputDigest:(await w.candidates('wf-1')).inputDigest, instructions:'', requestId:'initial' });
  const draft = {parentCandidateCommit:r.input.baseline.packCommit,candidateCommit:'d'.repeat(40),packDigest:'e'.repeat(40),itemResults:[{itemId:'item-0',status:'changed',reason:'fixture'}]};
  await w.report({workflowId:'wf-1',taskId:r.taskId,revision:1,expectedStateVersion:0,status:'succeeded',draft,checks:{schemaVersion:'workflow-repair-checks/v1',candidateCommit:draft.candidateCommit,packDigest:draft.packDigest,candidateDigest:digestRepairJson(draft),static:{status:'passed'},mock:{status:'not_covered'},coverage:[{itemId:'item-0'}]}});
  await w.revise('human', r.taskId, {workflowId:'wf-1',itemIds:['item-1'],inputDigest:(await w.candidates('wf-1')).inputDigest,instructions:'',requestId:'feedback',expectedAttemptRevision:1,parentCandidateCommit:draft.candidateCommit,feedback:'扩大范围'});
  expect((await w.candidates('wf-1')).tasks[0]).toMatchObject({title:'修复报告输出格式',source:'manual',revision:2});
  expect(await w.task(r.taskId)).toMatchObject({title:'修复报告输出格式',source:'manual'});
});
it('summarizes multiple goals by nodes instead of promoting the first warning to the title', async () => {
  const f = await repairFixture(2); fixtures.push(f);
  f.items[0].proposal = null;
  f.items[0].instruction = '当前生效 Pack 已变化，请重新核对原修改的适用性。';
  f.items[0].context = {nodeId:'report'};
  f.items[1].context = {nodeId:'search'};
  const w = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  await w.create('human',{workflowId:'wf-1',itemIds:['item-0','item-1'],inputDigest:(await w.candidates('wf-1')).inputDigest,instructions:'',requestId:'multiple'});
  expect((await w.candidates('wf-1')).tasks[0]).toMatchObject({title:'优化 report、search 节点（2 条建议）',source:'manual'});
});

it.each(['warning','long','missing'])('uses a bounded goal fallback for %s instead of a warning or raw instruction', async kind => {
 const f=await repairFixture(1);fixtures.push(f);
 f.items[0].proposal = kind==='long'?{summary:'报告'.repeat(100)}:null;
 f.items[0].instruction='当前生效 Pack 已变化，请重新核对原修改的适用性。';
 f.items[0].context=kind==='missing'?{}:{nodeId:'report'};
 const w=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort);
 await w.create('human',{workflowId:'wf-1',itemIds:['item-0'],inputDigest:(await w.candidates('wf-1')).inputDigest,instructions:'',requestId:kind});
 expect((await w.candidates('wf-1')).tasks[0]).toMatchObject({title:kind==='missing'?'工作流优化（1 条建议）':'优化 report 节点（1 条建议）',source:'manual'});
});
