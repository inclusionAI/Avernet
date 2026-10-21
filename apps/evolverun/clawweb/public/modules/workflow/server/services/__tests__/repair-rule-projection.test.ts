// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { createProposalCoordinator } from '../repair-proposal-integration.js';
import { expandProposalCards, proposalConflicts } from '../../contracts/repair-proposal.js';
import type { RepairItem } from '../../contracts/repair-batch.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';

let f: Awaited<ReturnType<typeof repairFixture>>;
afterEach(async () => { await f?.db.close(); vi.restoreAllMocks(); });
function textGoal(item: RepairItem, index: number) {
  item.proposal = null; item.instruction = '将 query 节点超时调整为300秒';
  item.context = { signature: 'query-timeout', nodeId: 'query', failureMode: 'timeout', diagnoses: [{
    diagnosisId: `diagnosis-${index}`, analysisId: `analysis-${index}`, flowId: `flow-${index}`,
    evidence: [{ eventId: `event-${index}`, payload: { observation: `failure-${index}` } }],
  }] };
}

it('folds identical goals without a model and freezes every selected original and its evidence', async () => {
  f = await repairFixture(6); f.items.forEach(textGoal);
  for (const migration of repairReleaseMigrations) for (const sql of migration.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const page = await service.candidates('wf-1');
  expect(page.items).toHaveLength(1);
  expect(page.items[0].aggregation).toMatchObject({ status: 'ready', relation: 'exact',
    itemIds: ['item-0','item-1','item-2','item-3','item-4','item-5'] });
  const state = await service.selectionState('wf-1', page.items[0].aggregation!.itemIds, false);
  expect(state.plans).toHaveLength(1);
  const revision = await service.create('owner', { workflowId: 'wf-1', itemIds: state.items.map(item => item.itemId),
    inputDigest: state.inputDigest, requestId: 'rules-evidence', instructions: '' });
  expect(revision.input.items).toHaveLength(6);
  for (const [index, item] of revision.input.items.entries()) {
    expect(item.sources).toEqual(f.items[index].sources);
    expect(item.context?.diagnoses).toMatchObject([{ diagnosisId: `diagnosis-${index}`, flowId: `flow-${index}`,
      evidence: [{ eventId: `event-${index}`, payload: { observation: `failure-${index}` } }] }]);
    expect(item.context?.evidencePacket).toMatchObject({ originalDiagnoses: 1, missingEvidence: false });
  }
  expect(f.calls).toHaveLength(1);
  const records = await f.db.query("SELECT * FROM workflow_repair_release_records WHERE record_kind IN ('proposal_scan','proposal_group')");
  expect(records).toHaveLength(0);
  const frozen = JSON.stringify(revision.input);
  const restarted = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  expect(JSON.stringify((await restarted.task(revision.taskId)).latestAttempt.input)).toBe(frozen);
});

it('keeps different nodes, versions and constraints separate instead of guessing semantic equivalence', async () => {
  f = await repairFixture(5); f.items.forEach(textGoal);
  f.items[2].instruction = '将 query 节点超时调整为600秒';
  f.items[3].context!.nodeId = 'report';
  for (const [index, item] of f.items.entries()) item.context!.sourceSnapshot = { status: 'resolved', snapshot: {
    kind: 'deployment', workflowId: 'wf-1', workflowVersion: index === 4 ? 2 : 1,
    deployNumber: index === 4 ? 2 : 1, configDigest: (index === 4 ? 'b' : 'a').repeat(64),
  } };
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const page = await service.candidates('wf-1');
  expect(page.items).toHaveLength(4);
  expect(page.items.filter(item => item.aggregation?.itemIds.length === 2)).toHaveLength(1);
});

it('keeps large duplicate sets selectable under the existing original-item limit', async () => {
  f = await repairFixture(101); f.items.forEach(textGoal);
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const page = await service.candidates('wf-1');
  expect(page.items).toHaveLength(2);
  const members = page.items.map(item => item.aggregation?.itemIds ?? [item.itemId]);
  expect(members.map(ids => ids.length)).toEqual([100, 1]);
  expect(new Set(members.flat()).size).toBe(101);
});

it('still detects conflicting values when one side is a folded duplicate card', async () => {
  f = await repairFixture(3);
  for (const [index, item] of f.items.entries()) {
    item.instruction = '';
    item.proposal = { baseSpecDigest: 'a'.repeat(64), summary: '调整超时',
      operations: [{ op: 'replace', nodeId: 'query', path: '/timeout', value: index === 2 ? 600 : 300 }] };
  }
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const page = await service.candidates('wf-1');
  expect(page.items).toHaveLength(2);
  const originals = expandProposalCards(page.items);
  expect(proposalConflicts(originals).flatMap(conflict => conflict.itemIds)).toContain('item-2');
});

it('generates automatic selected goals without consulting a legacy AI plan', async () => {
  f = await repairFixture(2);
  const baseline = await f.executionPort.resolveBaseline('wf-1');
  for (const [index, item] of f.items.entries()) item.proposal = {
    schemaVersion: 'workflow-patch/v1', workflowId: 'wf-1', baseSpecDigest: baseline.specDigest, summary: `节点${index}重试`,
    operations: [{ op: 'replace', nodeId: `node-${index}`, path: '/retry/maxAttempts', value: 2 }],
  };
  const plain = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const legacy = createProposalCoordinator({ db: f.db, read: wf => plain.proposalSnapshot(wf) });
  const prepare = vi.spyOn(legacy, 'prepare').mockRejectedValue(new Error('legacy AI plan unavailable'));
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort, legacy);
  const page = await service.candidates('wf-1');
  const revision = await service.create('owner', { workflowId: 'wf-1', itemIds: ['item-0','item-1'],
    inputDigest: page.inputDigest, instructions: '', requestId: 'automatic-without-ai-plan',
    automation: { kind: 'daily', intentId: 'd'.repeat(64), policyVersion: 1, evidenceCutoffMs: Date.now() } });
  expect(prepare).not.toHaveBeenCalled();
  expect(revision.input.items.map(item => item.itemId)).toEqual(['item-0','item-1']);
  expect(revision.input.integration?.plans.flatMap(plan => plan.originalItemIds)).toEqual(['item-0','item-1']);
  expect(f.calls).toHaveLength(1);
});
