import { friendConnectionRequestService } from '@/services/collaborationSquare/friendConnectionRequestService';
import * as requests from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import * as bots from '@/services/backendApi/collaboration/collaborationBotController';
import { BackendRequestError } from '@/services/backendApi/httpClient';

jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController');
jest.mock('@/services/backendApi/collaboration/collaborationBotController');
const list = jest.mocked(requests.listFriendConnectionRequests);
const query = jest.mocked(bots.queryCollaborationBots);
const actor = { type: 'bot' as const, id: 'mine:owner' };
const other = { type: 'bot' as const, id: 'peer:owner' };
const dto = (direction: 'sent' | 'received', requestId = direction): requests.FriendConnectionRequestDto => ({
  request_id: requestId, from_actor: direction === 'sent' ? actor : other,
  to_actor: direction === 'sent' ? other : actor, created_by: actor, status: 'pending', message: '一起协作',
});
const page = (direction: 'sent' | 'received', current = 1) => ({
  code: 20000, data: { items: [dto(direction)], total: 45, page: current, page_size: 20 },
});
beforeEach(() => {
  jest.resetAllMocks();
  list.mockImplementation(async (params = {}) => page(params.direction as 'sent' | 'received', params.page));
  query.mockResolvedValue({ code: 20000, data: { items: [
    { bot_id: other.id, kind: 'bot', name: '同伴', avatar_url: '/avatar-peer.png', status: 'online', reachability: 'reachable' },
    { bot_id: actor.id, kind: 'bot', name: '我的助手', avatar_url: '/avatar-mine.png' },
  ] } });
});

it('双向 pending 独立分页，并且合并完整 Bot id 只批量查一次资料', async () => {
  const signal = new AbortController().signal;
  const result = await friendConnectionRequestService.load(actor, { sent: 2, received: 3 }, signal);
  for (const [direction, current] of [['sent', 2], ['received', 3]] as const) {
    expect(list).toHaveBeenCalledWith({ actor_type: 'bot', actor_id: actor.id, direction, status: 'pending', page: current, page_size: 20 }, signal);
  }
  expect(query).toHaveBeenCalledTimes(1);
  expect(query).toHaveBeenCalledWith({ bot_ids: [actor.id, other.id] }, signal);
  expect(result.sent).toMatchObject({ total: 45, page: 2, pageSize: 20 });
  expect(result.received.page).toBe(3);
  expect(result.sent.items[0].toActor).toMatchObject({ id: other.id, name: '同伴', avatarUrl: '/avatar-peer.png', online: true });
  expect(result.received.items[0].fromActor.name).toBe('同伴');
});

it('空列表不查询 Bot 目录', async () => {
  list.mockResolvedValue({ code: 20000, data: { items: [], total: 0, page: 1, page_size: 20 } });
  const result = await friendConnectionRequestService.load(actor, { sent: 1, received: 1 });
  expect(result.sent.items).toEqual([]);
  expect(query).not.toHaveBeenCalled();
});

it('Human 的 user id 不加前缀、不送进 Bot 批量查询；遗漏 Bot 回退完整 id', async () => {
  const human = { type: 'human' as const, id: '7' };
  list.mockImplementation(async (params = {}) => ({ code: 20000, data: {
    items: [{ ...dto('sent'), from_actor: params.direction === 'sent' ? human : other,
      to_actor: params.direction === 'sent' ? other : human }], total: 1, page: 1, page_size: 20,
  } }));
  query.mockResolvedValue({ code: 20000, data: { items: [] } });
  const result = await friendConnectionRequestService.load(human, { sent: 1, received: 1 });
  expect(query).toHaveBeenCalledWith({ bot_ids: [other.id] }, undefined);
  expect(result.sent.items[0].fromActor).toMatchObject({ type: 'human', id: '7', name: '7' });
  expect(result.sent.items[0].toActor.name).toBe(other.id);
  expect(result.detailsWarning).toBeTruthy();
});

it('资料请求普通失败仍保留申请、默认头像和可重试警告', async () => {
  query.mockRejectedValue(new Error('offline'));
  const result = await friendConnectionRequestService.load(actor, { sent: 1, received: 1 });
  expect(result.sent.items[0].requestId).toBe('sent');
  expect(result.sent.items[0].toActor.avatarUrl).toMatch(/^data:image\/svg/);
  expect(result.detailsWarning).toBeTruthy();
});

it.each([401, 403])('资料请求鉴权错误 %s 不降级为成功', async (status) => {
  query.mockRejectedValue(new BackendRequestError('denied', { status, apiPath: '/query' }));
  await expect(friendConnectionRequestService.load(actor, { sent: 1, received: 1 })).rejects.toThrow('denied');
});
it('中止不被当成资料降级成功', async () => {
  const controller = new AbortController();
  query.mockImplementation(async () => { controller.abort(); throw new DOMException('aborted', 'AbortError'); });
  await expect(friendConnectionRequestService.load(actor, { sent: 1, received: 1 }, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
});
it.each([
  { code: 50000, data: page('sent').data },
  { code: 20000, data: { ...page('sent').data, items: 'invalid' } },
  { code: 20000, data: { ...page('sent').data, total: -1 } },
  { code: 20000, data: { ...page('sent').data, items: [{ ...dto('sent'), from_actor: { type: 'bot', id: 'stranger' } }] } },
])('列表非法响应必须失败而非展示为空', async (response) => {
  list.mockResolvedValue(response as never);
  await expect(friendConnectionRequestService.load(actor, { sent: 1, received: 1 })).rejects.toThrow();
  expect(query).not.toHaveBeenCalled();
});

it.each(['accept', 'reject'] as const)('%s 使用 request id，确认服务端状态后才成功', async (action) => {
  const status = action === 'accept' ? 'approved' : 'rejected';
  const api = action === 'accept' ? requests.acceptFriendConnectionRequest : requests.rejectFriendConnectionRequest;
  jest.mocked(api).mockResolvedValue({ code: 20000, data: { ...dto('received'), status } });
  await expect(friendConnectionRequestService.decide('received', action)).resolves.toMatchObject({ requestId: 'received', status });
  expect(api).toHaveBeenCalledWith('received', undefined);
});
it.each([
  { code: 50000, data: { ...dto('received'), status: 'approved' } },
  { code: 20000, data: dto('received') },
  { code: 20000, data: { ...dto('received'), request_id: 'wrong', status: 'approved' } },
])('写入业务失败或不匹配的确认不能伪装成功', async (response) => {
  jest.mocked(requests.acceptFriendConnectionRequest).mockResolvedValue(response as never);
  await expect(friendConnectionRequestService.decide('received', 'accept')).rejects.toThrow();
});

it('业务鉴权失败不被资料兜底掩盖', async () => {
  query.mockResolvedValue({ code: 40300, message: 'denied' });
  await expect(friendConnectionRequestService.load(actor, { sent: 1, received: 1 })).rejects.toMatchObject({ code: 'forbidden' });
});
it('无效资料响应可降级，但接口异常中止必须传播', async () => {
  query.mockResolvedValue({ code: 20000, data: { items: 'invalid' } } as never);
  expect((await friendConnectionRequestService.load(actor, { sent: 1, received: 1 })).detailsWarning).toBeTruthy();
  query.mockRejectedValue(new DOMException('aborted', 'AbortError'));
  await expect(friendConnectionRequestService.load(actor, { sent: 1, received: 1 })).rejects.toMatchObject({ name: 'AbortError' });
});
