import { backendRequest } from '@/services/backendApi/httpClient';
import { readTaskEscortWorkflows } from '@/services/taskEscortParity/workspaceService';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = jest.mocked(backendRequest);

it('uses the verified gateway workflow-types GET, real owner and cancellation, never sampled runs', async () => {
  const signal = new AbortController().signal;
  request.mockResolvedValue({
    workflows: [
      { workflow_id: 'wf-a', workflow_title: 'A', run_count: 0, last_status: 'succeeded', owner_id: 'owner-a' },
      { workflow_id: 'wf-b', workflow_title: null },
      { workflow_id: 'wf-c', run_count: '8', last_status: 2, owner_id: 1 },
    ],
    total: 3,
  });
  await expect(readTaskEscortWorkflows('user-a', signal)).resolves.toEqual([
    { id: 'wf-a', title: 'A', runCount: 0, lastStatus: 'succeeded', ownerId: 'owner-a' },
    { id: 'wf-b', title: 'wf-b', runCount: null, lastStatus: null, ownerId: null },
    { id: 'wf-c', title: 'wf-c', runCount: null, lastStatus: null, ownerId: null },
  ]);
  expect(request).toHaveBeenCalledWith('/openapi/v1/harnessflow/api/runs/workflow-types', {
    method: 'GET',
    params: { botOwnerId: 'user-a', limit: 500 },
    signal,
  });
});

it.each([
  {},
  { workflows: null },
  { workflows: [{}] },
  { workflows: [{ workflow_id: ' ' }] },
  { workflows: [{ workflow_id: 9 }] },
  { workflows: [{ workflow_id: 'a' }, { workflow_id: 'a' }] },
])('rejects malformed envelopes/required IDs rather than fabricating usable records: %j', async (payload) => {
  request.mockResolvedValue(payload);
  await expect(readTaskEscortWorkflows()).rejects.toThrow();
});

it('preserves empty lists, unknown metrics and upstream forbidden without success fallback', async () => {
  request.mockResolvedValueOnce({ workflows: [] });
  await expect(readTaskEscortWorkflows()).resolves.toEqual([]);
  request.mockResolvedValueOnce({ workflows: [{ workflow_id: 'a', run_count: -1, owner_id: ' ', last_status: '' }] });
  await expect(readTaskEscortWorkflows()).resolves.toEqual([
    { id: 'a', title: 'a', runCount: null, ownerId: null, lastStatus: null },
  ]);
  request.mockRejectedValueOnce(new Error('403'));
  await expect(readTaskEscortWorkflows()).rejects.toThrow('403');
});
