import { backendRequest } from '@/services/backendApi/httpClient';
import { launchCollaborationGroup } from '@/services/workspace/collaborationGroupLaunchService';
import type { CreateGroupInput } from '@/services/workspace/groupCreateRequest';

jest.mock('@/services/backendApi/httpClient');
const request = jest.mocked(backendRequest);
const validation = (patch = {}) => ({
  code: 20000,
  data: {
    valid: true,
    errors: [],
    warnings: [],
    summary: { participants: 1, nodes: 1, initial_nodes: ['work'] },
    participants: [{ binding: 'worker', required: true }],
    ...patch,
  },
});
const input = (): CreateGroupInput => ({
  name: '新协作',
  strategy: 'state_machine',
  definitionYaml: 'name: edited\n',
  context: '完成目标',
  driverBotUuid: 'bot-B:owner',
  originator: 'bot-B:owner',
  participants: [{ actor_id: 'bot-B:owner' }, { actor_id: 'worker:owner' }],
  participantBindings: [{ binding: 'worker', actor_ids: ['worker:owner'] }],
  humanMentionNotifyMode: 'all',
});

beforeEach(() => request.mockReset());

it('preflights edited YAML then posts only the OpenAPI group contract', async () => {
  request.mockResolvedValueOnce(validation()).mockResolvedValueOnce({
    code: 20100,
    data: {
      group_id: 'group-1',
      initial_session_id: 'session-1',
      driver_bot_uuid: 'bot-B:owner',
    },
  });
  const result = await launchCollaborationGroup(input());
  expect(result).toEqual({
    ok: true,
    data: {
      groupId: 'group-1',
      initialSessionId: 'session-1',
      driverBotId: 'bot-B:owner',
    },
  });
  expect(request.mock.calls.map(([url]) => url)).toEqual([
    '/api/v1/collaboration/definitions/validate',
    '/openapi/v1/collaboration/groups',
  ]);
  expect(request.mock.calls[0][1]?.data).toEqual({ definition_yaml: 'name: edited\n' });
  expect(request.mock.calls[1][1]?.data).toEqual({
    group_kind: 'normal',
    name: '新协作',
    context: '完成目标',
    originator: 'bot-B:owner',
    driver_bot_uuid: 'bot-B:owner',
    human_mention_notify_mode: 'all',
    participants: [
      { actor_id: 'bot-B:owner', role: 'driver' },
      { actor_id: 'worker:owner', role: 'consultant' },
    ],
    collaboration: {
      strategy: 'state_machine',
      definition: { content_yaml: 'name: edited\n' },
      participant_bindings: [{ binding: 'worker', actor_ids: ['worker:owner'] }],
    },
  });
});

it('preserves empty bindings when all declared participant slots are optional and unbound', async () => {
  request
    .mockResolvedValueOnce(validation({ participants: [{ binding: 'observer', required: false }] }))
    .mockResolvedValueOnce({ code: 20100, data: { group_id: 'group-optional' } });
  const result = await launchCollaborationGroup({
    ...input(),
    participants: [{ actor_id: 'bot-B:owner' }],
    participantBindings: [],
  });
  expect(result).toMatchObject({ ok: true, data: { groupId: 'group-optional' } });
  expect(request.mock.calls[1][1]?.data).toMatchObject({
    collaboration: { strategy: 'state_machine', participant_bindings: [] },
  });
});

it('freezes the driver, YAML, goal, participants and bindings before asynchronous preflight', async () => {
  let finish!: (value: unknown) => void;
  request.mockReturnValueOnce(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  request.mockResolvedValueOnce({ code: 20100, data: { group_id: 'group-1' } });
  const form = input();
  const pending = launchCollaborationGroup(form);
  form.driverBotUuid = 'changed';
  form.definitionYaml = 'changed';
  form.context = 'changed';
  form.participants[0].actor_id = 'changed';
  form.participantBindings![0].actor_ids[0] = 'changed';
  finish(validation());
  expect(await pending).toMatchObject({ ok: true, data: { driverBotId: 'bot-B:owner' } });
  expect(request.mock.calls[1][1]?.data).toMatchObject({
    driver_bot_uuid: 'bot-B:owner',
    context: '完成目标',
    collaboration: {
      definition: { content_yaml: 'name: edited\n' },
      participant_bindings: [{ binding: 'worker', actor_ids: ['worker:owner'] }],
    },
  });
});

it.each([
  { valid: false, errors: [{ code: 'INVALID', path: 'nodes', message: '无效节点' }] },
  { warnings: [{ code: 'VALIDATION_ONLY_FEATURE', path: 'nodes', message: '功能尚未开放' }] },
  { participants: [{ binding: 'new-role', required: true }] },
  { participants: [] },
])('does not create a group when current validation fails execution/binding requirements', async (patch) => {
  request.mockResolvedValueOnce(validation(patch));
  expect(await launchCollaborationGroup(input())).toMatchObject({ ok: false, error: { code: 'GROUP_CREATE_INVALID' } });
  expect(request).toHaveBeenCalledTimes(1);
});

it('rejects an empty goal before making requests', async () => {
  expect(await launchCollaborationGroup({ ...input(), context: '  ' })).toMatchObject({ ok: false });
  expect(request).not.toHaveBeenCalled();
});

it.each([undefined, 408, 500, 503])(
  'preserves an unknown create outcome for status %s without retries',
  async (status) => {
    request.mockResolvedValueOnce(validation()).mockRejectedValueOnce(Object.assign(new Error('failed'), { status }));
    expect(await launchCollaborationGroup(input())).toMatchObject({
      ok: false,
      error: { code: 'GROUP_CREATE_OUTCOME_UNKNOWN', canRetry: false },
    });
    expect(request).toHaveBeenCalledTimes(2);
  },
);

it('allows correction after an explicit rejected create request', async () => {
  request
    .mockResolvedValueOnce(validation())
    .mockRejectedValueOnce(Object.assign(new Error('invalid binding'), { status: 400 }));
  expect(await launchCollaborationGroup(input())).toMatchObject({
    ok: false,
    error: { code: 'GROUP_CREATE_REJECTED', canRetry: true },
  });
});

it('does not retry after an incomplete successful response', async () => {
  request.mockResolvedValueOnce(validation()).mockResolvedValueOnce({ code: 20100, data: {} });
  expect(await launchCollaborationGroup(input())).toMatchObject({
    ok: false,
    error: { code: 'GROUP_CREATE_OUTCOME_UNKNOWN' },
  });
  expect(request).toHaveBeenCalledTimes(2);
});

it.each([
  ['preflight', 15_000, 'VALIDATE_FAILED'],
  ['create', 60_000, 'GROUP_CREATE_OUTCOME_UNKNOWN'],
] as const)('aborts %s at its deadline with the appropriate retry state', async (phase, timeout, code) => {
  jest.useFakeTimers();
  try {
    if (phase === 'create') request.mockResolvedValueOnce(validation());
    request.mockImplementationOnce(
      (_url, options) =>
        new Promise((_resolve, reject) => {
          options?.signal?.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
        }),
    );
    const result = launchCollaborationGroup(input());
    await jest.advanceTimersByTimeAsync(timeout);
    expect(await result).toMatchObject({ ok: false, error: { code } });
    expect(request).toHaveBeenCalledTimes(phase === 'preflight' ? 1 : 2);
    expect(jest.getTimerCount()).toBe(0);
  } finally {
    jest.useRealTimers();
  }
});
