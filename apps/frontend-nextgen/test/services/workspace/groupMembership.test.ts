import type { GroupSummaryData } from '@/services/backendApi/collaboration/collaborationGroupController';
import { listGroups } from '@/services/backendApi/collaboration/collaborationGroupController';
import { resolveGroupMembership } from '@/services/workspace/groupMembership';

jest.mock('@/services/backendApi/collaboration/collaborationGroupController');
const list = jest.mocked(listGroups);
function summary(groupId: string, membership: 'direct' | 'session_only'): GroupSummaryData {
  return { group_id: groupId, kind: 'normal', status: 'active', visibility: 'private', membership,
    originator_actor_id: 'bot-1', created_at: 1, updated_at: 1 };
}
beforeEach(() => { jest.resetAllMocks(); });

it('positively identifies an off-page session-only group in the selected Bot view', async () => {
  list.mockResolvedValueOnce({ code: 20000, data: {
    items: Array.from({ length: 100 }, (_, i) => summary(`other-${i}`, 'session_only')), total: 101,
  } }).mockResolvedValueOnce({ code: 20000, data: { items: [summary('target', 'session_only')], total: 101 } });
  expect(await resolveGroupMembership('target', 'bot-view')).toEqual({ ok: true, data: 'session_only' });
  expect(list.mock.calls.map(([params]) => params)).toEqual([
    { kind: 'normal', membership: 'session_only', offset: 0, limit: 100, view_bot_id: 'bot-view' },
    { kind: 'normal', membership: 'session_only', offset: 100, limit: 100, view_bot_id: 'bot-view' },
  ]);
});

it('only identifies direct membership after a positive matching Human-view item, without user_id/Bot substitution', async () => {
  list.mockResolvedValueOnce({ code: 20000, data: { items: [], total: 0 } })
    .mockResolvedValueOnce({ code: 20000, data: { items: [summary('target', 'direct')], total: 1 } });
  expect(await resolveGroupMembership('target')).toEqual({ ok: true, data: 'direct' });
  expect(list.mock.calls.map(([params]) => params.membership)).toEqual(['session_only', 'direct']);
  for (const [params] of list.mock.calls) {
    expect(params).not.toHaveProperty('view_bot_id');
    expect(params).not.toHaveProperty('user_id');
    expect(params).not.toHaveProperty('q');
  }
});

it('does not infer either category from missing items in both complete lists', async () => {
  list.mockResolvedValue({ code: 20000, data: { items: [], total: 0 } });
  expect(await resolveGroupMembership('missing')).toEqual({ ok: true, data: null });
  expect(list).toHaveBeenCalledTimes(2);
});

it('fails closed on missing membership metadata instead of deriving it from a guessed relation', async () => {
  const item = summary('target', 'session_only');
  delete item.membership;
  list.mockResolvedValue({ code: 20000, data: { items: [item], total: 1 } });
  expect(await resolveGroupMembership('target')).toMatchObject({ ok: false });
  expect(list).toHaveBeenCalledTimes(1);
});

it('does not turn an incomplete page or business error into a missing-membership decision', async () => {
  list.mockResolvedValueOnce({ code: 20000, data: { items: [], total: 2 } });
  expect(await resolveGroupMembership('target')).toMatchObject({ ok: false });
  list.mockResolvedValueOnce({ code: 40300, data: { items: [], total: 0 } });
  expect(await resolveGroupMembership('target')).toMatchObject({ ok: false });
  expect(list).toHaveBeenCalledTimes(2);
});

it('stops pagination when the target or identity is invalidated', async () => {
  const controller = new AbortController();
  let resolve!: (value: Awaited<ReturnType<typeof listGroups>>) => void;
  list.mockReturnValueOnce(new Promise((r) => { resolve = r; }));
  const result = resolveGroupMembership('target', undefined, controller.signal);
  controller.abort();
  resolve({ code: 20000, data: { items: [summary('other', 'session_only')], total: 101 } });
  expect(await result).toMatchObject({ ok: false });
  expect(list).toHaveBeenCalledTimes(1);
});
