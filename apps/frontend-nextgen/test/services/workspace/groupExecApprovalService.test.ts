import { GroupExecApprovalService, parseGroupExecApprovalEvent } from '@/services/workspace/groupExecApprovalService';
import { GroupQuestionResolveError } from '@/services/workspace/groupQuestionService';

const identity = { groupId: 'group', bcsSessionId: 'group:session' };
const key = JSON.stringify(['run', 'approval']);
const payload = {
  kind: 'exec',
  phase: 'requested',
  interactionId: 'approval',
  toolName: 'bash',
  command: '/usr/bin/top -l 2',
  reason: 'escalate sandbox to danger-full-access',
  options: [
    { decision: 'allow_once', label: 'Allow once' },
    { decision: 'deny', label: 'Deny' },
  ],
};
function frame(overrides = {}) {
  return {
    type: 'event',
    event: 'interaction',
    group_id: 'group',
    bcsSessionId: 'group:session',
    bcsRunId: 'run',
    bot_uuid: 'bot',
    payload,
    ...overrides,
  };
}
function fixture() {
  const sent: any[] = [];
  const service = new GroupExecApprovalService({
    ...identity,
    isConnected: () => true,
    send: async (value) => {
      sent.push(value);
    },
  });
  let pending: any[] = [];
  service.subscribe((snapshot) => {
    pending = snapshot.interactions;
  });
  return { service, sent, pending: () => pending };
}

afterEach(() => jest.useRealTimers());

test('parses the DSH escalation and uses only trusted envelope identity', () => {
  const event = parseGroupExecApprovalEvent(frame({ payload: { ...payload, runId: 'spoofed' } }), identity);
  expect(event).toMatchObject({ phase: 'requested', interaction: { runId: 'run', command: payload.command } });
  for (const overrides of [
    { group_id: 'other' },
    { bcsSessionId: 'other' },
    { bot_uuid: '' },
    { payload: { ...payload, kind: 'ask_user' } },
    { payload: { ...payload, command: 123 } },
    { payload: { ...payload, options: [{ decision: 'allow_always', label: 'Always' }] } },
  ]) {
    expect(parseGroupExecApprovalEvent(frame(overrides), identity)).toBeNull();
  }
});

test.each(['allow_once', 'deny'] as const)(
  'sends %s once, waits for ACK and prevents replay resurrection',
  async (decision) => {
    const f = fixture();
    expect(f.service.handleFrame(frame())).toBe(true);
    const first = f.service.resolve(key, { decision });
    const duplicate = f.service.resolve(key, { decision });
    expect(f.sent).toHaveLength(1);
    expect(f.sent[0]).toMatchObject({
      method: 'interaction.resolve',
      params: {
        bcsRunId: 'run',
        interactionId: 'approval',
        decision,
        idempotencyKey: expect.any(String),
      },
    });
    f.service.handleFrame({ type: 'res', id: f.sent[0].id, ok: true, payload: { interactionStatus: 'resolved' } });
    await Promise.all([first, duplicate]);
    expect(f.pending()).toEqual([]);
    f.service.handleFrame(frame());
    expect(f.pending()).toEqual([]);
  },
);

test('timeout preserves pending approval and same decision reuses its idempotency key', async () => {
  jest.useFakeTimers();
  const f = fixture();
  f.service.handleFrame(frame());
  const attempt = f.service.resolve(key, { decision: 'deny' });
  const rejected = attempt.catch((error: unknown) => error);
  jest.advanceTimersByTime(10_000);
  expect(await rejected).toMatchObject({ code: 'TIMEOUT' });
  expect(f.pending()).toHaveLength(1);
  const retry = f.service.resolve(key, { decision: 'deny' });
  expect(f.sent[1].params.idempotencyKey).toBe(f.sent[0].params.idempotencyKey);
  expect(f.sent[1].id).not.toBe(f.sent[0].id);
  f.service.resetConnection();
  await expect(retry).rejects.toBeInstanceOf(GroupQuestionResolveError);
  expect(f.pending()).toEqual([]);
  expect(jest.getTimerCount()).toBe(0);
});

test('terminal before ACK closes approval; a late failure cannot revive it', async () => {
  const f = fixture();
  f.service.handleFrame(frame());
  const attempt = f.service.resolve(key, { decision: 'allow_once' });
  f.service.handleFrame(frame({ payload: { kind: 'exec', interactionId: 'approval', phase: 'invalidated' } }));
  await attempt;
  expect(f.pending()).toEqual([]);
  expect(
    f.service.handleFrame({ type: 'res', id: f.sent[0].id, ok: false, error: { code: 'late', message: 'late' } }),
  ).toBe(false);
});

test('unoffered decisions and disconnected clients cannot approve', async () => {
  const f = fixture();
  f.service.handleFrame(frame({ payload: { ...payload, options: [payload.options[1]] } }));
  await expect(f.service.resolve(key, { decision: 'allow_once' })).rejects.toThrow('选项');
  expect(f.sent).toHaveLength(0);
  const offline = new GroupExecApprovalService({ ...identity, isConnected: () => false, send: async () => undefined });
  offline.handleFrame(frame());
  await expect(offline.resolve(key, { decision: 'deny' })).rejects.toMatchObject({ code: 'DISCONNECTED' });
});
