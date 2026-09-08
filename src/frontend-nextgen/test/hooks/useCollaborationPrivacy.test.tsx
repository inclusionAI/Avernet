/** @jest-environment jsdom */
import type { CollaborationPrivacyLoadScope } from '@/domain/collaborationPrivacy/loadScope';
import type { CollaborationBot, CollaborationPrivacyOverview } from '@/domain/collaborationPrivacy/types';
import { useCollaborationPrivacy } from '@/hooks/useCollaborationPrivacy';
import * as identityModule from '@/hooks/useHumanIdentity';
import { collaborationPrivacyService } from '@/services/collaborationPrivacy';
import { workspaceService } from '@/services/workspace/workspaceService';
import { useCollaborationPrivacyStore } from '@/stores/collaborationPrivacyStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { afterAll, afterEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

const mockedUseHumanIdentity = jest.spyOn(identityModule, 'useHumanIdentity');

const overview = {
  currentUser: { displayName: '真实用户', employeeNumber: '447147', departmentPath: ['协作平台'] },
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
      identity: { userId: '447147', displayName: '真实用户', online: true },
    });
    const mockedLoadOverview = jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overview);

    renderHook(() => useCollaborationPrivacy());

    await waitFor(() =>
      expect(mockedLoadOverview).toHaveBeenCalledWith('447147', expect.any(AbortSignal), { target: 'currentUser' }),
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
      identity: { userId: '447147', displayName: '真实用户', online: true },
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
      identity: { userId: '447147', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:447147',
      identities: [{ id: 'bot-1:447147', kind: 'bot', displayName: 'Bot A', online: true }],
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
      identity: { userId: '447147', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:447147',
      identities: [{ id: 'bot-1:447147', kind: 'bot', displayName: 'Bot A', online: true }],
    });
    const mockedLoadOverview = jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overview);

    renderHook(() => useCollaborationPrivacy());

    await waitFor(() =>
      expect(mockedLoadOverview).toHaveBeenCalledWith('447147', expect.any(AbortSignal), {
        target: 'activeBot',
        botId: 'bot-1:447147',
      }),
    );
    expect(mockedLoadOverview).toHaveBeenCalledTimes(1);
  });

  it('切换工作身份时取消上一次在途请求并按新范围重取', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '447147', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:447147',
      identities: [
        { id: 'bot-1:447147', kind: 'bot', displayName: 'Bot A', online: true },
        { id: 'bot-2:447147', kind: 'bot', displayName: 'Bot B', online: true },
      ],
    });
    const mockedLoadOverview = jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overview);

    renderHook(() => useCollaborationPrivacy());
    await waitFor(() => expect(mockedLoadOverview).toHaveBeenCalledTimes(1));

    act(() => {
      workspaceService.switchIdentity('bot-2:447147');
    });
    await waitFor(() => expect(mockedLoadOverview).toHaveBeenCalledTimes(2));

    const signals = mockedLoadOverview.mock.calls.map((call: unknown[]) => call[1] as AbortSignal);
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
    expect(mockedLoadOverview.mock.calls[1][2]).toEqual({ target: 'activeBot', botId: 'bot-2:447147' });
  });

  it('切换身份后仅最新请求可以更新页面状态', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '447147', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:447147',
      identities: [
        { id: 'bot-1:447147', kind: 'bot', displayName: 'Bot A', online: true },
        { id: 'bot-2:447147', kind: 'bot', displayName: 'Bot B', online: true },
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
      workspaceService.switchIdentity('bot-2:447147');
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
      identity: { userId: '447147', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:447147',
      identities: [
        { id: 'human-1', kind: 'user', displayName: '真实用户', online: true },
        { id: 'bot-1:447147', kind: 'bot', displayName: 'Bot A', online: true },
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
      identity: { userId: '447147', displayName: '真实用户', online: true },
    });
    useWorkspaceStore.setState({
      activeIdentityId: 'human-1',
      identities: [
        { id: 'human-1', kind: 'user', displayName: '真实用户', online: true },
        { id: 'bot-1:447147', kind: 'bot', displayName: 'Bot A', online: true },
      ],
    });
    const mockedLoadOverview = jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(overviewWithBots);

    const { result } = renderHook(() => useCollaborationPrivacy());

    await waitFor(() => expect(result.current.showIdentityCard).toBe(true));
    expect(result.current.visibleBots).toEqual([]);

    act(() => {
      workspaceService.switchIdentity('bot-1:447147');
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
      { target: 'activeBot', botId: 'bot-1:447147' },
      { target: 'currentUser' },
    ]);
  });
});
