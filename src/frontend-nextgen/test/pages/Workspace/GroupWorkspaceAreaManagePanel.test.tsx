/**
 * @jest-environment jsdom
 *
 * 覆盖 GroupWorkspaceArea 的群管理面板详情补拉编排：
 * 面板保持打开时切换选中群，必须为新群重新拉取群详情（reloadSelectedGroup），
 * 否则「群成员管理」card 因 participants 为空 + participantCount>0 永远显示「加载中…」。
 */
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const baseGroup = {
  groupId: 'g1',
  name: '主站群',
  kind: 'free_chat' as const,
  status: 'active' as const,
  participants: [],
  sessions: [],
  lastMessageAt: 1,
  createdAt: 1,
  participantCount: 2,
  isPublic: false,
  deliveryPolicy: 'send_to_driver' as const,
};

// 可变 mock 状态：测试内切换 selectedGroupId 后 rerender，模拟侧栏切群。
const mockState: { selectedGroupId: string | null } = { selectedGroupId: 'g1' };
const mockReloadSelectedGroup = jest.fn();
const mockOnSelectGroup = jest.fn();

const mockStore = {
  sessionTabsByGroup: {},
  setSessionTabForGroup: jest.fn(),
  selectGroup: jest.fn(),
  membership: 'direct' as const,
};

jest.mock('@/stores/workspaceStore', () => ({
  useWorkspaceStore: (selector?: (s: typeof mockStore) => unknown) => (selector ? selector(mockStore) : mockStore),
}));
jest.mock('@/services/workspace/sessionService', () => ({ sessionService: { getSessionDetail: jest.fn() } }));
jest.mock('sonner', () => ({ toast: { info: jest.fn(), error: jest.fn(), success: jest.fn() } }));

jest.mock('@/pages/Workspace/hooks/useGroupWorkspace', () => ({
  useGroupWorkspace: () => ({
    groups: [baseGroup],
    expandedGroupIds: {} as Record<string, true>,
    selectedGroupId: mockState.selectedGroupId,
    selectedGroup: baseGroup,
    canManageGroup: { allowed: true },
    isLoadingGroups: false,
    groupSearchText: '',
    setGroupSearchText: jest.fn(),
    kindFilter: 'all' as const,
    setKindFilter: jest.fn(),
    membership: 'direct' as const,
    setMembership: jest.fn(),
    sortMode: 'createdAt' as const,
    setSortMode: jest.fn(),
    toggleGroupExpanded: jest.fn(),
    onSelectGroup: mockOnSelectGroup,
    dissolveGroup: jest.fn(),
    refreshGroups: jest.fn(),
    retryGroups: jest.fn(),
    groupsError: null,
    reloadSelectedGroup: mockReloadSelectedGroup,
    activeIdentity: null,
    activeIdentityId: null,
    identities: [],
    canDissolveGroup: { allowed: false },
  }),
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
jest.mock('@/pages/Workspace/hooks/useGroupChat', () => ({ useGroupChat: () => ({}) }));
jest.mock('@/pages/Workspace/hooks/useGroupManagement', () => ({ useGroupManagement: () => ({}) }));
jest.mock('@/pages/Workspace/hooks/useSessionManagement', () => ({ useSessionManagement: () => ({}) }));
jest.mock('@/pages/Workspace/hooks/useGroupCreateDialog', () => ({
  useGroupCreateDialog: () => ({ open: false, openModal: jest.fn(), closeModal: jest.fn(), handleCreated: jest.fn() }),
}));
jest.mock('@/pages/Workspace/hooks/useOpenDefaultGroupSession', () => ({
  useOpenDefaultGroupSession: () => jest.fn(),
}));

// GroupChatPane 桩件：暴露一个按钮触发 onTogglePanel('manage')，模拟齿轮打开管理面板。
jest.mock('@/pages/Workspace/components/GroupChatPane', () => ({
  GroupChatPane: ({ onTogglePanel }: { onTogglePanel: (panel: string) => void }) => (
    <div data-testid="group-chat-pane">
      <button type="button" onClick={() => onTogglePanel('manage')}>
        打开管理面板
      </button>
    </div>
  ),
}));
jest.mock('@/pages/Workspace/components/GroupChatPane/SessionFilesModal', () => ({
  SessionFilesModal: () => null,
}));
jest.mock('@/pages/Workspace/components/GroupMembersPanelSlot', () => ({
  GroupMembersPanelSlot: () => null,
}));
jest.mock('@/pages/Workspace/components/Modals/CreateGroupModal', () => ({
  CreateGroupModal: () => null,
}));
jest.mock('@/pages/Workspace/components/WorkspaceManagePanels', () => ({
  WorkspaceManagePanels: () => null,
}));
jest.mock('@/pages/Workspace/components/GroupSidebar', () => {
  const actual = jest.requireActual<typeof import('@/pages/Workspace/components/GroupSidebar')>(
    '@/pages/Workspace/components/GroupSidebar',
  );
  return { ...actual, GroupSidebar: () => <aside data-testid="in-flow-group-sidebar" /> };
});

const { GroupWorkspaceArea } =
  require('@/pages/Workspace/GroupWorkspaceArea') as typeof import('@/pages/Workspace/GroupWorkspaceArea');

function renderArea() {
  return render(
    <GroupWorkspaceArea
      view="group"
      onViewChange={jest.fn()}
      availableViews={['chat', 'group']}
      mobileListOpen={false}
      onCloseMobileList={jest.fn()}
    />,
  );
}

describe('GroupWorkspaceArea 管理面板打开期间切群补拉详情', () => {
  beforeEach(() => {
    mockState.selectedGroupId = 'g1';
    mockReloadSelectedGroup.mockClear();
  });

  it('打开管理面板时为当前选中群拉取详情', () => {
    renderArea();
    fireEvent.click(screen.getByRole('button', { name: '打开管理面板' }));
    expect(mockReloadSelectedGroup).toHaveBeenCalledWith('g1');
  });

  it('面板保持打开时切换选中群，为新群重新拉取详情', () => {
    const view = renderArea();
    fireEvent.click(screen.getByRole('button', { name: '打开管理面板' }));
    mockReloadSelectedGroup.mockClear();

    // 模拟侧栏切群：选中群变为 g2（真实场景中 onSelectGroup 写 store 后 selectedGroupId 变化）。
    mockState.selectedGroupId = 'g2';
    view.rerender(
      <GroupWorkspaceArea
        view="group"
        onViewChange={jest.fn()}
        availableViews={['chat', 'group']}
        mobileListOpen={false}
        onCloseMobileList={jest.fn()}
      />,
    );

    expect(mockReloadSelectedGroup).toHaveBeenCalledWith('g2');
  });

  it('面板未打开时切换选中群不拉取详情（保持按需拉取约定）', () => {
    const view = renderArea();
    mockState.selectedGroupId = 'g3';
    view.rerender(
      <GroupWorkspaceArea
        view="group"
        onViewChange={jest.fn()}
        availableViews={['chat', 'group']}
        mobileListOpen={false}
        onCloseMobileList={jest.fn()}
      />,
    );
    expect(mockReloadSelectedGroup).not.toHaveBeenCalled();
  });
});
