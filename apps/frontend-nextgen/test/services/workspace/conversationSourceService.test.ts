import { listConversationSource } from '@/services/workspace/conversationSourceService';
import { conversationService } from '@/services/workspace/conversationService';
import { listBotSessions } from '@/services/backendApi/bots/privateBotSessionController';
import { sortConversationSessions } from '@/domain/conversation/sessionOrder';
import { firstPageOf, nextPageOf } from '@/pages/Workspace/Chat/hooks/conversationSessionCache';
import type { ChatBotView } from '@/services/workspace/botSessionService';

jest.mock('@/services/backendApi/bots/privateBotSessionController');
const list = jest.mocked(listBotSessions);
const bot: ChatBotView = {
  botId: 'bot:owner', realBotId: 'bot', ownerId: 'owner', botType: 'service',
  displayName: 'Bot', online: true, chatable: true, isTeamBot: true,
};
const dto = (id: string, modified: string) => ({
  session_id: id, title: id, model: '', agent_id: 'main', message_count: 1,
  gmt_create: '2026-01-01T00:00:00Z', gmt_modified: modified,
});
const reply = (data: Record<string, unknown>) => ({
  code: 200, message: 'OK', request_id: 'test', data,
}) as unknown as Awaited<ReturnType<typeof listBotSessions>>; // 包含故意缺字段的旧后端响应。

beforeEach(() => jest.clearAllMocks());

it('preserves the Coding collab tab without sending an unsupported source to OpenAPI', async () => {
  const id = 'user:123:session:task:agent:member-task';
  list.mockResolvedValue(reply({ items: [dto(id, '2026-01-02')], total: 1 }));
  const result = await conversationService.listManagedSessions(
    { ...bot, engine: 'claude_code', isAgentCodingBot: true }, '123', 'all', 1, 10, '', undefined, 'collab',
  );
  expect(result).toMatchObject({ ok: true, data: { items: [{ sessionId: id }] } });
  expect(list).toHaveBeenCalledTimes(1);
  expect(list.mock.calls[0][1]).not.toHaveProperty('source');
});

it('keeps Coding task sessions in their dedicated collab tab, not in mine', async () => {
  list.mockResolvedValue(reply({ source: 'mine', items: [dto('user:123:session:task:agent:member-task', '')],
    total: 1, has_more: true, next_cursor: 'next' }));
  const result = await listConversationSource({ ...bot, isAgentCodingBot: true }, '123', 'mine', 1, 10, '');
  expect(result).toMatchObject({ ok: true, data: { items: [] } });
});

it('uses the authenticated collaborator as the mine conversation subject', async () => {
  list.mockResolvedValue(reply({ items: [], total: 0 }));
  const result = await listConversationSource(bot, 'human_123', 'mine', 1, 10, 'session:key',
    { deviceUuid: 'device', stage: 'online' });
  expect(list).toHaveBeenCalledWith('bot', {
    user_id: '123', owner_id: 'owner', f_user_id: '123', page: 1, page_size: 10,
    stage: 'online', session_key: 'session:key', device_uuid: 'device',
  });
  expect(result).toMatchObject({ ok: true, data: { items: [], hasMore: false } });
});

it('keeps the others operator window and cursor free of subject impersonation', async () => {
  list.mockResolvedValue(reply({ source: 'others', items: [], total: 0, has_more: true, next_cursor: 'next', scan_limited: true }));
  const result = await listConversationSource(bot, 'human_123', 'others', 1, 10, 'session:key',
    { deviceUuid: 'device', stage: 'online' }, 'previous');
  expect(list).toHaveBeenCalledWith('bot', {
    user_id: '123', owner_id: 'owner', source: 'others', page: 1, page_size: 10,
    stage: 'online', source_cursor: 'previous', session_key: 'session:key', device_uuid: 'device',
  });
  expect(result).toMatchObject({ ok: true, data: { items: [], hasMore: true, nextCursor: 'next', scanLimited: true } });
  if (!result.ok) throw new Error('missing source page');
  const first = firstPageOf([], 0, result.data);
  expect(first).toMatchObject({ hasMore: true, nextCursor: 'next' });
  expect(nextPageOf(first, result, 2)).toMatchObject({ hasMore: true, nextCursor: 'next', page: 2 });
});

it('sorts loaded sessions by updated time, falling back to created time without mutating the input', async () => {
  list.mockResolvedValue(reply({ source: 'mine', items: [dto('old', '2026-01-02'), dto('new', '2026-02-03')],
    total: 2, has_more: false, next_cursor: null }));
  const result = await listConversationSource(bot, '123', 'mine', 1, 10, '');
  if (!result.ok) throw new Error('missing source page');
  expect(result.data.items.map((item) => item.sessionId)).toEqual(['new', 'old']);
  const items = result.data.items;
  expect(sortConversationSessions([...items, { ...items[0], sessionId: 'unknown', gmtModified: '', gmtCreate: '' }])
    .map((item) => item.sessionId)).toEqual(['new', 'old', 'unknown']);
  expect(items).toHaveLength(2);
});

it.each([
  { items: [], total: 0 },
  { source: 'mine', items: [], total: 0, has_more: true, next_cursor: null },
])('does not silently treat an unsupported or malformed source response as filtered', async (data) => {
  list.mockResolvedValue(reply(data));
  expect(await listConversationSource(bot, '123', 'others', 1, 10, '')).toMatchObject({ ok: false });
});

it('keeps existing rows and cursor on continuation failure', () => {
  const base = { ...firstPageOf([], 0), hasMore: true, nextCursor: 'keep' };
  const result = nextPageOf(base, { ok: false, error: { code: 'LIMIT', friendlyMessage: 'scan limit', canRetry: true } }, 2);
  expect(result).toMatchObject({ hasMore: true, nextCursor: 'keep', loadMoreError: 'scan limit' });
});
