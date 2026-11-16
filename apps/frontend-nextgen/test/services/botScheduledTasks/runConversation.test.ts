import { backendRequest } from '@/services/backendApi/httpClient';
import { createRoutineClient } from '@/services/botScheduledTasks/service';
import { mapScheduledRoutineRunRecord } from '@/services/scheduledTasks/scheduledTasksService';
import type { RoutineContext } from '@/services/botScheduledTasks/types';

jest.mock('@/services/backendApi/httpClient', () => ({ ...jest.requireActual('@/services/backendApi/httpClient'), backendRequest: jest.fn() }));
const request = backendRequest as jest.Mock;
const context: RoutineContext = {
  bot_id: 'bot', bot_name: 'Bot', owner_id: 'owner', bot_type: 'service', engine: 'openclaw',
  supported: true, can_manage: false, environments: [],
};
beforeEach(() => request.mockReset());

test('history preserves messages and fallback fields without adding provider extensions or changing the addressed runtime', async () => {
  request.mockResolvedValue({ code: 200000, data: { total: 1, items: [{
    run_id: 'same', routine_id: 'job', status: 'error', started_at: null, finished_at: null,
    runtime_stage: 'online', device_uuid: 'instance-b', operator_id: 'caller-b',
    input: 'historical prompt', output: 'partial answer', error: 'failed',
    messages: [{ role: 'assistant', content: 'message' }], private_extension: 'ignored',
  }] } });
  const result = await createRoutineClient('bot', 'owner', 'actor').runs(context, {
    id: 'job', key: 'target', runtime_stage: 'online', device_uuid: 'instance-b', operator_id: 'caller-b',
  });
  expect(request).toHaveBeenCalledWith('/openapi/v1/bots/bot/routines/job/runs', expect.objectContaining({
    params: { user_id: 'actor', entity_id: 'owner', runtime_stage: 'online', device_uuid: 'instance-b',
      operator_id: 'caller-b', page: 1, page_size: 20 },
  }));
  expect(result.items[0]).toMatchObject({
    input: 'historical prompt', output: 'partial answer', error: 'failed',
    messages: [{ role: 'assistant', content: 'message' }], runtime_stage: 'online', device_uuid: 'instance-b',
  });
  expect(result.items[0]).not.toHaveProperty('private_extension');
});

test('older backend responses without execution content remain readable', async () => {
  request.mockResolvedValue({ code: 200000, data: { total: 1, items: [{
    run_id: 'old', routine_id: 'job', status: 'ok', started_at: null, finished_at: null,
  }] } });
  const result = await createRoutineClient('bot', 'owner', 'actor').runs(context, { id: 'job', key: 'old' });
  expect(result.items[0]).toMatchObject({ id: 'old', messages: [] });
  expect(result.items[0].input).toBeUndefined();
});

test('MyTask history retains the same transcript and instance context without treating current task prompt as history', () => {
  const record = mapScheduledRoutineRunRecord({
    run_id: 'run', routine_id: 'job', status: 'error', runtime_stage: 'verify',
    device_uuid: 'instance', operator_id: 'caller', input: 'historical input', output: 'historical output',
    error: 'execution failure', messages: [{ role: 'assistant', content: 'message' }, { private: 'ignored' }],
  });
  expect(record.conversation).toMatchObject({ id: 'run', routineId: 'job', runtime_stage: 'verify',
    device_uuid: 'instance', operator_id: 'caller', input: 'historical input', output: 'historical output',
    error: 'execution failure', messages: [{ role: 'assistant', content: 'message' }] });
  expect(mapScheduledRoutineRunRecord({ run_id: 'old' }).conversation?.input).toBeUndefined();
});
