import { targetParams, routineKey, type RoutineContext } from '@/services/botScheduledTasks/types';
import { createRoutineClient, mapManagedRoutine } from '@/services/botScheduledTasks/service';
import { backendRequest } from '@/services/backendApi/httpClient';
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = backendRequest as jest.Mock;
const context: RoutineContext = {
  bot_id: 'b',
  bot_name: 'Bot',
  owner_id: 'owner',
  bot_type: 'service',
  engine: 'openclaw',
  caller_mode: false,
  supported: true,
  can_manage: true,
  environments: [],
};
beforeEach(() => request.mockReset());
test('personal keeps the legacy shape and does not accidentally select a Caller', () => {
  expect(
    targetParams({ ...context, bot_type: 'personal' }, { runtime_stage: 'online', device_uuid: 'x' }, 'actor'),
  ).toEqual({});
});
test('Teclaw online always omits UUID, including stale values', () => {
  expect(
    targetParams({ ...context, engine: 'teclaw' }, { runtime_stage: 'online', device_uuid: 'stale' }, 'actor'),
  ).toEqual({ runtime_stage: 'online', operator_id: 'actor' });
});
test('Caller rejects non-production and service requires explicit instance', () => {
  expect(() =>
    targetParams({ ...context, caller_mode: true }, { runtime_stage: 'draft', device_uuid: 'x' }, 'actor'),
  ).toThrow('Caller');
  expect(() => targetParams(context, { runtime_stage: 'verify' }, 'actor')).toThrow('运行实例');
});
test('routing retains the task Caller identity when another administrator opens it', () => {
  expect(
    targetParams(context, { runtime_stage: 'online', device_uuid: 'x', operator_id: 'creator' }, 'admin').operator_id,
  ).toBe('creator');
  expect(routineKey({ id: 'same', runtime_stage: 'draft', device_uuid: 'x' })).not.toBe(
    routineKey({ id: 'same', runtime_stage: 'online', device_uuid: 'x' }),
  );
});
test('normalizes schedule and preserves target metadata', () => {
  expect(
    mapManagedRoutine({
      task_id: 'job',
      name: 'Daily',
      schedule: { expr: '0 9 * * *', tz: 'UTC' },
      runtime_stage: 'online',
      device_uuid: 'x',
      operator_id: 'creator',
    }),
  ).toMatchObject({ id: 'job', cron: '0 9 * * *', timezone: 'UTC', device_uuid: 'x', operator_id: 'creator' });
});
test('all mutations and history address the original target, never the viewer target', async () => {
  request.mockResolvedValue({ success: true, data: { runs: [] } });
  const client = createRoutineClient('b', 'owner', 'admin');
  const task = mapManagedRoutine({ task_id: 'job', runtime_stage: 'verify', device_uuid: 'x', operator_id: 'creator' });
  await client.toggle(context, task);
  await client.remove(context, task);
  await client.run(context, task);
  await client.runs(context, task);
  for (const [, options] of request.mock.calls)
    expect(options.params).toMatchObject({
      bot_id: 'b',
      owner_id: 'owner',
      runtime_stage: 'verify',
      device_uuid: 'x',
      operator_id: 'creator',
      user_id: 'admin',
    });
});
test('creation sends routing in body and no unsupported instance parameter for Teclaw online', async () => {
  request.mockResolvedValue({ success: true, data: {} });
  await createRoutineClient('b', 'owner', 'admin').save(
    { ...context, engine: 'teclaw' },
    {
      name: 'Daily',
      command: 'Do work',
      cron: '0 9 * * *',
      enabled: true,
      timeoutSecs: 1800,
      timezone: 'UTC',
      runtime_stage: 'online',
      device_uuid: 'stale',
    },
  );
  expect(request.mock.calls[0][1].data).toMatchObject({
    runtime_stage: 'online',
    operator_id: 'admin',
    schedule: '0 9 * * *',
  });
  expect(request.mock.calls[0][1].data).not.toHaveProperty('device_uuid');
});
test('business failures are never treated as successful saves', async () => {
  request.mockResolvedValue({ success: false, message: '锁已失效' });
  await expect(
    createRoutineClient('b', 'owner', 'admin').remove(
      context,
      mapManagedRoutine({ task_id: 'job', runtime_stage: 'draft', device_uuid: 'x' }),
    ),
  ).rejects.toThrow('锁已失效');
});

test('reads the actual engine payload/state shape without losing instructions or overrides', () => {
  const task = mapManagedRoutine({
    id: 'engine-job',
    enabled: true,
    schedule: { kind: 'cron', expr: '0 9 * * *', tz: 'UTC' },
    payload: { message: 'Generate report', model: 'chosen-model', timeout_secs: 120 },
    state: { next_run_at_ms: 1793577600000, last_run_at_ms: 1793491200000 },
  });
  expect(task).toMatchObject({ id: 'engine-job', command: 'Generate report', model: 'chosen-model', timeoutSecs: 120 });
  expect(task.nextRunAt).toBe(new Date(1793577600000).toISOString());
  expect(task.lastRunAt).toBe(new Date(1793491200000).toISOString());
});

test('invalid engine timestamps cannot break the entire task list', () => {
  expect(mapManagedRoutine({ id: 'job', state: { next_run_at_ms: Infinity } }).nextRunAt).toBeUndefined();
});
