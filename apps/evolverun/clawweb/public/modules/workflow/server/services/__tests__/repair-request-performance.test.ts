// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import { repairRequestDiagnostic } from '../../observability/repair-diagnostics.js';

let f: Awaited<ReturnType<typeof repairFixture>>;
afterEach(() => { f?.raw.close(); vi.restoreAllMocks(); });

it('keeps the unavailable-generation response without reading an unrelated task', async () => {
  f = await repairFixture(1);
  const service = createRepairWorkbenchService(f.db, f.sourcePort);
  await expect(service.revise('owner', 'missing-task', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: 'a'.repeat(64),
    instructions: '', requestId: 'no-execution', expectedAttemptRevision: 1, parentCandidateCommit: null, feedback: '' }))
    .rejects.toMatchObject({ code: 'CAPABILITY_UNAVAILABLE' });
});

it('reads a selection page without loading unrelated frozen revision bodies, while retaining default task metadata', async () => {
  f = await repairFixture(2);
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const shown = await service.candidates('wf-1');
  await service.create('owner', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: shown.inputDigest, instructions: '', requestId: 'initial' });
  const normal = await service.candidates('wf-1');
  expect(normal.tasks).toHaveLength(1);
  const reads = vi.spyOn(f.db, 'query');
  const light = await service.candidates('wf-1', { includeTasks: false });
  expect(light.tasks).toEqual([]);
  expect(light.items).toEqual(normal.items);
  expect(light.inputDigest).toBe(normal.inputDigest);
  expect(reads.mock.calls.filter(([sql]) => sql.includes('FROM workflow_repair_revisions'))).toHaveLength(0);
});

it.each([false, true])('freezes selected evidence once per revision with selection proof=%s, keeping retries idempotent', async proof => {
  f = await repairFixture(2);
  f.sourcePort.hydrate = async (_tx, _workflowId, items) => structuredClone(items);
  f.items[0].context = { diagnoses: [{ flowId: 'run-1', evidence: [{ eventId: 'event-1', payload: { text: 'original evidence' } }] }] };
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const shown = await service.candidates('wf-1');
  const initial = await service.create('owner', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: shown.inputDigest, instructions: '', requestId: 'initial' });
  const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: initial.input.baseline.packCommit, packDigest: 'e'.repeat(40), itemResults: [{ itemId: 'item-0', status: 'changed', reason: 'fixed' }] };
  await service.report({ workflowId: 'wf-1', taskId: initial.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded', draft,
    checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest, candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: [{ itemId: 'item-0' }] } });
  const state = await service.selectionState('wf-1', ['item-0', 'item-1'], true);
  const sources = vi.spyOn(f.sourcePort, 'load'), reads = vi.spyOn(f.raw, 'prepare');
  const request = { workflowId: 'wf-1', itemIds: ['item-0', 'item-1'], inputDigest: state.inputDigest, instructions: 'retain requirements',
    ...(proof ? { selection: Object.fromEntries(state.items.map(item => [item.itemId, item.selectionDigest!])) } : {}),
    requestId: 'revise-once', expectedAttemptRevision: 1, parentCandidateCommit: draft.candidateCommit, feedback: 'keep evidence' };
  let stages: Record<string, number> = {};
  const next = await repairRequestDiagnostic(async context => {
    const row = await service.revise('owner', initial.taskId, request);
    stages = context.stagesMs;
    return row;
  });
  expect(next.revision).toBe(2);
  expect(next.input.items.map(item => item.itemId)).toEqual(['item-0', 'item-1']);
  expect(next.input.items[0].context?.diagnoses).toMatchObject([{ evidence: [{ eventId: 'event-1', payload: { text: 'original evidence' } }] }]);
  expect(sources).toHaveBeenCalledTimes(1);
  expect(reads.mock.calls.filter(([sql]) => sql.includes('FROM workflow_repair_revisions') && sql.includes('ORDER BY revision DESC') && !sql.includes('LIMIT 1'))).toHaveLength(2);
  expect(stages).toHaveProperty('revision_write');
  expect(stages).toHaveProperty('ais_submit');
  expect((await service.revise('owner', initial.taskId, request)).revision).toBe(2);
  expect(sources).toHaveBeenCalledTimes(1);
  expect(f.calls).toHaveLength(2);
});
