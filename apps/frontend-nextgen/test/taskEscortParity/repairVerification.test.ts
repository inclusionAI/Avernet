import { backendRequest } from '@/services/backendApi/httpClient';
import { repairService } from '@/services/taskEscortParity/repairService';
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = backendRequest as jest.Mock;
const expected = { workflowId: 'wf', revision: 2, deployNumber: 12, candidateCommit: 'candidate', itemIds: ['i'] };
const evidence = {
  deploymentId: 'dep',
  deployNumber: 12,
  candidateCommit: 'candidate',
  samples: [],
  total: 0,
  status: 'waiting',
  note: 'no samples',
  metrics: {},
  recurrence: [],
};
const conclusion = {
  id: 'c',
  workflowId: 'wf',
  taskId: 't',
  revision: 2,
  deploymentId: 'dep',
  deployNumber: 12,
  itemId: 'i',
  result: 'verified',
};
beforeEach(() => request.mockReset());
it('filters task-wide conclusions to the selected deployment, never displaying other releases', async () => {
  request.mockResolvedValue({
    evidence,
    conclusions: [conclusion, { ...conclusion, id: 'other', deploymentId: 'old-dep', revision: 1, deployNumber: 8 }],
  });
  const result = await repairService.verification('t', 'dep', 'u', new AbortController().signal, expected);
  expect(result.conclusions).toEqual([conclusion]);
});
it.each([
  { workflowId: 'other' },
  { taskId: 'other' },
  { revision: 3 },
  { deployNumber: 13 },
  { itemId: 'foreign-item' },
])('rejects a selected-deployment conclusion with mismatched membership %j', async (mismatch) => {
  request.mockResolvedValue({ evidence, conclusions: [{ ...conclusion, ...mismatch }] });
  await expect(repairService.verification('t', 'dep', 'u', new AbortController().signal, expected)).rejects.toThrow();
});
it.each([
  { deploymentId: 'other' },
  { deployNumber: 13 },
  { candidateCommit: 'other' },
  { samples: [{ flowId: 'r', deployNumber: 99 }] },
])('rejects evidence inconsistent with the selected successful receipt %j', async (mismatch) => {
  request.mockResolvedValue({ evidence: { ...evidence, ...mismatch }, conclusions: [] });
  await expect(repairService.verification('t', 'dep', 'u', new AbortController().signal, expected)).rejects.toThrow();
});
