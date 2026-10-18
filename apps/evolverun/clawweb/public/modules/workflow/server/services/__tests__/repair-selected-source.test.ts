// @vitest-environment node
import { afterEach, expect, it } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { createProposalCoordinator } from '../repair-proposal-integration.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { digestRepairJson } from '../../contracts/repair-batch.js';
let f: Awaited<ReturnType<typeof repairFixture>>;
afterEach(() => f?.raw.close());
async function setup() {
  f = await repairFixture(2);
  return createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
}
it('accepts unchanged selected originals when unselected sources or supporting evidence change', async () => {
  const service = await setup();
  const shown = await service.candidates('wf-1');
  expect(shown.items[0].selectionDigest).toMatch(/^[a-f0-9]{64}$/);
  f.items[1].proposal!.summary = 'unselected change';
  f.items[0].context = { diagnoses: [{ flowId: 'new-supporting-run' }] };
  const result = await service.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: shown.inputDigest,
    selection: { 'item-0': shown.items[0].selectionDigest! }, instructions: 'keep my requirements', requestId: 'precise' });
  expect(result.input.items[0].context?.diagnoses).toEqual([{ flowId: 'new-supporting-run' }]);
  expect(f.calls).toHaveLength(1);
});
it('identifies changed selected content without accepting unseen values or creating a task', async () => {
  const service = await setup();
  const shown = await service.candidates('wf-1');
  f.items[0].proposal!.summary = 'different selected goal';
  await expect(service.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: shown.inputDigest,
    selection: { 'item-0': shown.items[0].selectionDigest! }, instructions: '', requestId: 'changed' }))
    .rejects.toMatchObject({ code: 'SOURCE_CHANGED', details: { reasonCode: 'SELECTED_ITEMS_CHANGED', items: [{ itemId: 'item-0', reasonCode: 'CONTENT_CHANGED' }] } });
  expect(f.calls).toHaveLength(0);
});
it('keeps stale original operations and evidence while routing even one source through integrated reevaluation', async () => {
  const plain = await setup();
  for (const m of repairReleaseMigrations) for (const sql of m.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
  f.items[0].proposal = { baseSpecDigest: 'e'.repeat(64), summary: 'report Markdown', operations: [{ op: 'replace', nodeId: 'report', path: '/executor/prompt', value: 'old full prompt' }] };
  f.items[0].context = { diagnoses: [{ flowId: 'old-run', evidence: [{ eventId: 'event', payload: { reason: 'bad output' } }] }] };
  const original = structuredClone(f.items[0]);
  const coordinator = createProposalCoordinator({ db: f.db, read: wf => plain.proposalSnapshot(wf) });
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort, coordinator);
  const shown = await service.candidates('wf-1');
  const created = await service.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: shown.inputDigest,
    selection: { 'item-0': shown.items.find(i => i.itemId === 'item-0')!.selectionDigest! }, instructions: '', requestId: 'reevaluate' });
  expect(created.input.integration).toMatchObject({ schemaVersion: 'workflow-proposal-plan/v1', baselineSpecDigest: 'a'.repeat(64), plans: [{ originalItemIds: ['item-0'] }] });
  expect(created.input.items[0].proposal).toEqual(original.proposal);
  expect(created.input.items[0].sources).toEqual(original.sources);
  expect(f.calls).toHaveLength(1);
});
it('reads bounded selection identities for >1MiB evidence but still rejects generation before freezing or dispatch', async () => {
  const service = await setup();
  f.items[0].context = { diagnoses: [{ flowId: 'large-run', evidence: [{ eventId: 'large', payload: { text: 'x'.repeat(1_100_000) } }] }] };
  const state = await service.selectionState('wf-1', ['item-0'], false);
  expect(state.items).toHaveLength(1);
  expect(JSON.stringify(state).length).toBeLessThan(12000);
  expect(state.items[0].selectionDigest).toMatch(/^[a-f0-9]{64}$/);
  await expect(service.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: state.inputDigest,
    selection: { 'item-0': state.items[0].selectionDigest! }, instructions: '', requestId: 'large' })).rejects.toMatchObject({ code: 'PAYLOAD_TOO_LARGE' });
  expect(f.calls).toHaveLength(0);
  expect(await f.db.query('SELECT * FROM workflow_repair_revisions')).toHaveLength(0);
});
it.each(['operations', 'patches'])('does not sequentially overwrite conflicting stale %s without a coordinator', async kind => {
  const service = await setup();
  f.items.forEach((item, i) => { item.proposal = { baseSpecDigest: 'e'.repeat(64), [kind]: kind === 'operations'
    ? [{ op: 'replace', nodeId: 'query', path: '/timeout', value: i ? 240 : 120 }]
    : [{ file: 'workflows/wf-1.yaml', content: i ? 'second' : 'first' }] }; });
  const shown = await service.candidates('wf-1');
  await expect(service.create('human', { workflowId: 'wf-1', itemIds: f.items.map(i => i.itemId), inputDigest: shown.inputDigest,
    instructions: '', requestId: 'conflicts' })).rejects.toMatchObject({ code: 'INVALID_INPUT', details: { reasonCode: 'PLAN_CONFLICT' } });
  expect(f.calls).toHaveLength(0);
});
it('permits the owning task to revise a frozen processing original after its live source disappears', async () => {
  const service = await setup();
  const shown = await service.candidates('wf-1');
  const first = await service.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: shown.inputDigest, instructions: '', requestId: 'owned' });
  const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: first.input.baseline.packCommit, packDigest: 'e'.repeat(40), itemResults: [{ itemId: 'item-0', status: 'changed', reason: 'fixed' }] };
  await service.report({ workflowId: 'wf-1', taskId: first.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded', draft,
    checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest, candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: [{ itemId: 'item-0' }] } });
  f.items.splice(0, 1);
  const current = await service.selectionState('wf-1', ['item-0'], true);
  expect(current.items[0].sourceAvailable).toBe(false);
  const next = await service.revise('human', first.taskId, { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: current.inputDigest,
    selection: { 'item-0': current.items[0].selectionDigest! }, instructions: '', requestId: 'owned-revise', expectedAttemptRevision: 1, parentCandidateCommit: draft.candidateCommit, feedback: 'retain constraints' });
  expect(next.revision).toBe(2);
  expect(next.input.baseline).toEqual(first.input.baseline);
});
it('applies strict evidence freezing to stale reevaluation even without a coordinator', async () => {
  const service = await setup();
  f.items[0].proposal = { baseSpecDigest: 'e'.repeat(64), operations: [{ op: 'replace', nodeId: 'report', path: '/prompt', value: 'old' }] };
  f.items[0].context = { diagnoses: [{ flowId: 'run', evidence: [{ eventId: 'missing', payload: null, missing: true }] }] };
  const shown = await service.candidates('wf-1');
  await expect(service.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: shown.inputDigest,
    instructions: '', requestId: 'missing-evidence' })).rejects.toMatchObject({ code: 'INVALID_INPUT', details: { reasonCode: 'EVIDENCE_INCOMPLETE' } });
  expect(f.calls).toHaveLength(0);
  f.items[0].context = { diagnoses: [{ flowId: 'run', evidence: [{ eventId: 'safe', payload: { text: 'complete evidence', authorization: 'credential-placeholder' } }] }] };
  const fresh = await service.candidates('wf-1');
  const created = await service.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: fresh.inputDigest, instructions: '', requestId: 'safe-evidence' });
  expect(created.input.items[0].context?.evidencePacket).toMatchObject({ missingEvidence: false, clipped: false });
  expect(JSON.stringify(created.input.items[0].context)).not.toContain('credential-placeholder');
});
