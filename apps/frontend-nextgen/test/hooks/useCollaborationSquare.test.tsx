/** @jest-environment jsdom */
import { notifyError, notifySuccess } from '@/components/ui/notify';
import type { PublicBot, PublicBotSearchQuery, PublicGroup, PublicTask } from '@/domain/collaborationSquare/types';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import {
  CollaborationSquareError,
  collaborationSquareBotService,
  collaborationSquareGroupService,
  collaborationSquareService,
  collaborationSquareTaskService,
} from '@/services/collaborationSquare';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { useSquareIdentityStore } from '@/stores/squareIdentityStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook, waitFor } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));
jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn(), notifySuccess: jest.fn() }));

const mockedUseHumanIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;
const mockedNotifyError = notifyError as jest.MockedFunction<typeof notifyError>;
const mockedNotifySuccess = notifySuccess as jest.MockedFunction<typeof notifySuccess>;
const humanContext = { actorId: 'human_900003', userId: '900003' };
const viewerFields = { viewerActorType: 'human', viewerActorId: '900003' };

interface PendingRequest {
  query: PublicBotSearchQuery | undefined;
  context: typeof humanContext | undefined;
  signal: AbortSignal | undefined;
  resolve: (page: { items: PublicBot[]; total: number }) => void;
}

const resultBot = (id: string): PublicBot => ({
  id,
  name: id,
  ownerName: 'Owner',
  description: '',
  capabilities: [],
  relationshipStatus: 'none',
});

const botPage = (items: PublicBot[], total = items.length) => ({ items, total });

const resultGroup = (id: string): PublicGroup => ({
  id,
  name: id,
  ownerBotName: '主理 Bot',
  ownerUserName: 'Owner',
  typeLabel: '协作群',
  memberCount: 2,
  goal: '',
  memberListVisibility: 'visible',
  canCreateSession: true,
});

const groupPage = (items: PublicGroup[], total = items.length) => ({ items, total });

const resultTask = (id: string): PublicTask => ({
  id,
  name: id,
  goal: `目标 ${id}`,
  acceptanceCriteria: ['验收标准'],
  status: 'pending_claim',
  publisherBotName: '协作助手',
  publishedAt: '2026-08-19T09:00:00Z',
});

describe('useCollaborationSquare Bot Search', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    (history.push as jest.Mock).mockClear();
    useCollaborationSquareStore.getState().reset();
    useSquareIdentityStore.getState().reset();
    useWorkspaceStore.getState().reset();
    useWorkspaceStore
      .getState()
      .setIdentities([{ id: 'human_900003', kind: 'user', displayName: '当前用户', online: true }], 'human_900003');
    mockedUseHumanIdentity.mockReturnValue({
      identity: { userId: '900003', displayName: '当前用户', online: true },
      status: 'ready',
    });
    mockedNotifyError.mockClear();
    mockedNotifySuccess.mockClear();
    window.history.replaceState({}, '', '/collaboration-square/bots');
  });

  afterEach(() => {
    jest.restoreAllMocks();
    jest.useRealTimers();
  });

  test('名称搜索按 1 秒防抖、取消旧请求并阻止过期结果覆盖', async () => {
    const pending: PendingRequest[] = [];
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(
      (query, context, signal) =>
        new Promise<{ items: PublicBot[]; total: number }>((resolve) => {
          pending.push({ query, context, signal, resolve });
        }),
    );
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    act(() => {
      jest.advanceTimersByTime(0);
    });
    expect(pending).toHaveLength(1);
    expect(pending[0].query).toEqual({ page: 1, pageSize: 24, ...viewerFields });
    expect(pending[0].context).toEqual(humanContext);

    act(() => {
      result.current.setQuery('bot', ' workflow ');
    });
    expect(pending[0].signal?.aborted).toBe(true);
    act(() => {
      jest.advanceTimersByTime(999);
    });
    expect(pending).toHaveLength(1);
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(pending).toHaveLength(2);
    expect(pending[1].query).toEqual({ search: 'workflow', page: 1, pageSize: 24, ...viewerFields });

    await act(async () => {
      pending[1].resolve(botPage([resultBot('new')]));
    });
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.id)).toEqual(['new']);

    await act(async () => {
      pending[0].resolve(botPage([resultBot('stale')]));
    });
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.id)).toEqual(['new']);

    unmount();
  });

  test('分享链接携带 Bot 名称提示，Clipboard 成功后只提示复制成功', async () => {
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([]));
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      result.current.share('bot', 'bot:target', '项目助手');
      await Promise.resolve();
    });

    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/collaboration-square/bots?resource=bot&id=bot%3Atarget&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B`,
    );
    expect(mockedNotifySuccess).toHaveBeenCalledWith('分享链接已复制');
    expect(mockedNotifyError).not.toHaveBeenCalled();
    unmount();
  });

  test('分享链接 Clipboard 失败时提示权限错误且不误报成功', async () => {
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([]));
    const writeText = jest.fn().mockRejectedValue(new Error('clipboard denied'));
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      result.current.share('bot', 'bot:target', '项目助手');
      await Promise.resolve();
    });

    expect(mockedNotifyError).toHaveBeenCalledWith('复制失败，请检查浏览器剪贴板权限');
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
    unmount();
  });

  test('分享链接在仅单一维度公开时携带受众类型参数', async () => {
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([]));
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      result.current.share('bot', 'bot:target', '项目助手', 'user');
      await Promise.resolve();
    });

    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/collaboration-square/bots?resource=bot&id=bot%3Atarget&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B&audience=user`,
    );
    expect(mockedNotifySuccess).toHaveBeenCalledWith('分享链接已复制');
    unmount();
  });

  test('Bot 分享深链自动对齐名称搜索，列表加载后定位目标卡片高亮且不打开画像', async () => {
    const target = {
      ...resultBot('bot:target'),
      name: '项目助手',
      description: '公开描述',
    };
    const listPending: PendingRequest[] = [];
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(
      (query, context, signal) =>
        new Promise<{ items: PublicBot[]; total: number }>((resolve) => {
          listPending.push({ query, context, signal, resolve });
        }),
    );
    const resolveSharedBot = jest.spyOn(collaborationSquareBotService, 'resolveSharedBot');
    window.history.replaceState(
      {},
      '',
      '/collaboration-square/bots?resource=bot&id=bot%3Atarget&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B',
    );

    const { unmount } = renderHook(() => useCollaborationSquare('bot'));
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listPending).toHaveLength(1);

    // 初始列表加载完成 → 深链把搜索对齐到分享名称（名称模式 + 关键词）
    await act(async () => {
      listPending[0].resolve(botPage([]));
      await Promise.resolve();
    });
    expect(useCollaborationSquareStore.getState().botQuery).toBe('项目助手');
    expect(useCollaborationSquareStore.getState().botSearchMode).toBe('name');

    // 深链对齐触发的名称搜索按 1 秒防抖加载，请求携带当前协作身份
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    expect(listPending).toHaveLength(2);
    expect(listPending[1].query).toEqual({ search: '项目助手', page: 1, pageSize: 24, ...viewerFields });
    expect(listPending[1].context).toEqual(humanContext);

    // 名称搜索结果加载完成 → 列表内按 ID 定位目标卡片并高亮，不再打开画像
    await act(async () => {
      listPending[1].resolve(botPage([target]));
      await Promise.resolve();
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    const state = useCollaborationSquareStore.getState();
    expect(state.highlightBotId).toBe('bot:target');
    expect(state.selectedBotId).toBeNull();
    expect(state.botProfile).toBeNull();
    expect(resolveSharedBot).not.toHaveBeenCalled();
    expect(mockedNotifyError).not.toHaveBeenCalled();
    expect(window.location.search).toContain('id=bot%3Atarget');
    unmount();
  });

  test('Bot 分享深链搜索态已对齐且列表含目标时直接定位高亮，不重复触发搜索', async () => {
    const target = { ...resultBot('bot:loaded'), name: '当前页助手' };
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([target]));
    const resolveSharedBot = jest.spyOn(collaborationSquareBotService, 'resolveSharedBot');
    useCollaborationSquareStore.getState().setQuery('bot', '当前页助手');
    window.history.replaceState(
      {},
      '',
      '/collaboration-square/bots?resource=bot&id=bot%3Aloaded&name=%E5%BD%93%E5%89%8D%E9%A1%B5%E5%8A%A9%E6%89%8B',
    );

    const { unmount } = renderHook(() => useCollaborationSquare('bot'));
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });

    await waitFor(() => expect(useCollaborationSquareStore.getState().highlightBotId).toBe('bot:loaded'));
    const listBotPage = collaborationSquareBotService.listBotPage as jest.Mock;
    expect(listBotPage).toHaveBeenCalledTimes(1);
    expect(resolveSharedBot).not.toHaveBeenCalled();
    expect(useCollaborationSquareStore.getState().selectedBotId).toBeNull();
    unmount();
  });

  test('Bot 分享深链名称搜索失败时不误判目标失效，重新加载后仍可定位', async () => {
    const target = { ...resultBot('bot:target'), name: '项目助手' };
    const listPending: (PendingRequest & { reject: (reason?: unknown) => void })[] = [];
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(
      (query, context, signal) =>
        new Promise<{ items: PublicBot[]; total: number }>((resolve, reject) => {
          listPending.push({ query, context, signal, resolve, reject });
        }),
    );
    const resolveSharedBot = jest.spyOn(collaborationSquareBotService, 'resolveSharedBot');
    window.history.replaceState(
      {},
      '',
      '/collaboration-square/bots?resource=bot&id=bot%3Atarget&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B',
    );

    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      listPending[0].resolve(botPage([]));
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    await act(async () => {
      listPending[1].reject(new Error('Catalog Search 暂不可用'));
      await Promise.resolve();
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });

    // 列表搜索失败由列表错误态承载：不误判目标失效，深链参数保留待重试
    expect(useCollaborationSquareStore.getState().error).toBe('Catalog Search 暂不可用');
    expect(mockedNotifyError).not.toHaveBeenCalledWith('内容已取消公开或对当前账号不可见');
    expect(useCollaborationSquareStore.getState().highlightBotId).toBeNull();
    expect(window.location.search).toContain('id=bot%3Atarget');

    // 用户重新加载成功后深链继续定位
    await act(async () => {
      result.current.reload();
      await Promise.resolve();
    });
    expect(listPending).toHaveLength(3);
    await act(async () => {
      listPending[2].resolve(botPage([target]));
      await Promise.resolve();
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await waitFor(() => expect(useCollaborationSquareStore.getState().highlightBotId).toBe('bot:target'));
    expect(resolveSharedBot).not.toHaveBeenCalled();
    unmount();
  });

  test('Bot 分享深链名称搜索无精确 ID 命中时按目标失效处理', async () => {
    const other = { ...resultBot('bot:other'), name: '项目助手' };
    const listPending: PendingRequest[] = [];
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(
      (query, context, signal) =>
        new Promise<{ items: PublicBot[]; total: number }>((resolve) => {
          listPending.push({ query, context, signal, resolve });
        }),
    );
    window.history.replaceState(
      {},
      '',
      '/collaboration-square/bots?resource=bot&id=bot%3Atarget&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B',
    );

    const { unmount } = renderHook(() => useCollaborationSquare('bot'));
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      listPending[0].resolve(botPage([]));
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    await act(async () => {
      listPending[1].resolve(botPage([other]));
      await Promise.resolve();
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });

    await waitFor(() => expect(mockedNotifyError).toHaveBeenCalledWith('内容已取消公开或对当前账号不可见'));
    expect(window.location.search).toBe('');
    expect(useCollaborationSquareStore.getState().highlightBotId).toBeNull();
    expect(useCollaborationSquareStore.getState().selectedBotId).toBeNull();
    unmount();
  });

  test('身份中途就绪（冷加载失败后恢复）后列表按分享名称重载并定位高亮成功', async () => {
    // 阶段 1：身份不可用（对应线上冷加载「当前工作身份不可用」），列表被门控不加载，深链不做判定
    mockedUseHumanIdentity.mockReturnValue({ identity: null, status: 'error', error: '身份未加载' });
    const listPending: PendingRequest[] = [];
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(
      (query, context, signal) =>
        new Promise<{ items: PublicBot[]; total: number }>((resolve) => {
          listPending.push({ query, context, signal, resolve });
        }),
    );
    const resolveSharedBot = jest.spyOn(collaborationSquareBotService, 'resolveSharedBot');
    window.history.replaceState(
      {},
      '',
      '/collaboration-square/bots?resource=bot&id=bot%3Atarget&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B',
    );

    const { rerender, unmount } = renderHook(() => useCollaborationSquare('bot'));
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(useCollaborationSquareStore.getState().error).toBe('当前工作身份不可用，请刷新后重试');
    expect(listPending).toHaveLength(0);
    expect(useCollaborationSquareStore.getState().highlightBotId).toBeNull();

    // 阶段 2：身份恢复就绪（用户点「重新加载」/身份服务恢复），列表与深链自动重驱动
    mockedUseHumanIdentity.mockReturnValue({
      identity: { userId: '900003', displayName: '当前用户', online: true },
      status: 'ready',
    });
    await act(async () => {
      rerender();
    });
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listPending).toHaveLength(1);

    // 初始列表加载完成 → 深链对齐名称搜索 → 列表按新条件重载 → 定位高亮
    await act(async () => {
      listPending[0].resolve(botPage([]));
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    await act(async () => {
      listPending[1].resolve(botPage([{ ...resultBot('bot:target'), name: '项目助手' }]));
      await Promise.resolve();
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });

    expect(useCollaborationSquareStore.getState().highlightBotId).toBe('bot:target');
    expect(resolveSharedBot).not.toHaveBeenCalled();
    expect(mockedNotifyError).not.toHaveBeenCalled();
    expect(window.location.search).toContain('id=bot%3Atarget');
    unmount();
  });

  test('深链在当前身份视角未命中时自动切换公开受众匹配的身份并定位成功', async () => {
    const target = { ...resultBot('bot:target'), name: '项目助手' };
    // 用户名下含 Bot 身份，发现页当前选中该 Bot 身份浏览
    useWorkspaceStore
      .getState()
      .setIdentities(
        [
          { id: 'human_900003', kind: 'user', displayName: '当前用户', online: true },
          { id: 'bot_910001', kind: 'bot', displayName: '我的 Bot', online: true },
        ],
        'human_900003',
      );
    useSquareIdentityStore.getState().selectIdentity('bot_910001');
    const listPending: PendingRequest[] = [];
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(
      (query, context, signal) =>
        new Promise<{ items: PublicBot[]; total: number }>((resolve) => {
          listPending.push({ query, context, signal, resolve });
        }),
    );
    window.history.replaceState(
      {},
      '',
      '/collaboration-square/bots?resource=bot&id=bot%3Atarget&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B',
    );

    const { unmount } = renderHook(() => useCollaborationSquare('bot'));
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    // Bot 身份视角初始列表加载完成 → 深链对齐名称搜索
    await act(async () => {
      listPending[0].resolve(botPage([]));
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    expect(listPending[1].query).toEqual(
      expect.objectContaining({ search: '项目助手', viewerActorType: 'bot', viewerActorId: 'bot_910001' }),
    );

    // Bot 身份视角搜索不到目标 → 自动切换为用户身份并轻提示
    await act(async () => {
      listPending[1].resolve(botPage([]));
      await Promise.resolve();
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(mockedNotifySuccess).toHaveBeenCalledWith('该 Bot 仅对用户公开，已为你切换为用户身份查看');
    expect(useSquareIdentityStore.getState().selectedIdentityId).toBeNull();

    // 切换身份后列表按用户身份重载 → 深链重新对齐搜索 → 定位高亮成功
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      listPending[2].resolve(botPage([]));
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    expect(listPending[3].query).toEqual(
      expect.objectContaining({ search: '项目助手', viewerActorType: 'human', viewerActorId: '900003' }),
    );
    await act(async () => {
      listPending[3].resolve(botPage([target]));
      await Promise.resolve();
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(useCollaborationSquareStore.getState().highlightBotId).toBe('bot:target');
    expect(mockedNotifyError).not.toHaveBeenCalled();
    expect(window.location.search).toContain('id=bot%3Atarget');
    unmount();
  });

  test('深链两类身份视角都未命中时才按目标失效处理', async () => {
    // 用户名下含 Bot 身份，当前以用户身份浏览（搜索结果不含目标 → 切 Bot 身份重试仍不中 → 失效）
    useWorkspaceStore
      .getState()
      .setIdentities(
        [
          { id: 'human_900003', kind: 'user', displayName: '当前用户', online: true },
          { id: 'bot_910001', kind: 'bot', displayName: '我的 Bot', online: true },
        ],
        'human_900003',
      );
    const listPending: PendingRequest[] = [];
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(
      (query, context, signal) =>
        new Promise<{ items: PublicBot[]; total: number }>((resolve) => {
          listPending.push({ query, context, signal, resolve });
        }),
    );
    window.history.replaceState(
      {},
      '',
      '/collaboration-square/bots?resource=bot&id=bot%3Atarget&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B',
    );

    const { unmount } = renderHook(() => useCollaborationSquare('bot'));
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      listPending[0].resolve(botPage([]));
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    // 用户身份视角搜索不中 → 自动切换 Bot 身份
    await act(async () => {
      listPending[1].resolve(botPage([]));
      await Promise.resolve();
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(mockedNotifySuccess).toHaveBeenCalledWith('该 Bot 仅对 Bot 公开，已为你切换为 Bot 身份查看');
    expect(useSquareIdentityStore.getState().selectedIdentityId).toBe('bot_910001');
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      listPending[2].resolve(botPage([]));
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    // Bot 身份视角仍未中 → 按目标失效处理
    await act(async () => {
      listPending[3].resolve(botPage([]));
      await Promise.resolve();
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(mockedNotifyError).toHaveBeenCalledWith('内容已取消公开或对当前账号不可见'),
    );
    expect(window.location.search).toBe('');
    expect(useCollaborationSquareStore.getState().highlightBotId).toBeNull();
    unmount();
  });

  test('深链声明仅对用户公开且当前为 Bot 身份时，先切换身份再定位，不空跑 Bot 视角搜索', async () => {
    const target = { ...resultBot('bot:target'), name: '项目助手' };
    useWorkspaceStore
      .getState()
      .setIdentities(
        [
          { id: 'human_900003', kind: 'user', displayName: '当前用户', online: true },
          { id: 'bot_910001', kind: 'bot', displayName: '我的 Bot', online: true },
        ],
        'human_900003',
      );
    useSquareIdentityStore.getState().selectIdentity('bot_910001');
    const listPending: PendingRequest[] = [];
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(
      (query, context, signal) =>
        new Promise<{ items: PublicBot[]; total: number }>((resolve) => {
          listPending.push({ query, context, signal, resolve });
        }),
    );
    window.history.replaceState(
      {},
      '',
      '/collaboration-square/bots?resource=bot&id=bot%3Atarget&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B&audience=user',
    );

    const { unmount } = renderHook(() => useCollaborationSquare('bot'));
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    // Bot 身份视角初始列表加载完成 → 深链按声明的受众类型直接切换为用户身份
    await act(async () => {
      listPending[0].resolve(botPage([]));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mockedNotifySuccess).toHaveBeenCalledWith('该 Bot 仅对用户公开，已为你切换为用户身份查看');
    expect(useSquareIdentityStore.getState().selectedIdentityId).toBeNull();

    // 身份切换后按用户身份重载列表 → 对齐名称搜索 → 定位高亮
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      listPending[1].resolve(botPage([]));
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    expect(listPending[2].query).toEqual(
      expect.objectContaining({ search: '项目助手', viewerActorType: 'human', viewerActorId: '900003' }),
    );
    await act(async () => {
      listPending[2].resolve(botPage([target]));
      await Promise.resolve();
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(useCollaborationSquareStore.getState().highlightBotId).toBe('bot:target');
    // 前置切换发生在搜索对齐之前：Bot 视角从未发起名称搜索
    expect(
      listPending.filter((p) => p.query?.search === '项目助手' && p.query.viewerActorType === 'bot'),
    ).toHaveLength(0);
    unmount();
  });

  test('深链声明仅对 Bot 公开且账号无 Bot 身份时，直接按目标失效处理', async () => {
    const listPending: PendingRequest[] = [];
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(
      (query, context, signal) =>
        new Promise<{ items: PublicBot[]; total: number }>((resolve) => {
          listPending.push({ query, context, signal, resolve });
        }),
    );
    window.history.replaceState(
      {},
      '',
      '/collaboration-square/bots?resource=bot&id=bot%3Atarget&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B&audience=bot',
    );

    const { unmount } = renderHook(() => useCollaborationSquare('bot'));
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      listPending[0].resolve(botPage([]));
      await Promise.resolve();
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });

    await waitFor(() =>
      expect(mockedNotifyError).toHaveBeenCalledWith('内容已取消公开或对当前账号不可见'),
    );
    expect(window.location.search).toBe('');
    expect(useCollaborationSquareStore.getState().highlightBotId).toBeNull();
    // 未发起任何名称搜索即失效（受众类型已声明必然不可见）
    expect(listPending.filter((p) => p.query?.search)).toHaveLength(0);
    unmount();
  });

  test('深链声明的受众类型与当前身份匹配时不切换，直接定位', async () => {
    const target = { ...resultBot('bot:target'), name: '项目助手' };
    const listPending: PendingRequest[] = [];
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(
      (query, context, signal) =>
        new Promise<{ items: PublicBot[]; total: number }>((resolve) => {
          listPending.push({ query, context, signal, resolve });
        }),
    );
    window.history.replaceState(
      {},
      '',
      '/collaboration-square/bots?resource=bot&id=bot%3Atarget&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B&audience=user',
    );

    const { unmount } = renderHook(() => useCollaborationSquare('bot'));
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      listPending[0].resolve(botPage([]));
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    await act(async () => {
      listPending[1].resolve(botPage([target]));
      await Promise.resolve();
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });

    expect(useCollaborationSquareStore.getState().highlightBotId).toBe('bot:target');
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
    expect(useSquareIdentityStore.getState().selectedIdentityId).toBeNull();
    unmount();
  });

  test('首屏使用 24 条并在加载更多时请求下一页且去重合并', async () => {
    const listBots = jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(async (query) => {
      if (query?.page === 1)
        return botPage(
          Array.from({ length: 24 }, (_, index) => resultBot(`page-1-${index}`)),
          26,
        );
      return botPage([resultBot('page-2-0'), resultBot('page-1-0')], 26);
    });
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listBots).toHaveBeenLastCalledWith(
      { page: 1, pageSize: 24, ...viewerFields },
      humanContext,
      expect.any(AbortSignal),
    );
    expect(result.current.hasMore).toBe(true);

    await act(async () => {
      await result.current.loadMore();
    });

    expect(listBots).toHaveBeenLastCalledWith(
      { page: 2, pageSize: 24, ...viewerFields },
      humanContext,
      expect.any(AbortSignal),
    );
    expect(useCollaborationSquareStore.getState().bots).toHaveLength(25);
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.id)).toContain('page-2-0');
    expect(result.current.hasMore).toBe(false);

    unmount();
  });

  test('名称搜索与智能搜索互相切换时清空已有输入', async () => {
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([]));
    jest.spyOn(collaborationSquareBotService, 'discoverBots').mockResolvedValue([]);
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });

    act(() => {
      result.current.setQuery('bot', '旧名称');
      result.current.setBotSearchMode('smart');
    });
    expect(result.current.botSearchMode).toBe('smart');
    expect(result.current.botQuery).toBe('');

    act(() => {
      result.current.setQuery('bot', '能力描述');
      result.current.setBotSearchMode('name');
    });
    expect(result.current.botSearchMode).toBe('name');
    expect(result.current.botQuery).toBe('');

    act(() => {
      result.current.setQuery('bot', '保留当前模式输入');
      result.current.setBotSearchMode('name');
    });
    expect(result.current.botQuery).toBe('保留当前模式输入');

    unmount();
  });

  test('智能搜索对非空能力描述调用真实 Discovery，空关键词只展示提示不发请求', async () => {
    const listBots = jest
      .spyOn(collaborationSquareBotService, 'listBotPage')
      .mockResolvedValue(botPage([resultBot('default')]));
    const discoverBots = jest
      .spyOn(collaborationSquareBotService, 'discoverBots')
      .mockResolvedValue([
        { ...resultBot('code'), name: '研发助手', description: '代码审查与质量改进', capabilities: ['代码审查'] },
      ]);
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    expect(listBots).toHaveBeenLastCalledWith(
      { page: 1, pageSize: 24, ...viewerFields },
      humanContext,
      expect.any(AbortSignal),
    );

    // 切到智能搜索但未输入关键词：不发请求、清空列表，交由面板展示输入提示。
    act(() => {
      result.current.setBotSearchMode('smart');
    });
    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    expect(discoverBots).not.toHaveBeenCalled();
    expect(result.current.visibleBots).toHaveLength(0);

    act(() => {
      result.current.setQuery('bot', '代码');
    });
    await act(async () => {
      jest.advanceTimersByTime(999);
    });
    expect(discoverBots).not.toHaveBeenCalled();
    await act(async () => {
      jest.advanceTimersByTime(1);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(discoverBots).toHaveBeenLastCalledWith(
      { keyword: '代码', topK: 20, minScore: 0.01, runtimeState: 'online', ...viewerFields },
      humanContext,
      expect.any(AbortSignal),
    );
    expect(result.current.visibleBots.map((bot) => bot.id)).toEqual(['code']);

    act(() => {
      result.current.setQuery('bot', '');
    });
    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    // 清空关键词再次回到提示态：不调用 Discovery、也不退回默认目录。
    expect(discoverBots).toHaveBeenCalledTimes(1);
    expect(result.current.visibleBots).toHaveLength(0);

    unmount();
  });

  test('成员弹窗同一次请求回填 Owner，切群清空且旧响应不能覆盖新群', async () => {
    const first = resultGroup('g1');
    const second = resultGroup('g2');
    jest.spyOn(collaborationSquareGroupService, 'listGroupPage').mockResolvedValue(groupPage([first, second]));
    const pending: Array<(detail: { ownerUserName: string; members: [] }) => void> = [];
    const fetchDetail = jest.spyOn(collaborationSquareGroupService, 'listGroupMembers').mockImplementation(
      () =>
        new Promise((resolve) => {
          pending.push(resolve);
        }),
    );
    const { result, unmount } = renderHook(() => useCollaborationSquare('group'));
    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    act(() => {
      void result.current.openGroupMembers(first);
    });
    await act(async () => {
      pending[0]({ ownerUserName: 'Owner A', members: [] });
    });
    expect(result.current.groupOwnerUserName).toBe('Owner A');
    act(() => {
      void result.current.openGroupMembers(first);
    });
    act(() => {
      void result.current.openGroupMembers(second);
    });
    expect(result.current.groupOwnerUserName).toBeNull();
    await act(async () => {
      pending[2]({ ownerUserName: 'Owner B', members: [] });
    });
    await act(async () => {
      pending[1]({ ownerUserName: '过期 Owner A', members: [] });
    });
    expect(result.current.groupOwnerUserName).toBe('Owner B');
    expect(result.current.selectedGroupId).toBe('g2');
    expect(result.current.detailLoading).toBe(false);
    expect(fetchDetail).toHaveBeenCalledTimes(3);
    unmount();
  });

  test.each(['close', 'unmount', 'identity'] as const)('成员请求在 %s 后返回不回填 Owner 或成员', async (action) => {
    const group = resultGroup('g1');
    jest.spyOn(collaborationSquareGroupService, 'listGroupPage').mockResolvedValue(groupPage([group]));
    let finish!: (detail: { ownerUserName: string; members: [] }) => void;
    jest.spyOn(collaborationSquareGroupService, 'listGroupMembers').mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { result, rerender, unmount } = renderHook(() => useCollaborationSquare('group'));
    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    act(() => {
      void result.current.openGroupMembers(group);
    });
    if (action === 'close')
      act(() => {
        result.current.closeGroupMembers();
      });
    if (action === 'unmount') unmount();
    if (action === 'identity') {
      mockedUseHumanIdentity.mockReturnValue({ identity: null, status: 'loading' });
      rerender();
    }
    await act(async () => {
      finish({ ownerUserName: '过期用户', members: [] });
    });
    expect(useCollaborationSquareStore.getState().groupOwnerUserName).toBeNull();
    expect(useCollaborationSquareStore.getState().groupMembers).toEqual([]);
    if (action !== 'unmount') unmount();
  });

  test('旧群详情失败不关闭新群、不提前结束新请求加载，也不弹过期错误', async () => {
    const first = resultGroup('g1');
    const second = resultGroup('g2');
    jest.spyOn(collaborationSquareGroupService, 'listGroupPage').mockResolvedValue(groupPage([first, second]));
    let failFirst!: (error: Error) => void;
    let finishSecond!: (detail: { ownerUserName: string; members: [] }) => void;
    jest
      .spyOn(collaborationSquareGroupService, 'listGroupMembers')
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            failFirst = reject;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishSecond = resolve;
          }),
      );
    const { result, unmount } = renderHook(() => useCollaborationSquare('group'));
    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    act(() => {
      void result.current.openGroupMembers(first);
    });
    act(() => {
      void result.current.openGroupMembers(second);
    });
    await act(async () => {
      failFirst(new CollaborationSquareError('target_invalid', '旧群失效'));
    });
    expect(result.current.selectedGroupId).toBe('g2');
    expect(result.current.detailLoading).toBe(true);
    expect(mockedNotifyError).not.toHaveBeenCalled();
    await act(async () => {
      finishSecond({ ownerUserName: 'Owner B', members: [] });
    });
    expect(result.current.groupOwnerUserName).toBe('Owner B');
    expect(result.current.detailLoading).toBe(false);
    unmount();
  });

  test('当前群详情失败维持错误提示且不展示目录里的 Owner', async () => {
    const group = resultGroup('g1');
    jest.spyOn(collaborationSquareGroupService, 'listGroupPage').mockResolvedValue(groupPage([group]));
    jest.spyOn(collaborationSquareGroupService, 'listGroupMembers').mockRejectedValue(new Error('详情不可用'));
    const { result, unmount } = renderHook(() => useCollaborationSquare('group'));
    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      await result.current.openGroupMembers(group);
    });
    expect(mockedNotifyError).toHaveBeenCalled();
    expect(result.current.groupOwnerUserName).toBeNull();
    expect(result.current.detailLoading).toBe(false);
    unmount();
  });

  test('公开群首次加载和名称搜索调用真实 Group List，详情仍保留原 Service 链路', async () => {
    const group: PublicGroup = {
      id: 'group-real-1',
      name: '公开群',
      ownerBotName: '主理 Bot',
      ownerUserName: 'Owner',
      typeLabel: '协作群',
      memberCount: 2,
      goal: '',
      memberListVisibility: 'visible',
      canCreateSession: true,
    };
    const listGroups = jest
      .spyOn(collaborationSquareGroupService, 'listGroupPage')
      .mockResolvedValue(groupPage([group]));
    const realMembers = jest.spyOn(collaborationSquareGroupService, 'listGroupMembers');
    const legacyMembers = jest
      .spyOn(collaborationSquareService, 'listGroupMembers')
      .mockResolvedValue({ members: [], ownerUserName: '未公开' });
    const { result, unmount } = renderHook(() => useCollaborationSquare('group'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    expect(listGroups).toHaveBeenLastCalledWith({ offset: 0, limit: 24 }, expect.any(AbortSignal));

    act(() => {
      result.current.setQuery('group', ' 公开 ');
    });
    await act(async () => {
      jest.advanceTimersByTime(999);
    });
    expect(listGroups).toHaveBeenCalledTimes(1);
    await act(async () => {
      jest.advanceTimersByTime(1);
    });
    expect(listGroups).toHaveBeenLastCalledWith({ search: '公开', offset: 0, limit: 24 }, expect.any(AbortSignal));

    await act(async () => {
      await result.current.openGroupMembers(group);
    });
    // 公开群成员经真实 service 调群详情 participants 反查（见 adapter.listGroupMembers），
    // 不再走 legacy/mock 链路（mock 在 dev/pre 会 404 误报 target_invalid）。
    expect(realMembers).toHaveBeenCalledWith(group.id);
    expect(legacyMembers).not.toHaveBeenCalled();

    unmount();
  });

  test('公开群在接近列表底部时按 offset=24 加载下一页并去重合并', async () => {
    const firstPage = Array.from({ length: 24 }, (_, index) => resultGroup(`group-page-1-${index}`));
    const secondPage = [resultGroup('group-page-2-0'), resultGroup(firstPage[0].id)];
    const listGroups = jest
      .spyOn(collaborationSquareGroupService, 'listGroupPage')
      .mockImplementation(async (query) =>
        query?.offset === 0 ? groupPage(firstPage, 26) : groupPage(secondPage, 26),
      );
    const { result, unmount } = renderHook(() => useCollaborationSquare('group'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    expect(result.current.hasMore).toBe(true);

    await act(async () => {
      await result.current.loadMore();
    });

    expect(listGroups).toHaveBeenLastCalledWith({ offset: 24, limit: 24 }, expect.any(AbortSignal));
    expect(useCollaborationSquareStore.getState().groups).toHaveLength(25);
    expect(useCollaborationSquareStore.getState().groups.map((item) => item.id)).toContain('group-page-2-0');
    expect(result.current.hasMore).toBe(false);

    unmount();
  });

  test('公开群创建会话调用真实 Group Service，并只使用服务端返回的会话信息导航', async () => {
    const group: PublicGroup = {
      id: 'group-real-session',
      name: '公开群',
      ownerBotName: '主理 Bot',
      ownerUserName: 'Owner',
      typeLabel: '协作群',
      memberCount: 2,
      goal: '',
      memberListVisibility: 'visible',
      canCreateSession: true,
    };
    jest.spyOn(collaborationSquareGroupService, 'listGroupPage').mockResolvedValue(groupPage([group]));
    const createGroupSession = jest
      .spyOn(collaborationSquareGroupService, 'createGroupSession')
      .mockResolvedValue({ sessionId: 'session-real-group', defaultRole: '顾问', memberSource: 'session_temp' });
    const legacyCreateGroupSession = jest.spyOn(collaborationSquareService, 'createGroupSession');
    const { result, unmount } = renderHook(() => useCollaborationSquare('group'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    act(() => {
      result.current.createGroupSession(group);
    });
    expect(result.current.createSessionTarget).toEqual(group);

    await act(async () => {
      result.current.submitCreateSession({ title: '测试会话', query: '测试协作目标', messageViewScope: 'full' });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(createGroupSession).toHaveBeenCalledWith(group.id, humanContext, {
      title: '测试会话',
      query: '测试协作目标',
      messageViewScope: 'full',
    });
    expect(legacyCreateGroupSession).not.toHaveBeenCalled();
    expect(history.push).toHaveBeenCalledWith(
      '/workspace/collaboration?current=human_900003&group=group-real-session&session=session-real-group&membership=session_only',
    );

    unmount();
  });

  test('Bot 列表和好友申请使用真实 Service，画像仍保留原有链路', async () => {
    const bot = resultBot('legacy-actions');
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
    const realProfile = jest.spyOn(collaborationSquareBotService, 'getBotProfile');
    const legacyProfile = jest.spyOn(collaborationSquareService, 'getBotProfile').mockResolvedValue({
      id: bot.id,
      name: bot.name,
      ownerName: bot.ownerName,
      description: bot.description,
      capabilities: [],
    });
    const realFriendship = jest
      .spyOn(collaborationSquareBotService, 'requestBotFriendship')
      .mockResolvedValue({ status: 'applying' });
    const legacyFriendship = jest.spyOn(collaborationSquareService, 'requestBotFriendship');
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      await result.current.openBotProfile(bot);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
    });

    expect(legacyProfile).toHaveBeenCalledWith(bot.id);
    expect(realProfile).not.toHaveBeenCalled();
    expect(realFriendship).toHaveBeenCalledWith(bot.id, humanContext, undefined, { type: 'human', id: '900003' });
    expect(legacyFriendship).not.toHaveBeenCalled();
    expect(useCollaborationSquareStore.getState().bots[0].relationshipStatus).toBe('applying');

    unmount();
  });

  test('好友申请优先把搜索结果中的 friendRequestBotId 传给真实 Service', async () => {
    const bot = { ...resultBot('default'), friendRequestBotId: 'default:366656' };
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
    const requestFriendship = jest
      .spyOn(collaborationSquareBotService, 'requestBotFriendship')
      .mockResolvedValue({ status: 'applying' });
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
    });

    expect(requestFriendship).toHaveBeenCalledWith(bot.id, humanContext, 'default:366656', {
      type: 'human',
      id: '900003',
    });
    unmount();
  });

  test('重复原始 Bot ID 时只锁定并更新发起申请的目标卡片', async () => {
    const botA = { ...resultBot('default'), name: 'Bot A', friendRequestBotId: 'default:entity-a' };
    const botB = { ...resultBot('default'), name: 'Bot B', friendRequestBotId: 'default:entity-b' };
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([botA, botB]));
    let resolveRequest: ((value: { status: 'applying' }) => void) | undefined;
    const requestFriendship = jest.spyOn(collaborationSquareBotService, 'requestBotFriendship').mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRequest = resolve;
        }),
    );
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(botA);
      await Promise.resolve();
    });

    expect(requestFriendship).toHaveBeenCalledWith(botA.id, humanContext, botA.friendRequestBotId, {
      type: 'human',
      id: '900003',
    });
    expect(useCollaborationSquareStore.getState().busyKeys).toEqual(['bot:human:900003:default:entity-a']);
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.relationshipStatus)).toEqual(['none', 'none']);

    await act(async () => {
      resolveRequest?.({ status: 'applying' });
      await Promise.resolve();
    });

    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.relationshipStatus)).toEqual([
      'applying',
      'none',
    ]);
    expect(useCollaborationSquareStore.getState().busyKeys).toEqual([]);
    unmount();
  });

  test('已是好友时创建真实 Bot 会话并使用 Workspace 单聊 URL 导航', async () => {
    const bot = { ...resultBot('bot-1:2088'), relationshipStatus: 'friend' as const };
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
    const openConversation = jest
      .spyOn(collaborationSquareBotService, 'openBotConversation')
      .mockResolvedValue({ sessionId: 'session-1' });
    const legacyConversation = jest.spyOn(collaborationSquareService, 'openBotConversation');
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(openConversation).toHaveBeenCalledWith(bot.id, humanContext, { isOwnedByLoggedInUser: false });
    expect(legacyConversation).not.toHaveBeenCalled();
    expect(history.push).toHaveBeenCalledWith('/workspace/chat?bot=bot-1%3A2088&session=session-1');

    unmount();
  });

  test('自有公开 Bot 直接创建会话，不发起好友申请', async () => {
    const bot = { ...resultBot('owned-bot'), isOwnedByLoggedInUser: true };
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
    const requestFriendship = jest.spyOn(collaborationSquareBotService, 'requestBotFriendship');
    const openConversation = jest
      .spyOn(collaborationSquareBotService, 'openBotConversation')
      .mockResolvedValue({ sessionId: 'session-owned' });
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(requestFriendship).not.toHaveBeenCalled();
    expect(openConversation).toHaveBeenCalledWith(bot.id, humanContext, { isOwnedByLoggedInUser: true });
    expect(history.push).toHaveBeenCalledWith('/workspace/chat?bot=owned-bot&session=session-owned');

    unmount();
  });

  test('好友申请 bot_not_found 失败时保留 Bot 列表项并恢复按钮状态', async () => {
    const bot = resultBot('bot-request-not-found');
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
    jest
      .spyOn(collaborationSquareBotService, 'requestBotFriendship')
      .mockRejectedValue(new CollaborationSquareError('network', '目标 Bot 当前不可用，申请未提交，请稍后重试'));
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(useCollaborationSquareStore.getState().bots.map((item) => item.id)).toEqual([bot.id]);
    expect(useCollaborationSquareStore.getState().busyKeys).toEqual([]);

    unmount();
  });

  test('好友申请直接返回 friend 时继续创建真实会话后导航', async () => {
    const bot = resultBot('bot-direct-friend');
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
    jest.spyOn(collaborationSquareBotService, 'requestBotFriendship').mockResolvedValue({ status: 'friend' });
    const openConversation = jest
      .spyOn(collaborationSquareBotService, 'openBotConversation')
      .mockResolvedValue({ sessionId: 'session-direct' });
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(openConversation).toHaveBeenCalledWith(bot.id, humanContext);
    expect(history.push).toHaveBeenCalledWith('/workspace/chat?bot=bot-direct-friend&session=session-direct');

    unmount();
  });

  test('Bot 工作身份发起 Bot-Bot 好友申请，建立关系后不调用用户私聊', async () => {
    const botIdentity = { id: 'bot-viewer', kind: 'bot' as const, displayName: '当前 Bot', online: true };
    useWorkspaceStore
      .getState()
      .setIdentities(
        [{ id: 'human_900003', kind: 'user', displayName: '当前用户', online: true }, botIdentity],
        botIdentity.id,
      );
    // 发现菜单已与全局身份脱钩：Bot viewer 由模块级身份选择设定（2026-09-23-discovery-tab-identity）
    useSquareIdentityStore.getState().selectIdentity(botIdentity.id);
    const bot = resultBot('bot-target');
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
    const requestFriendship = jest
      .spyOn(collaborationSquareBotService, 'requestBotFriendship')
      .mockResolvedValue({ status: 'friend' });
    const openConversation = jest.spyOn(collaborationSquareBotService, 'openBotConversation');
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(requestFriendship).toHaveBeenCalledWith(bot.id, { actorId: 'bot-viewer', userId: '900003' }, undefined, {
      type: 'bot',
      id: 'bot-viewer',
    });
    expect(openConversation).not.toHaveBeenCalled();
    expect(history.push).not.toHaveBeenCalled();
    expect(useCollaborationSquareStore.getState().bots[0].relationshipStatus).toBe('friend');
    expect(mockedNotifySuccess).toHaveBeenCalledWith('好友关系已建立');
    unmount();
  });

  test('Bot 工作身份已是好友或目标为自身时，不调用用户私聊或好友申请', async () => {
    const botIdentity = { id: 'bot-viewer', kind: 'bot' as const, displayName: '当前 Bot', online: true };
    useWorkspaceStore
      .getState()
      .setIdentities(
        [{ id: 'human_900003', kind: 'user', displayName: '当前用户', online: true }, botIdentity],
        botIdentity.id,
      );
    // 发现菜单已与全局身份脱钩：Bot viewer 由模块级身份选择设定（2026-09-23-discovery-tab-identity）
    useSquareIdentityStore.getState().selectIdentity(botIdentity.id);
    const friend = { ...resultBot('bot-friend'), relationshipStatus: 'friend' as const };
    const self = resultBot('bot-viewer');
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([friend, self]));
    const requestFriendship = jest.spyOn(collaborationSquareBotService, 'requestBotFriendship');
    const openConversation = jest.spyOn(collaborationSquareBotService, 'openBotConversation');
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    act(() => {
      result.current.primaryBotAction(friend);
      result.current.primaryBotAction(self);
    });

    expect(requestFriendship).not.toHaveBeenCalled();
    expect(openConversation).not.toHaveBeenCalled();
    expect(history.push).not.toHaveBeenCalled();
    unmount();
  });

  test('复合 Bot ID 创建会话失效时移除完整目标而不是截断 ID', async () => {
    const bot = { ...resultBot('bot-invalid:2088'), relationshipStatus: 'friend' as const };
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
    jest
      .spyOn(collaborationSquareBotService, 'openBotConversation')
      .mockRejectedValue(new CollaborationSquareError('target_invalid', '内容已取消公开或对当前账号不可见'));
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(useCollaborationSquareStore.getState().bots).toEqual([]);

    unmount();
  });
});

describe('useCollaborationSquare Task Plaza', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    (history.push as jest.Mock).mockClear();
    useCollaborationSquareStore.getState().reset();
    useSquareIdentityStore.getState().reset();
    useWorkspaceStore.getState().reset();
    useWorkspaceStore
      .getState()
      .setIdentities([{ id: 'human_900003', kind: 'user', displayName: '当前用户', online: true }], 'human_900003');
    mockedUseHumanIdentity.mockReturnValue({
      identity: { userId: '900003', displayName: '当前用户', online: true },
      status: 'ready',
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
    jest.useRealTimers();
  });

  test('resource=task 首屏调用 listPublicTasks 并回填 store.tasks', async () => {
    const listTasks = jest
      .spyOn(collaborationSquareTaskService, 'listPublicTasks')
      .mockResolvedValue({ items: [resultTask('task-1'), resultTask('task-2')], total: 2 });
    const { result, unmount } = renderHook(() => useCollaborationSquare('task'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listTasks).toHaveBeenLastCalledWith({ offset: 0, limit: 24 }, expect.any(AbortSignal));
    expect(result.current.tasks.map((task) => task.id)).toEqual(['task-1', 'task-2']);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.loading).toBe(false);

    unmount();
  });

  test('setTaskStatusFilter 透传 status，空关键词时立即重载', async () => {
    const listTasks = jest
      .spyOn(collaborationSquareTaskService, 'listPublicTasks')
      .mockResolvedValue({ items: [resultTask('task-2')], total: 1 });
    const { result, unmount } = renderHook(() => useCollaborationSquare('task'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listTasks).toHaveBeenLastCalledWith({ offset: 0, limit: 24 }, expect.any(AbortSignal));

    act(() => {
      result.current.setTaskStatusFilter('claimed');
    });
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listTasks).toHaveBeenLastCalledWith({ status: 'claimed', offset: 0, limit: 24 }, expect.any(AbortSignal));

    unmount();
  });

  test('setTaskQuery 按 1 秒防抖透传 search（命中 name/goal 由 adapter 完成）', async () => {
    const listTasks = jest
      .spyOn(collaborationSquareTaskService, 'listPublicTasks')
      .mockResolvedValue({ items: [resultTask('task-1')], total: 1 });
    const { result, unmount } = renderHook(() => useCollaborationSquare('task'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listTasks).toHaveBeenCalledTimes(1);

    act(() => {
      result.current.setTaskQuery(' 路 线图 ');
    });
    await act(async () => {
      jest.advanceTimersByTime(999);
    });
    expect(listTasks).toHaveBeenCalledTimes(1);
    await act(async () => {
      jest.advanceTimersByTime(1);
      await Promise.resolve();
    });
    expect(listTasks).toHaveBeenLastCalledWith({ search: '路 线图', offset: 0, limit: 24 }, expect.any(AbortSignal));

    unmount();
  });

  test('openTaskDetail 直接用列表项填充 taskDetail，不触发 getPublicTask', async () => {
    const task = resultTask('task-1');
    jest.spyOn(collaborationSquareTaskService, 'listPublicTasks').mockResolvedValue({ items: [task], total: 1 });
    // 详情改用已加载列表项（内存），不再发 getPublicTask 请求。
    const getTask = jest.spyOn(collaborationSquareTaskService, 'getPublicTask');
    const { result, unmount } = renderHook(() => useCollaborationSquare('task'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    act(() => {
      result.current.openTaskDetail(task);
    });
    expect(getTask).not.toHaveBeenCalled();
    expect(result.current.selectedTaskId).toBe('task-1');
    expect(result.current.taskDetail?.id).toBe('task-1');
    // 详情来自内存，无异步加载态。
    expect(result.current.detailLoading).toBe(false);
    // 任务列表不受详情打开影响。
    expect(result.current.tasks.map((item) => item.id)).toEqual(['task-1']);

    unmount();
  });

  test('closeTaskDetail 清理 selectedTaskId 与 taskDetail', async () => {
    const task = resultTask('task-1');
    jest.spyOn(collaborationSquareTaskService, 'listPublicTasks').mockResolvedValue({ items: [task], total: 1 });
    const { result, unmount } = renderHook(() => useCollaborationSquare('task'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    act(() => {
      result.current.openTaskDetail(task);
    });
    expect(result.current.selectedTaskId).toBe('task-1');
    act(() => {
      result.current.closeTaskDetail();
    });
    expect(result.current.selectedTaskId).toBeNull();
    expect(result.current.taskDetail).toBeNull();

    unmount();
  });

  test('listPublicTasks 失败时 surfaced 为 error 字符串，不因 target_invalid 特判删除任务', async () => {
    jest
      .spyOn(collaborationSquareTaskService, 'listPublicTasks')
      .mockRejectedValue(new CollaborationSquareError('target_invalid', '整页失效'));
    const { result, unmount } = renderHook(() => useCollaborationSquare('task'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(result.current.error).toBe('整页失效');
    expect(result.current.loading).toBe(false);
    expect(result.current.tasks).toEqual([]);

    unmount();
  });

  test('reload 触发重新加载首屏列表', async () => {
    const listTasks = jest
      .spyOn(collaborationSquareTaskService, 'listPublicTasks')
      .mockResolvedValue({ items: [resultTask('task-1')], total: 1 });
    const { result, unmount } = renderHook(() => useCollaborationSquare('task'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listTasks).toHaveBeenCalledTimes(1);
    await act(async () => {
      await result.current.reload();
    });
    expect(listTasks).toHaveBeenCalledTimes(2);

    unmount();
  });
});
