/** @jest-environment jsdom */
// bcs-standalone-friend-discovery Task 3：发现页 Bot 列表改用 BCS 原始 offset 游标（
// nextOffset/hasMore，不计算精确 total）；角色/搜索词变化时旧 AbortSignal 取消、迟到结果
// 不得覆盖新角色；智能搜索独立保留 search_mode（semantic / name_fallback / empty_query）
// 供「名称匹配」提示消费——空结果也不能丢失模式。
import type {
  BotCandidatePage,
  BotCatalogViewer,
  PublicBot,
  PublicBotDiscoveryResult,
} from '@/domain/collaborationSquare/types';
import { useCollaborationSquareList } from '@/hooks/useCollaborationSquareList';
import { collaborationSquareBotService } from '@/services/collaborationSquare';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { act, renderHook } from '@testing-library/react';

const humanContext = { actorId: 'human_900003', userId: '900003' };
const humanViewer: BotCatalogViewer = { viewerActorType: 'human', viewerActorId: '900003' };
const botViewer: BotCatalogViewer = { viewerActorType: 'bot', viewerActorId: 'assistant:owner-7' };

const resultBot = (id: string, overrides: Partial<PublicBot> = {}): PublicBot => ({
  id,
  name: id,
  ownerName: 'Owner',
  description: '',
  capabilities: [],
  relationshipStatus: 'none',
  ...overrides,
});

/** BCS 原始游标页 fixture：类型上无 total——Hook 只能依赖 nextOffset/hasMore。 */
const cursorPage = (items: PublicBot[], overrides: Partial<BotCandidatePage> = {}): BotCandidatePage => ({
  items,
  nextOffset: items.length,
  hasMore: false,
  ...overrides,
});

function discoveryResult(items: PublicBot[], searchMode: PublicBotDiscoveryResult['searchMode']): PublicBotDiscoveryResult {
  return { items, searchMode };
}

interface PendingCall {
  query: Record<string, unknown>;
  signal: AbortSignal | undefined;
  resolve: (page: BotCandidatePage) => void;
}

interface ListHarnessOptions {
  botQuery?: string;
  botSearchMode?: 'name' | 'smart';
  viewer?: BotCatalogViewer;
}

function renderList(options: ListHarnessOptions = {}) {
  const state = useCollaborationSquareStore.getState();
  return renderHook(
    ({ viewer, query }: { viewer: BotCatalogViewer; query: string }) =>
      useCollaborationSquareList({
        resource: 'bot',
        humanBotContext: humanContext,
        humanIdentityStatus: 'ready',
        botQuery: query,
        groupQuery: '',
        botSearchMode: options.botSearchMode ?? 'name',
        viewer,
        gateSmartEmpty: true,
        setBots: state.setBots,
        appendBots: state.appendBots,
        setGroups: state.setGroups,
        appendGroups: state.appendGroups,
        setLoading: state.setLoading,
        setError: state.setError,
      }),
    { initialProps: { viewer: options.viewer ?? humanViewer, query: options.botQuery ?? '' } },
  );
}

describe('useCollaborationSquareList（BCS 游标与智能搜索模式）', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    useCollaborationSquareStore.getState().reset();
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it('普通列表按 offset=0 + pageSize=24 请求，并保存 BCS 游标返回的 hasMore（不依赖 total）', async () => {
    const pageOne = Array.from({ length: 24 }, (_, index) => resultBot(`bot-${index}`));
    const listBotPage = jest
      .spyOn(collaborationSquareBotService, 'listBotPage')
      .mockResolvedValue(cursorPage(pageOne, { nextOffset: 30, hasMore: true }));
    const { result } = renderList();

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });

    expect(listBotPage).toHaveBeenCalledWith(
      { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
      expect.any(AbortSignal),
    );
    expect(useCollaborationSquareStore.getState().bots).toHaveLength(24);
    expect(result.current.hasMore).toBe(true);
    // 名称模式无智能 search_mode 可展示。
    expect(result.current.searchResultMode).toBeNull();
  });

  it('loadMore 从上一页返回的 BCS 原始 nextOffset 续读（非 page*pageSize），并写回新游标', async () => {
    const pageOne = Array.from({ length: 24 }, (_, index) => resultBot(`bot-${index}`));
    const pageTwo = [resultBot('bot-30'), resultBot('bot-0')];
    const listBotPage = jest
      .spyOn(collaborationSquareBotService, 'listBotPage')
      .mockResolvedValueOnce(cursorPage(pageOne, { nextOffset: 30, hasMore: true }))
      .mockResolvedValueOnce(cursorPage(pageTwo, { nextOffset: 32, hasMore: false }));
    const { result } = renderList();

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      await result.current.loadMore();
    });

    // 续读 offset 来自上一页的 BCS 原始 nextOffset（30），不是 page*pageSize（24）。
    expect(listBotPage).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 30, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' }),
      humanContext,
      expect.any(AbortSignal),
    );
    // 去重合并：24 条 + 新页 2 条其中 1 条与首页重复。
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.id)).toContain('bot-30');
    expect(useCollaborationSquareStore.getState().bots).toHaveLength(25);
    expect(result.current.hasMore).toBe(false);
  });

  it('翻页后改搜索词、防抖未触发时 loadMore 不得用新关键词 + 旧游标发请求', async () => {
    const pending: PendingCall[] = [];
    jest
      .spyOn(collaborationSquareBotService, 'listBotPage')
      .mockImplementation(
        (query, _context, signal) =>
          new Promise((resolve) => {
            pending.push({ query: (query ?? {}) as Record<string, unknown>, signal, resolve });
          }),
      );
    const { result, rerender } = renderList();

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      pending[0].resolve(cursorPage(Array.from({ length: 24 }, (_, index) => resultBot(`a-${index}`)), { nextOffset: 24, hasMore: true }));
      await Promise.resolve();
    });
    act(() => {
      void result.current.loadMore();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(pending).toHaveLength(2);
    await act(async () => {
      pending[1].resolve(cursorPage([resultBot('a-24')], { nextOffset: 25, hasMore: true }));
      await Promise.resolve();
    });
    expect(useCollaborationSquareStore.getState().bots).toHaveLength(25);

    // 搜索词已变但防抖窗口尚未结束：旧游标 + 新关键词的组合请求必须被拦截。
    rerender({ viewer: humanViewer, query: '规划' });
    act(() => {
      void result.current.loadMore();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(pending).toHaveLength(2);
  });

  it('新查询首屏在途期间 loadMore 不得用 offset=0 重复请求并追加首屏', async () => {
    const pending: PendingCall[] = [];
    jest
      .spyOn(collaborationSquareBotService, 'listBotPage')
      .mockImplementation(
        (query, _context, signal) =>
          new Promise((resolve) => {
            pending.push({ query: (query ?? {}) as Record<string, unknown>, signal, resolve });
          }),
      );
    const { result, rerender } = renderList();

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      pending[0].resolve(cursorPage(Array.from({ length: 24 }, (_, index) => resultBot(`a-${index}`)), { nextOffset: 30, hasMore: true }));
      await Promise.resolve();
    });

    rerender({ viewer: humanViewer, query: '规划' });
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    expect(pending).toHaveLength(2);
    expect(pending[1].query).toMatchObject({ offset: 0, search: '规划' });

    // 首屏响应仍未返回：此时续读必须被拦截，不能再次以 offset=0 追加一页。
    act(() => {
      void result.current.loadMore();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(pending).toHaveLength(2);

    await act(async () => {
      pending[1].resolve(cursorPage([resultBot('new-1')], { nextOffset: 1, hasMore: false }));
      await Promise.resolve();
    });
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.id)).toEqual(['new-1']);
  });

  it('搜索词或所选角色变化时游标复位为 0，旧 AbortSignal 被取消，迟到结果不能覆盖新角色', async () => {
    const pending: PendingCall[] = [];
    jest
      .spyOn(collaborationSquareBotService, 'listBotPage')
      .mockImplementation(
        (query, _context, signal) =>
          new Promise((resolve) => {
            pending.push({ query: (query ?? {}) as Record<string, unknown>, signal, resolve });
          }),
      );
    const { result, rerender } = renderList();

    await act(async () => {
     jest.advanceTimersByTime(0);
    });
    expect(pending).toHaveLength(1);
    await act(async () => {
      pending[0].resolve(cursorPage(Array.from({ length: 24 }, (_, index) => resultBot(`a-${index}`)), { nextOffset: 30, hasMore: true }));
      await Promise.resolve();
    });
    // 触发 loadMore（挂起中，由本用例手动控制 resolve），确认其续读使用上一页的 nextOffset。
    act(() => {
      void result.current.loadMore();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(pending[1].query).toMatchObject({ offset: 30 });
    await act(async () => {
      pending[1].resolve(cursorPage([resultBot('a-30')], { nextOffset: 31, hasMore: false }));
      await Promise.resolve();
    });
    expect(useCollaborationSquareStore.getState().bots).toHaveLength(25);

    // 搜索词变化：游标复位为 0 并携带名称关键词；早于防抖窗口不发请求。
    rerender({ viewer: humanViewer, query: '规划' });
    await act(async () => {
      jest.advanceTimersByTime(999);
    });
    expect(pending).toHaveLength(2);
    await act(async () => {
      jest.advanceTimersByTime(1);
    });
    expect(pending[2].query).toMatchObject({ offset: 0, search: '规划' });

    // 角色变化：同样复位游标为 0，并切换为新视角身份；旧请求 AbortSignal 被取消。
    rerender({ viewer: botViewer, query: '规划' });
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    expect(pending).toHaveLength(4);
    expect(pending[3].query).toMatchObject({
      offset: 0,
      search: '规划',
      viewerActorType: 'bot',
      viewerActorId: 'assistant:owner-7',
    });
    expect(pending[2].signal?.aborted).toBe(true);

    // 迟到的旧角色结果不得覆盖新角色列表。
    await act(async () => {
      pending[2].resolve(cursorPage([resultBot('stale-human-result')]));
      await Promise.resolve();
    });
    await act(async () => {
      pending[3].resolve(cursorPage([resultBot('fresh-bot-role-result')]));
      await Promise.resolve();
    });
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.id)).toEqual(['fresh-bot-role-result']);
  });

  it('智能搜索 semantic 命中保留 score/shortProfile 并独立返回 searchResultMode，不接分页', async () => {
    const semanticBot = resultBot('assistant:owner-8', { score: 0.92, shortProfile: '擅长任务规划' });
    const discoverBots = jest
      .spyOn(collaborationSquareBotService, 'discoverBots')
      .mockResolvedValue(discoveryResult([semanticBot], 'semantic'));
    const { result } = renderList({ botSearchMode: 'smart', botQuery: '任务规划' });

    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });

    expect(discoverBots).toHaveBeenCalledWith(
      { keyword: '任务规划', viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
      expect.any(AbortSignal),
    );
    expect(result.current.searchResultMode).toBe('semantic');
    expect(useCollaborationSquareStore.getState().bots[0].score).toBe(0.92);
    expect(useCollaborationSquareStore.getState().bots[0].shortProfile).toBe('擅长任务规划');
    // 智能结果是一次性批次，不提供加载更多。
    expect(result.current.hasMore).toBe(false);
  });

  it('智能搜索 name_fallback 结果保留原 search_mode（供「名称匹配」提示），items 不带 score', async () => {
    const discoverBots = jest
      .spyOn(collaborationSquareBotService, 'discoverBots')
      .mockResolvedValue(discoveryResult([resultBot('assistant:owner-9')], 'name_fallback'));
    const { result } = renderList({ botSearchMode: 'smart', botQuery: '规划' });

    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });

    expect(discoverBots).toHaveBeenCalledWith(
      { keyword: '规划', viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
      expect.any(AbortSignal),
    );
    expect(result.current.searchResultMode).toBe('name_fallback');
    expect(useCollaborationSquareStore.getState().bots[0].score).toBeUndefined();
  });

  it('智能搜索空关键词本地短路：不发请求、清空列表，但仍保留 empty_query 模式', async () => {
    const discoverBots = jest.spyOn(collaborationSquareBotService, 'discoverBots');
    useCollaborationSquareStore.getState().setBots([resultBot('旧结果')]);
    const { result } = renderList({ botSearchMode: 'smart', botQuery: '' });

    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });

    expect(discoverBots).not.toHaveBeenCalled();
    expect(useCollaborationSquareStore.getState().bots).toEqual([]);
    // 空结果不能丢失 empty_query 模式。
    expect(result.current.searchResultMode).toBe('empty_query');
    expect(result.current.hasMore).toBe(false);
  });

  it('普通 Bot 列表失败时不吞错误：错误文案进入列表错误态', async () => {
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockRejectedValue(new Error('BCS 候选服务暂不可用'));
    renderList();

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });

    expect(useCollaborationSquareStore.getState().error).toBe('BCS 候选服务暂不可用');
    expect(useCollaborationSquareStore.getState().bots).toEqual([]);
    expect(useCollaborationSquareStore.getState().loading).toBe(false);
  });
});
