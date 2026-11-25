/** @jest-environment jsdom */
// 好友名单数据 Hook(公开设置 · 好友名单):装入目录并派生分类计数,单类型
// 重拉,Owner 移除动线(actor 为 Bot 协作 uuid, deleted:false 走已被移除中性
// 路径),以及 >20 页的 fetch-all 回归(最后一页的好友也要能被搜到)。
import { filterFriendViewsByKeyword } from '@/domain/collaborationPrivacy/botFriends';
import { useBotFriendRoster } from '@/hooks/useBotFriendRoster';
import { queryCollaborationBots } from '@/services/backendApi/collaboration/collaborationBotController';
import {
  deleteFriendConnection,
  listFriendConnections,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import {
  botFriendConversationService,
  type BotFriendDirectoryResult,
  type BotFriendPage,
  type BotIdentityFriendView,
} from '@/services/workspace/botFriendConversationService';
import type { DomainResult } from '@/services/workspace/identityService';
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

// 好友名单链路的网络边界:Hook/服务走真实实现,控制器 mock(类型导出在编译期
// 擦除,工厂只需替换运行时函数)。
jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController', () => ({
  deleteFriendConnection: require('jest-mock').fn(),
  listFriendConnections: require('jest-mock').fn(),
}));
jest.mock('@/services/backendApi/collaboration/collaborationBotController', () => ({
  queryCollaborationBots: require('jest-mock').fn(),
}));

const deleteFriendConnectionMock = deleteFriendConnection as unknown as jest.Mock;
const listFriendConnectionsMock = listFriendConnections as unknown as jest.Mock;
const queryCollaborationBotsMock = queryCollaborationBots as unknown as jest.Mock;

const BOT_UUID = 'bot-b:100'; // BCS 协作 Bot UUID(targetBot.id),不是后端行 bot.id

const friendView = (overrides: Partial<BotIdentityFriendView> = {}): BotIdentityFriendView => ({
  actorType: 'human',
  actorId: '447147',
  queryId: 'human_447147',
  displayName: '风太',
  online: true,
  detailsResolved: true,
  ...overrides,
});

const sectionOk = (
  items: BotIdentityFriendView[],
  total = items.length,
): DomainResult<BotFriendPage<BotIdentityFriendView>> => ({
  ok: true,
  data: { items, total, page: 1, pageSize: 100, hasMore: false },
});

const sectionFailure = (code: string): DomainResult<BotFriendPage<BotIdentityFriendView>> => ({
  ok: false,
  error: { code, friendlyMessage: '加载失败，请稍后重试。', canRetry: true },
});

const directory = (overrides: Partial<BotFriendDirectoryResult> = {}): BotFriendDirectoryResult => ({
  human: sectionOk([
    friendView(),
    friendView({ actorId: '448148', queryId: 'human_448148', displayName: '雨太' }),
  ]),
  bot: sectionOk([
    friendView({
      actorType: 'bot',
      actorId: 'bot-friend:9',
      queryId: 'bot-friend:9',
      displayName: '好友 Bot',
    }),
  ]),
  ...overrides,
});

beforeEach(() => {
  deleteFriendConnectionMock.mockReset();
  listFriendConnectionsMock.mockReset();
  queryCollaborationBotsMock.mockReset();
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe('useBotFriendRoster', () => {
  it('loads the directory for the collaboration Bot uuid and derives split counts', async () => {
    const loadDirectory = jest
      .spyOn(botFriendConversationService, 'loadDirectory')
      .mockResolvedValue(directory());

    const { result } = renderHook(() => useBotFriendRoster(BOT_UUID));

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(loadDirectory).toHaveBeenCalledWith(BOT_UUID);
    expect(result.current.counts).toEqual({ users: 2, bots: 1 });
    expect(result.current.removeError).toBeNull();
  });

  it('degrades a failed type to a null count and retries only that type', async () => {
    jest.spyOn(botFriendConversationService, 'loadDirectory').mockResolvedValue(
      directory({ bot: sectionFailure('BOT_FRIEND_BOTS_LOAD_FAILED') }),
    );
    const loadSection = jest.spyOn(botFriendConversationService, 'loadSection');

    const { result } = renderHook(() => useBotFriendRoster(BOT_UUID));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.counts).toEqual({ users: 2, bots: null });

    loadSection.mockResolvedValue(
      sectionOk([
        friendView({
          actorType: 'bot',
          actorId: 'bot-friend:9',
          queryId: 'bot-friend:9',
          displayName: '好友 Bot',
        }),
      ]),
    );
    await act(async () => {
      await result.current.reload('bot');
    });

    expect(loadSection).toHaveBeenCalledWith(BOT_UUID, 'bot');
    expect(result.current.counts).toEqual({ users: 2, bots: 1 });
    // 未失败的类型不被重拉/清空。
    expect(result.current.sections?.human).toEqual(directory().human);
  });

  it('removes a friend through the owner actor params and refetches only that type', async () => {
    const loadDirectory = jest
      .spyOn(botFriendConversationService, 'loadDirectory')
      .mockResolvedValue(directory());
    const loadSection = jest
      .spyOn(botFriendConversationService, 'loadSection')
      .mockResolvedValue(sectionOk([]));
    deleteFriendConnectionMock.mockResolvedValue({ code: 20000, message: 'OK', data: { deleted: true } });

    const { result } = renderHook(() => useBotFriendRoster(BOT_UUID));
    await waitFor(() => expect(result.current.loading).toBe(false));

    const view = friendView();
    let removal;
    await act(async () => {
      removal = await result.current.removeFriend(view);
    });

    expect(removal).toEqual({ ok: true });
    expect(deleteFriendConnectionMock).toHaveBeenCalledWith({
      actor_type: 'bot',
      actor_id: BOT_UUID,
      target_actor_type: 'human',
      target_actor_id: '447147',
    });
    // 只重拉被移除的类型(计数与页签收敛于原地刷新)。
    expect(loadSection).toHaveBeenCalledWith(BOT_UUID, 'human');
    expect(loadDirectory).toHaveBeenCalledTimes(1);
    expect(result.current.removeError).toBeNull();
    expect(result.current.removingIds).toEqual([]);
    expect(result.current.counts).toEqual({ users: 0, bots: 1 });
  });

  it('surfaces a denied removal as removeError without touching the sections', async () => {
    jest.spyOn(botFriendConversationService, 'loadDirectory').mockResolvedValue(directory());
    const loadSection = jest.spyOn(botFriendConversationService, 'loadSection');
    deleteFriendConnectionMock.mockRejectedValue(new Error('403 forbidden'));

    const { result } = renderHook(() => useBotFriendRoster(BOT_UUID));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let removal;
    await act(async () => {
      removal = await result.current.removeFriend(friendView());
    });

    expect(removal).toEqual({ ok: false });
    expect(result.current.removeError?.code).toBe('BOT_FRIEND_REMOVE_FAILED');
    expect(loadSection).not.toHaveBeenCalled();
    expect(result.current.sections?.human).toEqual(directory().human);
    expect(result.current.counts).toEqual({ users: 2, bots: 1 });
  });

  it('treats deleted:false as the neutral already-removed path', async () => {
    jest.spyOn(botFriendConversationService, 'loadDirectory').mockResolvedValue(directory());
    const loadSection = jest
      .spyOn(botFriendConversationService, 'loadSection')
      .mockResolvedValue(sectionOk([]));
    deleteFriendConnectionMock.mockResolvedValue({ code: 20000, message: 'OK', data: { deleted: false } });

    const { result } = renderHook(() => useBotFriendRoster(BOT_UUID));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let removal;
    await act(async () => {
      removal = await result.current.removeFriend(friendView());
    });

    expect(removal).toEqual({ ok: true, alreadyRemoved: true });
    expect(result.current.removeError).toBeNull();
    expect(loadSection).toHaveBeenCalledWith(BOT_UUID, 'human');
  });

  it('guards a double removal of the same friend', async () => {
    jest.spyOn(botFriendConversationService, 'loadDirectory').mockResolvedValue(directory());
    jest.spyOn(botFriendConversationService, 'loadSection').mockResolvedValue(sectionOk([]));
    let resolveDelete: (value: unknown) => void = () => undefined;
    deleteFriendConnectionMock.mockImplementation(
      () => new Promise((resolve) => {
        resolveDelete = resolve;
      }),
    );

    const { result } = renderHook(() => useBotFriendRoster(BOT_UUID));
    await waitFor(() => expect(result.current.loading).toBe(false));

    const first = result.current.removeFriend(friendView());
    await waitFor(() => expect(result.current.removingIds).toEqual(['447147']));
    let second;
    await act(async () => {
      second = await result.current.removeFriend(friendView());
    });
    expect(second).toEqual({ ok: false, duplicate: true });
    expect(deleteFriendConnectionMock).toHaveBeenCalledTimes(1);

    resolveDelete({ code: 20000, message: 'OK', data: { deleted: true } });
    await act(async () => {
      await first;
    });
    expect(result.current.removingIds).toEqual([]);
  });
});

describe('useBotFriendRoster fetch-all regression (real service)', () => {
  // 不经 Hook 的 service 替身:真实 botFriendConversationService +  mocked 控制器,
  // 验证 >20 页(>2,000 好友)也一页不漏,最后一页的好友能被搜索命中。
  const relationEnvelope = (ids: string[], total: number, page: number) => ({
    code: 20000,
    message: 'OK',
    data: {
      items: ids.map((id) => ({ actor: { type: 'human' as const, id } })),
      total,
      page,
      page_size: 100,
    },
    request_id: 'relations',
  });

  it('a 21-page section lists every friend and search finds the last page', async () => {
    listFriendConnectionsMock.mockImplementation(async (params: {
      target_type?: string;
      page?: number;
    }) => {
      if (params.target_type === 'bot') {
        return { code: 20000, message: 'OK', data: { items: [], total: 0, page: 1, page_size: 100 } };
      }
      const page = params.page ?? 1;
      const start = (page - 1) * 100;
      const size = page === 21 ? 50 : 100;
      const ids = Array.from({ length: size }, (_, index) => `${start + index + 1}`);
      return relationEnvelope(ids, 2050, page);
    });
    queryCollaborationBotsMock.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: { items: [] },
      request_id: 'details',
    });

    const { result } = renderHook(() => useBotFriendRoster(BOT_UUID));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.counts).toEqual({ users: 2050, bots: 0 });
    const items = result.current.sections?.human.ok ? result.current.sections.human.data.items : [];
    expect(items).toHaveLength(2050);
    // 只在最后一页出现的好友(第 2,050 个)也能被关键词命中。
    const hits = filterFriendViewsByKeyword(items, '2050');
    expect(hits.map((view) => view.actorId)).toEqual(['2050']);
    expect(
      listFriendConnectionsMock.mock.calls.filter(
        (call: unknown[]) => (call[0] as { target_type?: string }).target_type === 'human',
      ),
    ).toHaveLength(21);
  });

  it('a zero-progress page degrades the type to its error state, never a silent partial list', async () => {
    // 设计 D2:total 未满时空页必须中止为该类型的错误态（可重试）,
    // 不能静默渲染 100/300 的假"全量"名单。
    listFriendConnectionsMock.mockImplementation(async (params: {
      target_type?: string;
      page?: number;
    }) => {
      if (params.target_type === 'bot') {
        return { code: 20000, message: 'OK', data: { items: [], total: 0, page: 1, page_size: 100 } };
      }
      const page = params.page ?? 1;
      if (page >= 2) return relationEnvelope([], 300, page);
      const ids = Array.from({ length: 100 }, (_, index) => `${index + 1}`);
      return relationEnvelope(ids, 300, page);
    });
    queryCollaborationBotsMock.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: { items: [] },
      request_id: 'details',
    });

    const { result } = renderHook(() => useBotFriendRoster(BOT_UUID));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.sections?.human.ok).toBe(false);
    expect(result.current.counts.users).toBeNull();
    // 另一类型不受影响。
    expect(result.current.counts.bots).toBe(0);
  });
});
