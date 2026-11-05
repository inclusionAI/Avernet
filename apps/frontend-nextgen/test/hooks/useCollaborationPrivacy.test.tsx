/** @jest-environment jsdom */
import type { CollaborationPrivacyLoadScope } from '@/domain/collaborationPrivacy/loadScope';
import type { CollaborationBot, CollaborationPrivacyOverview } from '@/domain/collaborationPrivacy/types';
import { useCollaborationPrivacy } from '@/hooks/useCollaborationPrivacy';
import * as identityModule from '@/hooks/useHumanIdentity';
import * as riskController from '@/services/backendApi/collaboration/collaborationBotPublicRiskController';
import { collaborationPrivacyService } from '@/services/collaborationPrivacy';
import { workspaceService } from '@/services/workspace/workspaceService';
import { useCollaborationPrivacyStore } from '@/stores/collaborationPrivacyStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { afterAll, afterEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

const mockedUseHumanIdentity = jest.spyOn(identityModule, 'useHumanIdentity');

const overview = {
  currentUser: { displayName: '真实用户', employeeNumber: '900004', departmentPath: ['协作平台'] },
  organizationOptions: [],
  bots: [],
} as CollaborationPrivacyOverview;

const makeBot = (id: string, name: string): CollaborationBot => ({
  id,
  name,
  engine: 'OpenClaw',
  joinedBcn: true,
  collaborationStatus: 'online',
  profilePublic: true,
  taskClaimingEnabled: false,
  dreamModelEnabled: false,
  publication: {
    user: { scope: 'all', organizationPaths: [] },
    bot: { scope: 'none', organizationPaths: [] },
  },
  pendingPublications: {},
  friendApproval: { mode: 'all', exemptOrganizationPaths: [] },
});

const overviewWithBots: CollaborationPrivacyOverview = {
  ...overview,
  bots: [makeBot('bot-1', 'Bot A'), makeBot('bot-2', 'Bot B')],
};

afterEach(() => {
  jest.clearAllMocks();
  useCollaborationPrivacyStore.getState().reset();
  useWorkspaceStore.getState().reset();
});

afterAll(() => {
  jest.restoreAllMocks();
});

describe('useCollaborationPrivacy identity wiring', () => {
  it('waits for identity and passes the employee number to the overview service', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
    const mockedLoadOverview = jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overview);

    renderHook(() => useCollaborationPrivacy());

    await waitFor(() =>
      expect(mockedLoadOverview).toHaveBeenCalledWith('900004', expect.any(AbortSignal), { target: 'currentUser' }),
    );
  });

  it('does not call the overview service while identity is loading', () => {
    mockedUseHumanIdentity.mockReturnValue({ status: 'loading', identity: null });

    renderHook(() => useCollaborationPrivacy());

    expect(collaborationPrivacyService.loadOverview).not.toHaveBeenCalled();
    expect(useCollaborationPrivacyStore.getState().loading).toBe(true);
  });

  it('用户身份只显示用户内容，不显示 Bot 管理卡片', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'human-1',
      identities: [{ id: 'human-1', kind: 'user', displayName: '真实用户', online: true }],
    });
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBots);

    const { result } = renderHook(() => useCollaborationPrivacy());

    await waitFor(() => expect(result.current.showIdentityCard).toBe(true));
    expect(result.current.visibleBots).toEqual([]);
  });

  it('Bot 身份只显示当前 Bot 的管理卡片', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:900004',
      identities: [{ id: 'bot-1:900004', kind: 'bot', displayName: 'Bot A', online: true }],
    });
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBots);

    const { result } = renderHook(() => useCollaborationPrivacy());

    await waitFor(() => expect(result.current.visibleBots).toHaveLength(1));
    expect(result.current.showIdentityCard).toBe(false);
    expect(result.current.visibleBots[0].id).toBe('bot-1');
  });

  it('Bot 身份只按选中 Bot 请求，不携带全量范围', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:900004',
      identities: [{ id: 'bot-1:900004', kind: 'bot', displayName: 'Bot A', online: true }],
    });
    const mockedLoadOverview = jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overview);

    renderHook(() => useCollaborationPrivacy());

    await waitFor(() =>
      expect(mockedLoadOverview).toHaveBeenCalledWith('900004', expect.any(AbortSignal), {
        target: 'activeBot',
        botId: 'bot-1:900004',
      }),
    );
    expect(mockedLoadOverview).toHaveBeenCalledTimes(1);
  });

  it('切换工作身份时取消上一次在途请求并按新范围重取', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:900004',
      identities: [
        { id: 'bot-1:900004', kind: 'bot', displayName: 'Bot A', online: true },
        { id: 'bot-2:900004', kind: 'bot', displayName: 'Bot B', online: true },
      ],
    });
    const mockedLoadOverview = jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overview);

    renderHook(() => useCollaborationPrivacy());
    await waitFor(() => expect(mockedLoadOverview).toHaveBeenCalledTimes(1));

    act(() => {
      workspaceService.switchIdentity('bot-2:900004');
    });
    await waitFor(() => expect(mockedLoadOverview).toHaveBeenCalledTimes(2));

    const signals = mockedLoadOverview.mock.calls.map((call: unknown[]) => call[1] as AbortSignal);
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
    expect(mockedLoadOverview.mock.calls[1][2]).toEqual({ target: 'activeBot', botId: 'bot-2:900004' });
  });

  it('切换身份后仅最新请求可以更新页面状态', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:900004',
      identities: [
        { id: 'bot-1:900004', kind: 'bot', displayName: 'Bot A', online: true },
        { id: 'bot-2:900004', kind: 'bot', displayName: 'Bot B', online: true },
      ],
    });
    let resolveFirst!: (value: CollaborationPrivacyOverview) => void;
    let resolveSecond!: (value: CollaborationPrivacyOverview) => void;
    const firstOverview = { ...overview, bots: [makeBot('bot-1', 'Bot A')] };
    const secondOverview = { ...overview, bots: [makeBot('bot-2', 'Bot B')] };
    const mockedLoadOverview = jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
      )
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveSecond = resolve;
        }),
      );

    const { result } = renderHook(() => useCollaborationPrivacy());
    await waitFor(() => expect(mockedLoadOverview).toHaveBeenCalledTimes(1));
    act(() => {
      workspaceService.switchIdentity('bot-2:900004');
    });
    await waitFor(() => expect(mockedLoadOverview).toHaveBeenCalledTimes(2));

    await act(async () => {
      resolveFirst(firstOverview);
      await Promise.resolve();
    });
    expect(result.current.loading).toBe(true);
    expect(result.current.overview).toBeNull();

    await act(async () => {
      resolveSecond(secondOverview);
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.visibleBots.map((bot) => bot.id)).toEqual(['bot-2']));
    expect(result.current.loading).toBe(false);
  });

  it('守护：生产入口任何身份下都不会回落到遗留全量 hydrate', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:900004',
      identities: [
        { id: 'human-1', kind: 'user', displayName: '真实用户', online: true },
        { id: 'bot-1:900004', kind: 'bot', displayName: 'Bot A', online: true },
      ],
    });
    const mockedLoadOverview = jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overview);

    const { unmount } = renderHook(() => useCollaborationPrivacy());
    await waitFor(() => expect(mockedLoadOverview).toHaveBeenCalledTimes(1));
    act(() => {
      workspaceService.switchIdentity('human-1');
    });
    await waitFor(() => expect(mockedLoadOverview).toHaveBeenCalledTimes(2));
    unmount();

    // spy 的 mock.calls 在类型上退化为 any，显式声明数组类型以获得 target 判别保护。
    const loadScopes: Array<CollaborationPrivacyLoadScope | undefined> = mockedLoadOverview.mock.calls.map(
      (call: unknown[]) => call[2] as CollaborationPrivacyLoadScope | undefined,
    );
    expect(loadScopes).toHaveLength(2);
    expect(loadScopes.some((loadScope) => loadScope?.target === 'allBots')).toBe(false);
  });

  it('同一页面内切换 Human 与 Bot 身份时更新内容并关闭旧身份编辑态', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'human-1',
      identities: [
        { id: 'human-1', kind: 'user', displayName: '真实用户', online: true },
        { id: 'bot-1:900004', kind: 'bot', displayName: 'Bot A', online: true },
      ],
    });
    const mockedLoadOverview = jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(overviewWithBots);

    const { result } = renderHook(() => useCollaborationPrivacy());

    await waitFor(() => expect(result.current.showIdentityCard).toBe(true));
    expect(result.current.visibleBots).toEqual([]);

    act(() => {
      workspaceService.switchIdentity('bot-1:900004');
    });
    await waitFor(() => expect(result.current.visibleBots.map((bot) => bot.id)).toEqual(['bot-1']));
    expect(result.current.showIdentityCard).toBe(false);

    act(() => {
      result.current.toggleDirect(overviewWithBots.bots[0], 'profilePublic', false);
      result.current.openPublicationEditor(overviewWithBots.bots[0], 'user');
      result.current.openScopeViewer(overviewWithBots.bots[0], 'bot');
      result.current.openFriendEditor(overviewWithBots.bots[0]);
    });
    expect(result.current.confirmation).not.toBeNull();
    expect(result.current.publicationEditor).not.toBeNull();
    expect(result.current.scopeViewer).not.toBeNull();
    expect(result.current.friendEditorBot).not.toBeNull();

    act(() => {
      workspaceService.switchIdentity('human-1');
    });
    await waitFor(() => expect(result.current.showIdentityCard).toBe(true));
    expect(result.current.visibleBots).toEqual([]);
    expect(result.current.confirmation).toBeNull();
    expect(result.current.publicationEditor).toBeNull();
    expect(result.current.scopeViewer).toBeNull();
    expect(result.current.friendEditorBot).toBeUndefined();
    expect(mockedLoadOverview).toHaveBeenCalledTimes(3);
    const scopes: Array<CollaborationPrivacyLoadScope | undefined> = mockedLoadOverview.mock.calls.map(
      (call: unknown[]) => call[2] as CollaborationPrivacyLoadScope | undefined,
    );
    expect(scopes).toEqual([
      { target: 'currentUser' },
      { target: 'activeBot', botId: 'bot-1:900004' },
      { target: 'currentUser' },
    ]);
  });
});

describe('useCollaborationPrivacy fixedBotId（BotWorkshop 通用配置弹窗形态）', () => {
  it('工作身份未就绪时仍按指定 Bot 以 activeBot scope 加载，并直接命中该 Bot', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
    const mockedLoadOverview = jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(overviewWithBots);

    const { result } = renderHook(() => useCollaborationPrivacy({ fixedBotId: 'bot-2' }));

    await waitFor(() =>
      expect(mockedLoadOverview).toHaveBeenCalledWith('900004', expect.any(AbortSignal), {
        target: 'activeBot',
        botId: 'bot-2',
      }),
    );
    await waitFor(() => expect(result.current.visibleBots.map((bot) => bot.id)).toEqual(['bot-2']));
    expect(result.current.showIdentityCard).toBe(false);
  });

  it('指定 Bot 未命中时 visibleBots 为空（弹窗分区降级数据源）', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBots);

    const { result } = renderHook(() => useCollaborationPrivacy({ fixedBotId: 'bot-missing' }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeNull();
    expect(result.current.visibleBots).toEqual([]);
  });

  it('外部 Bot 弹窗按 external scope 加载，并把 isExternalBot 透传到提交', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
    const mockedLoadOverview = jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(overviewWithBots);

    const { result } = renderHook(() => useCollaborationPrivacy({ fixedBotId: 'bot-2', isExternalBot: true }));

    await waitFor(() =>
      expect(mockedLoadOverview).toHaveBeenCalledWith('900004', expect.any(AbortSignal), {
        target: 'activeBot',
        botId: 'bot-2',
        external: true,
      }),
    );

    const mockedSubmitPublication = jest
      .spyOn(collaborationPrivacyService, 'submitPublication')
      .mockResolvedValue(overviewWithBots.bots[1]);
    act(() => {
      result.current.openPublicationEditor(overviewWithBots.bots[1], 'bot');
    });
    await act(async () => {
      await result.current.submitPublication({ scope: 'all', organizationPaths: [] }, [
        { deptNo: 'A4195', deptName: '蚂蚁集团-CRO线-大安全技术部' },
      ]);
    });

    expect(mockedSubmitPublication).toHaveBeenCalledWith({
      botId: 'bot-2',
      audience: 'bot',
      config: { scope: 'all', organizationPaths: [] },
      deptEntries: [{ deptNo: 'A4195', deptName: '蚂蚁集团-CRO线-大安全技术部' }],
      isExternalBot: true,
    });
    await waitFor(() => expect(result.current.publicationEditor).toBeNull());
  });
});

describe('useCollaborationPrivacy 在途读取与共享 Store 的过期隔离', () => {
  const readyIdentity = () =>
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
  const overviewA: CollaborationPrivacyOverview = { ...overview, bots: [makeBot('bot-a', 'Bot A')] };
  const overviewB: CollaborationPrivacyOverview = { ...overview, bots: [makeBot('bot-b', 'Bot B')] };

  // 只 mock service，真实 Hook 与真实共享 Store 参与，deferred Promise 故意忽略 abort。
  function mockDeferredLoads() {
    const deferreds: Array<{
      resolve: (value: CollaborationPrivacyOverview) => void;
      reject: (reason: unknown) => void;
    }> = [];
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          deferreds.push({ resolve, reject });
        }),
    );
    return { deferreds };
  }

  it('卸载后手动刷新迟到的成功与 finally 不改写已 reset 的共享 Store', async () => {
    readyIdentity();
    const { deferreds } = mockDeferredLoads();
    const { result, unmount } = renderHook(() => useCollaborationPrivacy({ fixedBotId: 'bot-a' }));
    await waitFor(() => expect(deferreds).toHaveLength(1));
    let manual!: Promise<void>;
    act(() => {
      manual = result.current.load();
    });
    await waitFor(() => expect(deferreds).toHaveLength(2));
    unmount();
    expect(useCollaborationPrivacyStore.getState().overview).toBeNull();
    expect(useCollaborationPrivacyStore.getState().loading).toBe(true);

    await act(async () => {
      deferreds[1].resolve(overviewA);
      await manual;
    });

    const state = useCollaborationPrivacyStore.getState();
    expect(state.overview).toBeNull();
    expect(state.error).toBeNull();
    expect(state.loading).toBe(true);
  });

  it('卸载后手动刷新迟到的失败不改写已 reset 的共享 Store', async () => {
    readyIdentity();
    const { deferreds } = mockDeferredLoads();
    const { result, unmount } = renderHook(() => useCollaborationPrivacy({ fixedBotId: 'bot-a' }));
    await waitFor(() => expect(deferreds).toHaveLength(1));
    let manual!: Promise<void>;
    act(() => {
      manual = result.current.load();
    });
    await waitFor(() => expect(deferreds).toHaveLength(2));
    unmount();

    await act(async () => {
      deferreds[1].reject(new Error('读取失败'));
      await manual;
    });

    const state = useCollaborationPrivacyStore.getState();
    expect(state.overview).toBeNull();
    expect(state.error).toBeNull();
    expect(state.loading).toBe(true);
  });

  it('切换固定 Bot 后旧 Bot 的迟到成功不覆盖新 Bot 的 overview', async () => {
    readyIdentity();
    const { deferreds } = mockDeferredLoads();
    const { result, rerender } = renderHook(
      ({ botId }: { botId: string }) => useCollaborationPrivacy({ fixedBotId: botId }),
      { initialProps: { botId: 'bot-a' } },
    );
    await waitFor(() => expect(deferreds).toHaveLength(1));
    rerender({ botId: 'bot-b' });
    await waitFor(() => expect(deferreds).toHaveLength(2));

    await act(async () => {
      deferreds[1].resolve(overviewB);
    });
    await waitFor(() => expect(result.current.visibleBots.map((bot) => bot.id)).toEqual(['bot-b']));
    expect(result.current.loading).toBe(false);

    await act(async () => {
      deferreds[0].resolve(overviewA);
      await Promise.resolve();
    });

    expect(result.current.overview?.bots.map((bot) => bot.id)).toEqual(['bot-b']);
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it('切换固定 Bot 后旧 Bot 的迟到失败不污染新 Bot 的 error/loading', async () => {
    readyIdentity();
    const { deferreds } = mockDeferredLoads();
    const { result, rerender } = renderHook(
      ({ botId }: { botId: string }) => useCollaborationPrivacy({ fixedBotId: botId }),
      { initialProps: { botId: 'bot-a' } },
    );
    await waitFor(() => expect(deferreds).toHaveLength(1));
    rerender({ botId: 'bot-b' });
    await waitFor(() => expect(deferreds).toHaveLength(2));

    await act(async () => {
      deferreds[1].resolve(overviewB);
    });
    await waitFor(() => expect(result.current.visibleBots.map((bot) => bot.id)).toEqual(['bot-b']));

    await act(async () => {
      deferreds[0].reject(new Error('旧 Bot 读取失败'));
      await Promise.resolve();
    });

    expect(result.current.overview?.bots.map((bot) => bot.id)).toEqual(['bot-b']);
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });
});

describe('useCollaborationPrivacy 外部 Bot 不查询公开风险', () => {
  it.each([true, false])('通用配置打开、重载及编辑对 Bot 可见性（isExternalBot=%s）', async (isExternalBot) => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBots);
    const currentRisk = jest
      .spyOn(riskController, 'getCurrentBotPublicRiskCheck')
      .mockResolvedValue({ code: 20000, data: null });
    const { result } = renderHook(() => useCollaborationPrivacy({ fixedBotId: 'bot-2', isExternalBot }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(result.current.visibleBots).toHaveLength(1));
    act(() => result.current.openPublicationEditor(overviewWithBots.bots[1], 'bot'));
    await act(async () => {
      await result.current.load();
    });

    const expectedCalls = isExternalBot ? [] : [['bot-2', '900004', { public_scope: 'user' }]];
    expect(currentRisk.mock.calls).toEqual(expectedCalls);
  });
});

describe('静默刷新（silent 选项，修复后自动刷新不闪整面板）', () => {
  const readyHuman = () =>
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '900004', displayName: '真实用户', online: true },
    });

  it('silent 刷新不再进入 loading，成功后照常更新 overview', async () => {
    readyHuman();
    let finish!: (value: typeof overviewWithBots) => void;
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValueOnce(overviewWithBots)
      .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const { result } = renderHook(() => useCollaborationPrivacy());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      void result.current.load(undefined, { silent: true });
    });
    expect(result.current.loading).toBe(false);

    await act(async () => {
      finish(overviewWithBots);
    });
    expect(result.current.loading).toBe(false);
    expect(result.current.overview).toEqual(overviewWithBots);
  });

  it('silent 刷新失败时写 error，但不翻转 loading', async () => {
    readyHuman();
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValueOnce(overviewWithBots)
      .mockRejectedValueOnce(new Error('网络异常'));
    const { result } = renderHook(() => useCollaborationPrivacy());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeNull();

    await act(async () => {
      await result.current.load(undefined, { silent: true });
    });
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeTruthy();
    expect(result.current.overview).toEqual(overviewWithBots);
  });

  it('silent 读取同样按 loadId 失效：卸载后迟到响应不写共享 Store', async () => {
    readyHuman();
    let late!: (value: typeof overviewWithBots) => void;
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValueOnce(overviewWithBots)
      .mockImplementationOnce(() => new Promise((resolve) => { late = resolve; }));
    const { result, unmount } = renderHook(() => useCollaborationPrivacy());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      void result.current.load(undefined, { silent: true });
    });
    unmount();
    const storeAfterReset = useCollaborationPrivacyStore.getState();

    await act(async () => {
      late({ ...overviewWithBots, bots: [] as never });
    });
    const state = useCollaborationPrivacyStore.getState();
    expect(state.overview).toEqual(storeAfterReset.overview);
    expect(state.error).toEqual(storeAfterReset.error);
    expect(state.loading).toEqual(storeAfterReset.loading);
  });
});
