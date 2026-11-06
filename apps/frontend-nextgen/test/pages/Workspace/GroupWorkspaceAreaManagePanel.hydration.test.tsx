/**
 * @jest-environment jsdom
 *
 * 管理面板打开期间列表刷新后的完整群详情生命周期（真实 useGroupWorkspace + 真实 Store，
 * 仅 mock service 与无关子组件）：
 * - 新列表摘要即时生效，完整管理详情按需重拉后恢复 role/mode/owner/群配置；
 * - 显式空详情标记为已加载，不无限重拉；
 * - 面板关闭时新列表不触发任何详情补拉（头像预算 AC-02）。
 */
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { GroupView } from '@/domain/collaboration';
import { groupService } from '@/services/workspace/groupService';
import { useWorkspaceStore } from '@/stores/workspaceStore';

let mockManagedGroup: GroupView | null = null;

jest.mock('@/services/workspace/groupService');
jest.mock('sonner', () => ({ toast: { info: jest.fn(), error: jest.fn(), success: jest.fn() } }));
jest.mock('@/services/workspace/sessionService', () => ({
  sessionService: { getSessionDetail: jest.fn() },
}));
jest.mock('@/pages/Workspace/hooks/useGroupSessions', () => ({
  useGroupSessions: () => ({
    sessionsByGroupId: {},
    hasMoreSessionsByGroupId: {},
    totalSessionsByGroupId: {},
    isLoadingMoreSessionsByGroupId: {},
    loadMoreSessions: jest.fn(),
    errorByGroupId: {},
    loadMoreErrorByGroupId: {},
    reloadGroup: jest.fn(),
    selectedSession: null,
    selectedSessionId: null,
    openSession: jest.fn(),
    createSessionIn: jest.fn(),
    leaveSession: jest.fn(),
    updateMemberMode: jest.fn(),
    applySessionUpdate: jest.fn(),
    favoriteSessionIds: [],
    sessionSearchText: '',
    setSessionSearchText: jest.fn(),
    toggleFavorite: jest.fn(),
    renameSession: jest.fn(),
    deleteSession: jest.fn(),
  }),
}));
jest.mock('@/pages/Workspace/hooks/useGroupChat', () => ({
  useGroupChat: () => ({ chat: { messages: [] }, panelRef: { current: null } }),
}));
jest.mock('@/pages/Workspace/hooks/useGroupManagement', () => ({
  useGroupManagement: () => ({ createShare: jest.fn() }),
}));
jest.mock('@/pages/Workspace/hooks/useSessionManagement', () => ({
  useSessionManagement: () => ({ createShare: jest.fn() }),
}));
jest.mock('@/pages/Workspace/hooks/useGroupCreateDialog', () => ({
  useGroupCreateDialog: () => ({
    open: false,
    openModal: jest.fn(),
    closeModal: jest.fn(),
    handleCreated: jest.fn(),
  }),
}));
jest.mock('@/pages/Workspace/hooks/useOpenDefaultGroupSession', () => ({
  useOpenDefaultGroupSession: () => jest.fn(),
}));

jest.mock('@/pages/Workspace/components/GroupChatPane', () => ({
  GroupChatPane: ({ onTogglePanel }: { onTogglePanel: (panel: string) => void }) => (
    <div data-testid="group-chat-pane">
      <button type="button" onClick={() => onTogglePanel('manage')}>打开管理面板</button>
      <button type="button" onClick={() => onTogglePanel('none')}>关闭面板</button>
    </div>
  ),
}));
jest.mock('@/pages/Workspace/components/WorkspaceManagePanels', () => ({
  WorkspaceManagePanels: ({ group }: { group: GroupView | null }) => {
    mockManagedGroup = group;
    return <div data-testid="manage-panel" />;
  },
}));
jest.mock('@/pages/Workspace/components/GroupMembersPanelSlot', () => ({
  GroupMembersPanelSlot: () => null,
}));
jest.mock('@/pages/Workspace/components/GroupWorkspaceSidebar', () => ({
  GroupWorkspaceSidebar: () => null,
}));
jest.mock('@/pages/Workspace/components/GroupChatPane/SessionFilesSidebar', () => ({
  SessionFilesSidebar: () => null,
}));
jest.mock('@/pages/Workspace/components/GroupChatPane/GroupHistorySidebar', () => ({
  GroupHistorySidebar: () => null,
}));
jest.mock('@/pages/Workspace/components/Modals/CreateGroupModal', () => ({
  CreateGroupModal: () => null,
}));

const { GroupWorkspaceArea } =
  require('@/pages/Workspace/GroupWorkspaceArea') as typeof import('@/pages/Workspace/GroupWorkspaceArea');

const gs = groupService as unknown as Record<string, jest.Mock<any>>;
const botIdentity = { id: 'bot-1', kind: 'bot' as const, displayName: 'B1', online: true };

const base = {
  groupId: 'g1',
  name: '主站群',
  kind: 'free_chat' as const,
  status: 'active' as const,
  sessions: [],
  lastMessageAt: 1,
  createdAt: 1,
  isPublic: false,
  deliveryPolicy: 'send_to_driver' as const,
};

const summaryMembers = [
  { actorId: 'b2', kind: 'bot' as const, name: '摘要成员', avatarUrl: '/summary.png' },
];

const fullDetail: GroupView = {
  ...base,
  participantCount: 2,
  participants: [
    { actorId: 'b1', kind: 'bot', name: 'OwnerBot', role: 'owner', mode: 'auto', avatarUrl: '/owner.png' },
    { actorId: 'b2', kind: 'bot', name: '摘要成员', role: 'worker', mode: 'muted', avatarUrl: '/summary.png' },
  ],
  participantSummaries: summaryMembers,
  participantDetailsLoaded: true,
  ownerUserId: 'u-owner',
  driverBotUuid: 'b1',
  deliveryPolicy: 'send_to_driver',
};

function newSummaryList(members: typeof summaryMembers): { ok: true; data: GroupView[] } {
  return {
    ok: true,
    data: [
      {
        ...base,
        participantCount: members.length,
        participants: [],
        participantDetailsLoaded: false,
        participantSummaries: members,
      },
    ],
  };
}

function renderArea() {
  return render(<GroupWorkspaceArea mobileListOpen={false} onCloseMobileList={jest.fn()} />);
}

function refreshGroups() {
  act(() => {
    useWorkspaceStore.getState().setMembership('session_only');
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockManagedGroup = null;
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.setState({
    activeIdentityId: 'bot-1',
    identities: [botIdentity],
    selectedGroupId: 'g1',
    membership: 'direct',
  });
  gs.getVisibleGroups.mockImplementation((xs: GroupView[]) => xs);
  gs.canManageGroup.mockReturnValue({ allowed: true });
  gs.canDissolveGroup.mockReturnValue({ allowed: false });
  gs.dissolveGroup.mockResolvedValue({ ok: true, data: null });
});

describe('GroupWorkspaceArea 管理面板与列表刷新的详情生命周期', () => {
  it('摘要即时生效，管理详情按需重拉并恢复 role/mode/owner 与群配置', async () => {
    let finishDetail!: (res: { ok: true; data: GroupView }) => void;
    gs.loadGroupDetailOrBcs
      .mockResolvedValueOnce({ ok: true, data: fullDetail })
      .mockImplementationOnce(() => new Promise((resolve) => { finishDetail = resolve; }));
    gs.loadGroups.mockResolvedValueOnce(newSummaryList(summaryMembers));

    renderArea();
    await waitFor(() => expect(mockManagedGroup?.participantSummaries).toEqual(summaryMembers));
    fireEvent.click(screen.getByRole('button', { name: '打开管理面板' }));

    // 打开面板：按需拉取完整详情（role/mode 等管理字段仅完整详情才有）。
    await waitFor(() => expect(gs.loadGroupDetailOrBcs).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockManagedGroup?.participants).toHaveLength(2));
    expect(mockManagedGroup?.participantDetailsLoaded).toBe(true);

    // 刷新含新摘要的列表：同群被新列表对象替换，摘要立即生效，头像仍为空。
    const nextSummary = [
      { actorId: 'b3', kind: 'bot' as const, name: '新摘要成员', avatarUrl: '/next.png' },
    ];
    gs.loadGroups.mockResolvedValueOnce(newSummaryList(nextSummary));
    refreshGroups();
    await waitFor(() => expect(gs.loadGroups).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockManagedGroup?.participantSummaries).toEqual(nextSummary));
    expect(mockManagedGroup?.participants).toEqual([]);
    // 新列表对象详情未加载 → 已打开的管理面板按需重拉（不是逐群头像补拉）。
    await waitFor(() => expect(gs.loadGroupDetailOrBcs).toHaveBeenCalledTimes(2));

    // 详情返回后恢复完整成员（role/mode）、owner 与群配置。
    const refreshedDetail: GroupView = {
      ...fullDetail,
      participants: [
        { actorId: 'b3', kind: 'bot', name: '新摘要成员', role: 'manager', mode: 'present', avatarUrl: '/next.png' },
      ],
      participantSummaries: nextSummary,
      ownerUserId: 'u-next',
      deliveryPolicy: 'inject_observers',
    };
    await act(async () => {
      finishDetail({ ok: true, data: refreshedDetail });
    });
    await waitFor(() => expect(mockManagedGroup?.participantDetailsLoaded).toBe(true));
    expect(mockManagedGroup?.participants[0]).toMatchObject({ role: 'manager', mode: 'present' });
    expect(mockManagedGroup?.ownerUserId).toBe('u-next');
    expect(mockManagedGroup?.deliveryPolicy).toBe('inject_observers');
  });

  it('显式空详情标记为已加载，不无限重拉', async () => {
    gs.loadGroups.mockResolvedValueOnce(newSummaryList(summaryMembers));
    gs.loadGroupDetailOrBcs.mockResolvedValueOnce({
      ok: true,
      data: {
        ...base,
        participantCount: 0,
        participants: [],
        participantSummaries: [],
        participantDetailsLoaded: true,
      },
    });

    renderArea();
    await waitFor(() => expect(mockManagedGroup?.participantSummaries).toEqual(summaryMembers));
    fireEvent.click(screen.getByRole('button', { name: '打开管理面板' }));

    await waitFor(() => expect(gs.loadGroupDetailOrBcs).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockManagedGroup?.participantDetailsLoaded).toBe(true));
    // 显式空详情（true）不再触发下一轮请求。
    await act(async () => {
      await new Promise((resolve) => { setTimeout(resolve, 50); });
    });
    expect(gs.loadGroupDetailOrBcs).toHaveBeenCalledTimes(1);
  });

  it('管理面板关闭时刷新新摘要列表不触发详情补拉', async () => {
    gs.loadGroups.mockResolvedValueOnce(newSummaryList(summaryMembers));
    gs.loadGroupDetailOrBcs.mockResolvedValue({ ok: true, data: fullDetail });

    renderArea();
    await waitFor(() => expect(mockManagedGroup?.participantSummaries).toEqual(summaryMembers));

    const nextSummary = [
      { actorId: 'b3', kind: 'bot' as const, name: '新摘要成员', avatarUrl: '/next.png' },
    ];
    gs.loadGroups.mockResolvedValueOnce(newSummaryList(nextSummary));
    refreshGroups();
    await waitFor(() => expect(gs.loadGroups).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockManagedGroup?.participantSummaries).toEqual(nextSummary));

    expect(gs.loadGroupDetailOrBcs).not.toHaveBeenCalled();
    expect(gs.loadGroupParticipants).not.toHaveBeenCalled();
  });
});
