import { expect, it } from 'vitest';
import type { RepairBaseline } from '@avernet/workflow/server/contracts/repair-batch';
import type { RepairInboxItem } from '@avernet/workflow/server/contracts/repair-workbench';
import { createProposalModel } from '../repair-proposal-model';

it('classifies frozen source goals without mutating or applying old proposals', async () => {
  const baseline: RepairBaseline = { workflowId: 'wf', packId: 'pack', repoId: 'repo', releaseRevision: 2, activeDeployNumber: 2,
    packCommit: 'b'.repeat(40), packDigest: 'c'.repeat(40), specDigest: 'a'.repeat(64), specPath: 'workflows/wf.yaml' };
  const item: RepairInboxItem = { itemId: 'original', workflowId: 'wf', groupKey: 'group', proposalKey: 'd'.repeat(64),
    contentRevision: 1, previousItemId: null, proposal: { baseSpecDigest: 'e'.repeat(64), summary: 'old goal' }, instruction: '',
    sources: [], context: { evidence: 'original evidence' }, episodeKey: 'initial', state: 'pending', stateVersion: 0,
    sourceAvailable: true, updatedAtMs: 1, activeTaskId: null, activeRevision: null, disposition: null };
  const original = structuredClone(item), signal = new AbortController().signal;
  const grouping = { plans: [{ itemIds: ['original'], title: 'goal', changes: [], relation: 'uncertain' }], conflicts: [] };
  let request: RequestInit | undefined;
  const model = createProposalModel({ apiUrl: 'https://fixture.invalid', apiKey: 'fixture-key', model: 'controlled' }, async (_url, init) => {
    request = init;
    return new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(grouping) } }] }), { status: 200 });
  })!;
  expect(await model.classify({ baseline, items: [item], signal })).toEqual(grouping);
  const body = JSON.parse(request!.body as string);
  expect(body.messages.filter((row: { role: string }) => row.role === 'system').map((row: { content: string }) => row.content).join('\n')).toContain('sourceSnapshot是这些原建议所属的固定运行版本');
  expect(JSON.parse(body.messages.at(-1).content)).toEqual({ baseline, items: [original] });
  expect(request!.signal).toBe(signal);
  const sourceSnapshot = { status: 'resolved' as const, snapshot: { kind: 'deployment' as const, workflowId:'wf',workflowVersion:1,deployNumber:1,configDigest:'e'.repeat(64) } };
  await model.classify({sourceSnapshot,items:[item],signal});
  expect(JSON.parse(JSON.parse(request!.body as string).messages.at(-1).content)).toEqual({sourceSnapshot,items:[original]});
  expect(item).toEqual(original);
});
