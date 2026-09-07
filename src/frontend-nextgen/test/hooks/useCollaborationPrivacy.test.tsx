/** @jest-environment jsdom */
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

    await waitFor(() => expect(mockedLoadOverview).toHaveBeenCalledWith('447147', expect.any(AbortSignal)));
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
    expect(mockedLoadOverview).toHaveBeenCalledTimes(1);
    expect(mockedLoadOverview).toHaveBeenCalledWith('447147', expect.any(AbortSignal));
  });
});
