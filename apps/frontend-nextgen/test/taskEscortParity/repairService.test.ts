import { repairController } from '@/services/backendApi/harnessFlow/repairController';
import { backendRequest } from '@/services/backendApi/httpClient';
import { repairService } from '@/services/taskEscortParity/repairService';
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = backendRequest as jest.Mock;
beforeEach(() => request.mockReset());
it('uses scoped GETs for candidates/history/diff/original/frozen materials/trials/verification', async () => {
  request.mockResolvedValue({});
  const signal = new AbortController().signal;
  await repairController.candidates(
    'wf',
    'u',
    {
      versionScope: 'current',
      snapshotKey: JSON.stringify(['deployment', 'wf', 1, 11]),
      state: 'all',
      page: 2,
      pageSize: 20,
      includeHistorical: true,
      includeTasks: true,
      nodeId: 'n',
      failureMode: 'timeout',
    },
    signal,
  );
  expect(request.mock.calls[0][1].params).toEqual({
    user_id: 'u',
    workflowId: 'wf',
    versionScope: 'current',
    snapshotKey: JSON.stringify(['deployment', 'wf', 1, 11]),
    state: 'all',
    page: 2,
    pageSize: 20,
    includeHistorical: true,
    includeTasks: true,
    nodeId: 'n',
    failureMode: 'timeout',
  });
  await repairController.task('t/a', 'u', 3, signal);
  expect(request).toHaveBeenLastCalledWith(
    '/openapi/v1/harnessflow/api/workflow-repairs/t%2Fa',
    expect.objectContaining({ params: { user_id: 'u', revisionPage: 3 } }),
  );
  await repairController.diff('t', 2, 'parent', 'u', signal);
  await repairController.item('wf', 'i/a', 'u', signal);
  expect(request.mock.calls.at(-1)[1].params.originalOnly).toBe(true);
  await repairController.material('t', 2, 'i/a', 'u', signal);
  expect(request.mock.calls.at(-1)[0]).toContain('/t/revisions/2/materials/i%2Fa');
  await repairController.trials('t', 20, 'u', signal);
  await repairController.verification('t', 'dep', 'u', signal);
  expect(
    request.mock.calls.every(
      ([, o]) => o.method === 'GET' && o.injectUserId === false && o.params.user_id === 'u' && o.signal === signal,
    ),
  ).toBe(true);
});
it('rejects cross-workflow task identity and never falls back live for frozen evidence', async () => {
  const signal = new AbortController().signal;
  request.mockResolvedValue({ workflowId: 'other', taskId: 't' });
  await expect(repairService.task('wf', 't', 'u', 1, signal)).rejects.toThrow();
  request.mockReset().mockRejectedValue(Object.assign(new Error('gone'), { status: 404 }));
  await expect(repairService.material('t', 2, 'i', 'u', signal)).rejects.toThrow('404');
  expect(request).toHaveBeenCalledTimes(1);
});
it('rejects v1/malformed capabilities rather than using a demo fallback', async () => {
  request.mockResolvedValue({ schemaVersion: 'workflow-repair/v1', workflowId: 'wf', items: [], tasks: [] });
  await expect(repairService.candidates('wf', 'u', {}, new AbortController().signal)).rejects.toThrow();
});
