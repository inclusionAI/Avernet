import * as controller from '@/services/backendApi/bots/botChatController';
import * as logController from '@/services/backendApi/bots/botLogController';
import { botChatService } from '@/services/botWorkshop/botChatService';
import { emptyBotChatFilters, useBotChatStore } from '@/stores/botChatStore';
import { afterEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/botChatController');
jest.mock('@/services/backendApi/bots/botLogController');
const listBotChats = controller.listBotChats as jest.MockedFunction<typeof controller.listBotChats>;
const getBotChat = controller.getBotChat as jest.MockedFunction<typeof controller.getBotChat>;
const listGroupBotTraces = logController.listGroupBotTraces as jest.MockedFunction<
  typeof logController.listGroupBotTraces
>;
const getGroupBotTrace = logController.getGroupBotTrace as jest.MockedFunction<typeof logController.getGroupBotTrace>;

afterEach(() => {
  jest.resetAllMocks();
  useBotChatStore.getState().reset();
});

describe('botChatService', () => {
  it('把筛选条件和共享 Bot owner 映射到 Gateway query', async () => {
    listBotChats.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: { sessions: [], total: 0, page: 1, limit: 20, has_more: false },
    });
    const context = { botId: 'b1', botName: 'Bot', userId: 'u1', ownerId: 'owner' };
    useBotChatStore.getState().openFor(context);
    await botChatService.list(context, { ...emptyBotChatFilters(), keyword: 'hello', sessionKey: 'session-1' });
    expect(listBotChats).toHaveBeenCalledWith(
      'b1',
      expect.objectContaining({
        user_id: 'u1',
        owner_id: 'owner',
        query: 'hello',
        session_key: 'session-1',
        match_mode: 'contains',
        include_output_match: true,
      }),
    );
    expect(useBotChatStore.getState().page?.total).toBe(0);
  });

  it('详情查询不为本人 Bot 发送冗余 owner_id', async () => {
    getBotChat.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: { id: 't1', timestamp: '2026-08-19T00:00:00Z' },
    });
    const context = { botId: 'b1', botName: 'Bot', userId: 'u1', ownerId: 'u1' };
    useBotChatStore.getState().openFor(context);
    await botChatService.detail(context, 't1');
    expect(getBotChat).toHaveBeenCalledWith('b1', 't1', { user_id: 'u1', owner_id: undefined });
    expect(useBotChatStore.getState().detail?.id).toBe('t1');
  });

  it('日志详情关联查询使用精确标识但保留默认时间限制', async () => {
    listBotChats.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: { sessions: [], total: 0, page: 1, limit: 100, has_more: false },
    });
    const context = { botId: 'b1', botName: 'Bot', userId: 'u1' };
    useBotChatStore.getState().openFor(context);
    await botChatService.related(
      context,
      {
        id: 't1',
        timestamp: '2026-08-19T00:00:00Z',
        name: 'Trace',
        sessionKey: 'session-1',
        status: 'SUCCESS',
        latencyMs: 0,
        totalTokens: 0,
        totalCost: 0,
        observations: [],
      },
      'session',
    );
    expect(listBotChats).toHaveBeenCalledWith(
      'b1',
      expect.objectContaining({ session_key: 'session-1', match_mode: 'exact', limit: 100 }),
    );
    expect(listBotChats.mock.calls[0][1]).not.toHaveProperty('time_scope');
  });

  it('按群 ID 使用老 bot-chats 接口并显式查询全部历史', async () => {
    listBotChats.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: { sessions: [], total: 0, page: 1, limit: 100, has_more: false },
    });
    const context = { botId: 'viewer-bot', botName: 'Viewer', userId: 'u1' };
    useBotChatStore.getState().openFor(context);

    await botChatService.related(
      context,
      {
        id: 't1',
        timestamp: '2026-08-19T00:00:00Z',
        name: 'Trace',
        groupId: 'group-1',
        status: 'SUCCESS',
        latencyMs: 0,
        totalTokens: 0,
        totalCost: 0,
        observations: [],
      },
      'group',
    );

    expect(listBotChats).toHaveBeenCalledWith(
      'viewer-bot',
      expect.objectContaining({
        user_id: 'u1',
        owner_id: undefined,
        group_id: 'group-1',
        match_mode: 'exact',
        time_scope: 'all',
        page: 1,
        limit: 100,
      }),
    );
    expect(listGroupBotTraces).not.toHaveBeenCalled();
  });

  it('群关联详情保留关联列表并使用 Group 授权上下文', async () => {
    getGroupBotTrace.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: { id: 'other-trace', timestamp: '2026-08-19T00:00:00Z', group_id: 'group-1' },
    });
    const context = { botId: 'viewer-bot', botName: 'Viewer', userId: 'u1' };
    useBotChatStore.getState().openFor(context);
    const related = { items: [], total: 3, page: 1, limit: 100, hasMore: false };
    useBotChatStore.getState().setRelatedState({ relationScope: 'group', related });

    await botChatService.detail(context, 'other-trace', 'group-1');

    expect(getGroupBotTrace).toHaveBeenCalledWith('other-trace', {
      bot_id: 'viewer-bot',
      group_id: 'group-1',
      user_id: 'u1',
      owner_id: undefined,
    });
    expect(getBotChat).not.toHaveBeenCalled();
    expect(useBotChatStore.getState().related).toBe(related);
    expect(useBotChatStore.getState().relationScope).toBe('group');
  });

  it('切换关联维度时先清空旧的跨 Bot 列表', async () => {
    let resolveRequest: ((value: Awaited<ReturnType<typeof listBotChats>>) => void) | undefined;
    listBotChats.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRequest = resolve;
        }),
    );
    const context = { botId: 'viewer-bot', botName: 'Viewer', userId: 'owner' };
    const detail = {
      id: 't1',
      timestamp: '2026-08-23T00:00:00Z',
      name: 'Trace',
      sessionKey: 'session-1',
      groupId: 'group-1',
      status: 'SUCCESS',
      latencyMs: 0,
      totalTokens: 0,
      totalCost: 0,
      observations: [],
    };
    useBotChatStore.getState().openFor(context);
    useBotChatStore.getState().setRelatedState({
      relationScope: 'group',
      related: { items: [{ ...detail, botId: 'other-bot' }], total: 1, page: 1, limit: 100, hasMore: false },
    });

    const request = botChatService.related(context, detail, 'session');

    expect(useBotChatStore.getState().relationScope).toBe('session');
    expect(useBotChatStore.getState().related).toBeUndefined();
    resolveRequest?.({
      code: 200000,
      message: 'OK',
      data: { sessions: [], total: 0, page: 1, limit: 100, has_more: false },
    });
    await request;
  });

  it('加载更多群关联 Trace 时追加当前列表并保留后端 total', async () => {
    listBotChats.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: {
        sessions: [
          { id: 't1', timestamp: '2026-08-23T00:00:00Z' },
          { id: 't2', timestamp: '2026-08-24T00:00:00Z', bot_id: 'source-bot' },
        ],
        total: 2,
        page: 2,
        limit: 100,
        has_more: false,
      },
    });
    const context = { botId: 'viewer-bot', botName: 'Viewer', userId: 'owner' };
    const detail = {
      id: 't1',
      timestamp: '2026-08-23T00:00:00Z',
      name: 'Trace',
      groupId: 'group-1',
      status: 'SUCCESS',
      latencyMs: 0,
      totalTokens: 0,
      totalCost: 0,
      observations: [],
    };
    useBotChatStore.getState().openFor(context);
    useBotChatStore.getState().setRelatedState({
      relationScope: 'group',
      related: {
        items: [
          {
            id: detail.id,
            timestamp: detail.timestamp,
            name: detail.name,
            groupId: detail.groupId,
            status: detail.status,
            latencyMs: detail.latencyMs,
            totalTokens: detail.totalTokens,
            totalCost: detail.totalCost,
          },
        ],
        total: 2,
        page: 1,
        limit: 100,
        hasMore: true,
      },
    });

    await botChatService.related(context, detail, 'group', 2, true);

    expect(listBotChats).toHaveBeenCalledWith(
      'viewer-bot',
      expect.objectContaining({
        group_id: 'group-1',
        match_mode: 'exact',
        time_scope: 'all',
        page: 2,
        limit: 100,
      }),
    );

    expect(useBotChatStore.getState().related).toMatchObject({
      total: 2,
      page: 2,
      hasMore: false,
      items: [{ id: 't1' }, { id: 't2', botId: 'source-bot' }],
    });
  });
});
