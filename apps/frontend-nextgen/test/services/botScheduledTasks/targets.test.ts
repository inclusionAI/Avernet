import { backendRequest, BackendRequestError } from '@/services/backendApi/httpClient';
import { createRoutineClient, mapManagedRoutine } from '@/services/botScheduledTasks/service';
import {
  addressFromKey,
  routineKey,
  validateCreateTarget,
  type RoutineContext,
} from '@/services/botScheduledTasks/types';
import { describeFailure } from '@/services/botScheduledTasks/results';
jest.mock('@/services/backendApi/httpClient', () => {
  const actual = jest.requireActual('@/services/backendApi/httpClient');
  return { ...actual, backendRequest: jest.fn() };
});
const request = backendRequest as jest.Mock;
const scope = { botId: 'bot/a', userId: 'admin', entityId: 'owner' };
const client = () => createRoutineClient(scope.botId, scope.entityId, scope.userId);
const context: RoutineContext = {
  bot_id: 'bot/a',
  bot_name: 'Bot',
  owner_id: 'owner',
  bot_type: 'service',
  engine: 'openclaw',
  caller_mode: false,
  supported: true,
  can_manage: true,
  environments: [],
};
const dto = {
  routine_id: 'job/a',
  bot_id: '',
  owner_id: 'owner',
  name: 'Daily',
  trigger: { type: 'schedule' as const, cron: '0 9 * * *' },
  command: 'Summarize',
  enabled: true,
  timezone: 'UTC',
  model: null,
  timeout_secs: 1800,
  runtime_stage: 'verify' as const,
  device_uuid: 'uuid',
  operator_id: 'caller',
};
const form = {
  name: 'Daily',
  command: 'Summarize',
  cron: '0 9 * * *',
  enabled: true,
  timezone: 'UTC',
  timeoutSecs: 1800,
  runtime_stage: 'verify' as const,
  device_uuid: 'uuid',
};
const ok = (data: unknown, code = 200000) => ({ code, message: 'OK', data, request_id: 'trace' });
beforeEach(() => request.mockReset());
test('default aggregate list keeps stage/operator absent and trusts server total', async () => {
  request.mockResolvedValue(
    ok({ total: 89, items: [dto], failed_targets: [{ runtime_stage: 'online', reason: 'instances_query_timeout' }] }),
  );
  const result = await client().list({}, 3, 20);
  expect(request).toHaveBeenCalledWith(
    '/openapi/v1/bots/bot%2Fa/routines',
    expect.objectContaining({
      method: 'GET',
      params: { user_id: 'admin', entity_id: 'owner', page: 3, page_size: 20 },
    }),
  );
  expect(result.total).toBe(89);
  expect(result.failed_targets).toHaveLength(1);
  expect(result.items[0].bot_id).toBe('bot/a');
});
test('empty partial failure is preserved and classified without internal error text', async () => {
  request.mockResolvedValue(
    ok({ total: 0, items: [], failed_targets: [{ reason: 'new_reason', runtime_stage: 'online' }] }),
  );
  const result = await client().list();
  expect(result.items).toEqual([]);
  expect(result.failed_targets).toHaveLength(1);
  expect(describeFailure(result.failed_targets[0])).toContain('目标暂不可用');
});
test('create uses nested trigger, identity query, and body-only target', async () => {
  request.mockResolvedValue(ok(dto, 201000));
  await client().save(context, form);
  const [url, options] = request.mock.calls[0];
  expect(url).toBe('/openapi/v1/bots/bot%2Fa/routines');
  expect(options.method).toBe('POST');
  expect(options.params).toEqual({ user_id: 'admin', entity_id: 'owner' });
  expect(options.data).toMatchObject({
    trigger: { type: 'schedule', cron: '0 9 * * *' },
    runtime_stage: 'verify',
    device_uuid: 'uuid',
  });
  for (const field of ['schedule', 'user_id', 'owner_id', 'entity_id', 'bot_id', 'operator_id'])
    expect(options.data).not.toHaveProperty(field);
});
test('personal create does not add targets; Teclaw online omits UUID; Caller is online only', () => {
  expect(validateCreateTarget({ ...context, bot_type: 'personal' }, form, 'actor')).toEqual({});
  expect(validateCreateTarget({ ...context, engine: 'teclaw' }, { ...form, runtime_stage: 'online' }, 'actor')).toEqual(
    { runtime_stage: 'online' },
  );
  expect(() => validateCreateTarget({ ...context, caller_mode: true }, form, 'actor')).toThrow('Caller');
  expect(
    validateCreateTarget({ ...context, caller_mode: true }, { ...form, runtime_stage: 'online' }, 'actor'),
  ).toMatchObject({ operator_id: 'actor' });
});
test('PATCH/run/history/delete retain the row target and use documented methods', async () => {
  request.mockImplementation((url) =>
    Promise.resolve(
      ok(
        url.endsWith('/runs')
          ? { total: 0, items: [], failed_targets: [] }
          : url.endsWith('/run')
          ? { run_id: 'run', routine_id: dto.routine_id, status: 'completed', started_at: null, finished_at: null }
          : { ...dto, deleted: true },
      ),
    ),
  );
  const task = mapManagedRoutine(dto, scope);
  await client().save(context, form, task);
  await client().toggle(context, task);
  await client().run(context, task);
  await client().runs(context, task, 2, 10);
  await client().remove(context, task);
  expect(request.mock.calls.map(([, options]) => options.method)).toEqual(['PATCH', 'PATCH', 'POST', 'GET', 'DELETE']);
  for (const [url, options] of request.mock.calls) {
    expect(url).toContain('/job%2Fa');
    expect(options.params).toMatchObject({
      user_id: 'admin',
      entity_id: 'owner',
      runtime_stage: 'verify',
      device_uuid: 'uuid',
      operator_id: 'caller',
    });
    expect(options.params).not.toHaveProperty('force');
    expect(options.params).not.toHaveProperty('owner_id');
  }
  expect(request.mock.calls[1][1].data).toEqual({ enabled: false });
  expect(request.mock.calls[2][1].data).toBeUndefined();
  expect(request.mock.calls[3][1].params).toMatchObject({ page: 2, page_size: 10 });
});
test('stage-only published edits/deletes are blocked; toggle handles batch', async () => {
  const task = mapManagedRoutine({ ...dto, device_uuid: undefined, operator_id: undefined }, scope);
  await expect(client().save(context, form, task)).rejects.toThrow('只能启停');
  await expect(client().remove(context, task)).rejects.toThrow('明确的实例');
  request.mockResolvedValue(
    ok({
      results: [{ runtime_stage: 'verify', device_uuid: 'a', success: true, routine: dto, run: null, reason: null }],
      succeeded: 1,
      failed: 0,
      failed_targets: [],
    }),
  );
  const result = await client().toggle(context, task);
  expect(result.batch?.results).toHaveLength(1);
  expect(request.mock.calls[0][1].data).toEqual({ enabled: false });
});
test.each([
  ['completed', 'success'],
  ['failed', 'error'],
  ['unknown', 'warning'],
  ['future-status', 'warning'],
])('trigger status %s yields %s', async (status, level) => {
  request.mockResolvedValue(
    ok({ run_id: 'run', routine_id: dto.routine_id, status, started_at: null, finished_at: null }),
  );
  expect((await client().run(context, mapManagedRoutine(dto, scope))).level).toBe(level);
});
test('history validates target without fallback and preserves duplicate run IDs', async () => {
  const task = mapManagedRoutine({ ...dto, runtime_stage: 'draft', operator_id: undefined }, scope);
  await expect(client().runs(context, task)).rejects.toThrow('不支持草稿态');
  expect(request).not.toHaveBeenCalled();
  request.mockResolvedValue(
    ok({
      total: 2,
      items: [
        { run_id: 'same', routine_id: 'job', status: 'ok', started_at: '2026-11-02T01:00:00Z', finished_at: null },
        { run_id: 'same', routine_id: 'job', status: 'skipped', started_at: '2026-11-02T01:00:00Z', finished_at: null },
      ],
      failed_targets: [],
    }),
  );
  const history = await client().runs(context, mapManagedRoutine(dto, scope));
  expect(history.items.map((item) => item.status)).toEqual(['ok', 'skipped']);
  expect(history.total).toBe(2);
});
test('keys include owner/bot/target, and deep links retain the same address', () => {
  const row = mapManagedRoutine(dto, scope);
  expect(row.key).not.toBe(routineKey({ ...row, owner_id: 'other' }, scope));
  expect(addressFromKey(row.key, scope)).toMatchObject({
    id: dto.routine_id,
    device_uuid: 'uuid',
    operator_id: 'caller',
  });
  expect(addressFromKey(row.key, { ...scope, botId: 'other' })).toBeUndefined();
});
test('six-digit errors keep trace ID and a failed write is never replayed', async () => {
  request.mockResolvedValue({ code: 409000, data: null, message: 'Conflict', request_id: 'trace-409' });
  await expect(client().save(context, form)).rejects.toMatchObject({ requestId: 'trace-409', status: 409 });
  expect(request).toHaveBeenCalledTimes(1);
  request.mockReset();
  request.mockRejectedValue(
    new BackendRequestError('Timeout', { apiPath: '/routines', status: 504, data: { request_id: 'trace-timeout' } }),
  );
  await expect(client().run(context, mapManagedRoutine(dto, scope))).rejects.toThrow('不要重复提交');
  expect(request).toHaveBeenCalledTimes(1);
});
test('metadata uses published Bot APIs rather than custom targets endpoint', async () => {
  request.mockImplementation((url) =>
    Promise.resolve(
      ok(
        url.endsWith('/caller-context')
          ? { bot_call_type: 'caller' }
          : url.endsWith('/editors')
          ? { items: [{ user_id: 'admin', role: 'admin' }] }
          : { bot_id: 'bot/a', bot_name: 'Bot', owner_entity_id: 'owner', bot_type: 'service', engine: 'openclaw' },
      ),
    ),
  );
  const result = await client().context();
  expect(result).toMatchObject({ caller_mode: true, can_manage: true });
  expect(request.mock.calls.every(([url]) => !url.includes('/api/cron'))).toBe(true);
});

test('an explicit list target survives missing DTO target fields, without inventing aggregate targets', async () => {
  request.mockResolvedValue(
    ok({
      total: 1,
      items: [{ ...dto, runtime_stage: null, device_uuid: null, operator_id: null }],
      failed_targets: [],
    }),
  );
  const result = await client().list({ runtime_stage: 'online', device_uuid: 'chosen' });
  expect(result.items[0]).toMatchObject({ runtime_stage: 'online', device_uuid: 'chosen' });
  expect(result.items[0].operator_id).toBeUndefined();
  const aggregate = await client().list();
  expect(aggregate.items[0].runtime_stage).toBeUndefined();
});
test('an HTTP-success error envelope still preserves uncertainty and never retries', async () => {
  request.mockResolvedValue({ code: 504000, message: 'Timeout', data: null, request_id: 'trace-504' });
  await expect(client().run(context, mapManagedRoutine(dto, scope))).rejects.toThrow('不要重复提交');
  expect(request).toHaveBeenCalledTimes(1);
});
