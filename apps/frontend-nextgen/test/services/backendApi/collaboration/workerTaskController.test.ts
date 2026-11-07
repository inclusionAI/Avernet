import { backendRequest } from '@/services/backendApi/httpClient';
import { queryWorkerTaskStatuses, cancelWorkerTask } from '@/services/backendApi/collaboration/workerTaskController';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = jest.mocked(backendRequest);

it('queries BCS worker tasks using full selectors and the bare response', async () => {
  const ref = `bcs_intent_${'1'.repeat(32)}`;
  const body = { tasks: [{ ref, status: 'running' }] };
  request.mockResolvedValueOnce(body);
  expect(await queryWorkerTaskStatuses([ref])).toBe(body);
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/collaboration/worker-tasks/status', {
    method: 'POST', data: { intent_ids: [ref] }, injectUserId: false,
  });
});

it('cancels exactly the requested assignment without a Bot-wide abort', async () => {
  const ref = `bcs_intent_${'2'.repeat(32)}`;
  request.mockResolvedValueOnce({ ref, status: 'unknown' });
  expect(await cancelWorkerTask(ref)).toEqual({ ref, status: 'unknown' });
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/collaboration/worker-tasks/cancel', {
    method: 'POST', data: { intent_id: ref, reason: '用户停止任务' }, injectUserId: false,
  });
});
