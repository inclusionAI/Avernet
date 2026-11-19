import { sessionInitiatorId } from '@/domain/conversation/sessionInitiator';
import { queryCollaborationBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { loadSessionInitiators } from '@/services/workspace/sessionInitiatorService';

jest.mock('@/services/backendApi/collaboration/collaborationBotController');
const query = jest.mocked(queryCollaborationBots);
const envelope = (items: Array<{ kind: 'human' | 'bot'; bot_id: string; name?: string; avatar_url?: string }>) => ({
  code: 200,
  data: { items, total: items.length },
});
beforeEach(() => query.mockReset());

it.each([
  ['user:149608:session:abc:agent:main', '149608'],
  ['agent:main:session:abc:user:447147', '447147'],
  ['agent:main:direct:WB123', 'WB123'],
  ['user:149608:session:abc:agent:member-task', null],
  ['bcs-cli:owner:session:abc:user:149608', null],
  ['session-unknown', null],
  ['user:bot_external:session:abc', null],
])('extracts only explicit human creators: %s', (id, expected) => {
  expect(sessionInitiatorId(id)).toBe(expected);
});

it('batches unique humans, reuses cache, and never accepts Bot or unrelated profiles', async () => {
  query.mockResolvedValue(
    envelope([
      { kind: 'human', bot_id: 'human_123', name: '张三', avatar_url: '/avatar.png' },
      { kind: 'bot', bot_id: 'human_456', name: '不是人类' },
      { kind: 'human', bot_id: 'human_789', name: '不相关' },
    ]),
  );
  const result = await loadSessionInitiators('viewer-batch', ['123', '456', '123']);
  expect(query).toHaveBeenCalledWith({ bot_ids: ['human_123', 'human_456'] });
  expect(result).toEqual({
    '123': { userId: '123', displayName: '张三', avatarUrl: '/avatar.png' },
    '456': { userId: '456' },
  });
  await loadSessionInitiators('viewer-batch', ['123']);
  expect(query).toHaveBeenCalledTimes(1);
});

it('deduplicates in-flight lookups and isolates viewers including late responses', async () => {
  let finish!: (value: ReturnType<typeof envelope>) => void;
  query.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const first = loadSessionInitiators('viewer-A', ['123']);
  const joined = loadSessionInitiators('viewer-A', ['123']);
  query.mockResolvedValue(envelope([{ kind: 'human', bot_id: 'human_123', name: 'B可见姓名' }]));
  expect((await loadSessionInitiators('viewer-B', ['123']))['123'].displayName).toBe('B可见姓名');
  finish(envelope([{ kind: 'human', bot_id: 'human_123', name: 'A可见姓名' }]));
  expect(await joined).toEqual(await first);
  expect((await loadSessionInitiators('viewer-B', ['123']))['123'].displayName).toBe('B可见姓名');
  expect(query).toHaveBeenCalledTimes(2);
});

it('degrades to IDs on failures, treats numeric names as unknown, and skips unresolved viewers', async () => {
  expect(await loadSessionInitiators('', ['123'])).toEqual({});
  expect(query).not.toHaveBeenCalled();
  query.mockRejectedValueOnce(new Error('unavailable'));
  expect(await loadSessionInitiators('viewer-fail', ['123'])).toEqual({ '123': { userId: '123' } });
  query.mockResolvedValue(envelope([{ kind: 'human', bot_id: 'human_123', name: '123' }]));
  expect((await loadSessionInitiators('viewer-numeric', ['123']))['123'].displayName).toBeUndefined();
});

it('bounds each query batch to 50 humans', async () => {
  query.mockResolvedValue(envelope([]));
  await loadSessionInitiators(
    'viewer-large',
    Array.from({ length: 101 }, (_, i) => String(i)),
  );
  expect(query.mock.calls.map(([body]) => (body.bot_ids as string[]).length)).toEqual([50, 50, 1]);
});

it('expires failed lookups sooner and bounds the memory cache', async () => {
  const now = jest.spyOn(Date, 'now').mockReturnValue(1_000);
  try {
    query.mockResolvedValue(envelope([]));
    await loadSessionInitiators('viewer-expiry', ['123']);
    now.mockReturnValue(31_001);
    await loadSessionInitiators('viewer-expiry', ['123']);
    expect(query).toHaveBeenCalledTimes(2);
    query.mockClear();
    await loadSessionInitiators('viewer-bound', Array.from({ length: 501 }, (_, i) => String(i)));
    expect(query).toHaveBeenCalledTimes(11);
    await loadSessionInitiators('viewer-bound', ['0']);
    expect(query).toHaveBeenCalledTimes(12);
  } finally {
    now.mockRestore();
  }
});
