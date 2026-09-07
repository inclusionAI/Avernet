/**
 * @jest-environment jsdom
 *
 * 覆盖 GroupWorkspaceArea 的 <lg 协作群列表抽屉（二级菜单）。聊天视图的会话列表抽屉在 WorkspacePage
 * 内用同一 Drawer + close-on-select 模式，结构等价；自动收起则复用 useMediaQuery 副作用
 * （已由 useMediaQuery.test 与 AppShellResponsive.test 覆盖同款 useMinWidth(1024) 逻辑）。
 */
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

const baseGroup = {
  groupId: 'g1',
  name: '主站群',
  kind: 'free_chat' as const,
  status: 'active' as const,
  participants: [],
  sessions: [
    {
      sessionId: 's1',
      groupId: 'g1',
      title: '会话一',
      kind: 'chat' as const,
      status: 'running' as const,
      participants: [],
      lastMessageAt: 1,
      createdAt: 1,
      favorite: false,
    },
  ],
  lastMessageAt: 1,
  createdAt: 1,
  participantCount: 2,
  isPublic: false,
  deliveryPolicy: 'send_to_driver' as const,
};

const mockStore = {
  sessionTabsByGroup: {},
  setSessionTabForGroup: jest.fn(),
  selectGroup: jest.fn(),
  membership: 'direct' as const,
};

const mockOpenSession = jest.fn();
const mockApplySessionUpdate = jest.fn();

jest.mock('@/stores/workspaceStore', () => ({
  useWorkspaceStore: (selector?: (s: typeof mockStore) => unknown) => (selector ? selector(mockStore) : mockStore),
}));
jest.mock('@/services/workspace/sessionService', () => ({ sessionService: { getSessionDetail: jest.fn() } }));
jest.mock('sonner', () => ({ toast: { info: jest.fn() } }));

jest.mock('@/pages/Workspace/hooks/useGroupWorkspace', () => ({
  useGroupWorkspace: () => ({
    groups: [baseGroup],
    expandedGroupIds: { g1: true } as Record<string, true>,
    selectedGroupId: null,
    selectedGroup: null,
    canManageGroup: false,
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
    onSelectGroup: jest.fn(),
    dissolveGroup: jest.fn(),
    refreshGroups: jest.fn(),
    reloadSelectedGroup: jest.fn(),
    activeIdentity: null,
    identities: [],
  }),
}));
jest.mock('@/pages/Workspace/hooks/useGroupSessions', () => ({
  useGroupSessions: () => ({
    sessionsByGroupId: { g1: baseGroup.sessions },
    selectedSession: null,
    selectedSessionId: null,
    selectedGroupId: null,
    mockApplySessionUpdate,
    openSession: mockOpenSession,
    createSessionIn: jest.fn(),
    leaveSession: jest.fn(),
    favoriteSessionIds: [],
    sessionSearchText: '',
    setSessionSearchText: jest.fn(),
    toggleFavorite: jest.fn(),
    renameSession: jest.fn(),
    deleteSession: jest.fn(),
    addMember: jest.fn(),
    removeMember: jest.fn(),
    createShare: jest.fn(),
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

// 重叶子组件桩化；GroupSidebarList（抽屉内容）保持真实，GroupSidebar（内流外壳）桩化避免与抽屉重复渲染群名。
jest.mock('@/pages/Workspace/components/GroupChatPane', () => ({
  GroupChatPane: () => <div data-testid="group-chat-pane" />,
}));
jest.mock('@/pages/Workspace/components/GroupChatPane/SessionFilesModal', () => ({
  SessionFilesModal: () => null,
}));
jest.mock('@/pages/Workspace/components/MembersPanel', () => ({ MembersPanel: () => null }));
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

// Drawer 受控桩件：open=true 渲染 children + 一个 onClose 触发按钮，open=false 渲染 null。
// 规避 Radix Dialog 在 jsdom 下退出动画不触发导致内容不卸载的问题。
jest.mock('@/components/ui', () => {
  const actual = jest.requireActual<typeof import('@/components/ui')>('@/components/ui');
  return {
    ...actual,
    Drawer: ({
      open,
      onOpenChange,
      children,
    }: {
      open: boolean;
      onOpenChange?: (o: boolean) => void;
      children: React.ReactNode;
    }) =>
      open ? (
        <div data-testid="drawer">
          <button type="button" onClick={() => onOpenChange?.(false)}>
            关闭抽屉
          </button>
          {children}
        </div>
      ) : null,
    DrawerContent: ({ children, bodyClassName }: { children: React.ReactNode; bodyClassName?: string }) => (
      <div data-testid="drawer-content" className={bodyClassName}>
        {children}
      </div>
    ),
    DrawerTitle: ({ children }: { children: React.ReactNode }) => <span data-testid="drawer-title">{children}</span>,
  };
});

const { GroupWorkspaceArea } =
  require('@/pages/Workspace/GroupWorkspaceArea') as typeof import('@/pages/Workspace/GroupWorkspaceArea');

function renderArea(mobileListOpen: boolean) {
  const onCloseMobileList = jest.fn();
  const view = render(
    <GroupWorkspaceArea
      view="group"
      onViewChange={jest.fn()}
      availableViews={['chat', 'group']}
      mobileListOpen={mobileListOpen}
      onCloseMobileList={onCloseMobileList}
    />,
  );
  return { view, onCloseMobileList };
}

describe('GroupWorkspaceArea responsive off-canvas list (二级・协作群)', () => {
  it('mobileListOpen=false: no drawer, in-flow sidebar present', () => {
    renderArea(false);
    expect(screen.getByTestId('in-flow-group-sidebar')).toBeInTheDocument();
    expect(screen.queryByTestId('drawer-content')).not.toBeInTheDocument();
  });

  it('mobileListOpen=true: drawer renders the group list with its session', () => {
    renderArea(true);
    expect(screen.getByTestId('drawer-content')).toBeInTheDocument();
    expect(screen.getByText('主站群')).toBeInTheDocument();
    expect(screen.getByText('会话一')).toBeInTheDocument();
  });

  it('selecting a session from the drawer opens it and closes the drawer', () => {
    mockOpenSession.mockClear();
    const { onCloseMobileList } = renderArea(true);

    fireEvent.click(screen.getByText('会话一'));
    expect(mockOpenSession).toHaveBeenCalledWith('g1', 's1');
    expect(onCloseMobileList).toHaveBeenCalled();
  });

  it('drawer onOpenChange(false) (overlay/escape) calls onCloseMobileList', () => {
    const { onCloseMobileList } = renderArea(true);
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
    expect(onCloseMobileList).toHaveBeenCalled();
  });
});
