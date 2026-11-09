/**
 * @jest-environment jsdom
 *
 * 覆盖 GroupWorkspaceArea 的群管理面板详情补拉编排：
 * 面板保持打开时切换选中群，必须为新群重新拉取群详情（reloadSelectedGroup），
 * 否则「群成员管理」card 因 participants 为空 + participantCount>0 永远显示「加载中…」。
 */
// 槽位真实装配必须先于 GroupWorkspaceArea 模块求值（useGroupChatHeaderPanels 在模块作用域解析槽位）：
// import 按声明序求值，require 于文件体执行、必然晚于本行。
import '../../helpers/registerOpenTaskPanelBootstrapSlot';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { GroupView } from '@/domain/collaboration';

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
const mockState: {
  selectedGroupId: string | null;
  selectedGroup: GroupView;
  hideGroup?: boolean;
  expandedGroupIds?: Record<string, true>;
} = { selectedGroupId: 'g1', selectedGroup: baseGroup };
const mockSessionLoad = jest.fn();
const mockReloadSelectedGroup = jest.fn();
const mockOnSelectGroup = jest.fn();
const mockOpenSession = jest.fn();
const mockApplySessionUpdate = jest.fn();
const mockGetSessionDetail = jest.fn(async (sessionId: string) => ({
  ok: true,
  data: { sessionId, title: '会话详情' },
}));
const mockCreateGroupShare = jest.fn(async () => ({ ok: true, data: { invitationUrl: '/invite/groups/test' } }));
const mockCreateSessionShare = jest.fn(async () => ({ ok: true, data: { invitationUrl: '/invite/sessions/test' } }));

const mockStore = {
  sessionTabsByGroup: {},
  setSessionTabForGroup: jest.fn(),
  selectGroup: jest.fn(),
  membership: 'direct' as const,
};

jest.mock('@/stores/workspaceStore', () => ({
  useWorkspaceStore: (selector?: (s: typeof mockStore) => unknown) => (selector ? selector(mockStore) : mockStore),
}));
jest.mock('@/services/workspace/sessionService', () => ({ sessionService: { getSessionDetail: mockGetSessionDetail } }));
jest.mock('sonner', () => ({ toast: { info: jest.fn(), error: jest.fn(), success: jest.fn() } }));

jest.mock('@/pages/Workspace/hooks/useGroupWorkspace', () => ({
  useGroupWorkspace: () => ({
    groups: mockState.hideGroup ? [] : [baseGroup],
    expandedGroupIds: mockState.expandedGroupIds ?? {},
    selectedGroupId: mockState.selectedGroupId,
    selectedGroup: mockState.selectedGroup,
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
// 会话数据可切换：接线守护用例需注入选中会话（resources 副屏渲染条件之一）。
const sessionsMock = { selectedSession: null as { sessionId: string; title: string } | null };
jest.mock('@/pages/Workspace/hooks/useGroupSessions', () => ({
  useGroupSessions: (groupId: string, expandedGroupIds: string[]) => {
    mockSessionLoad(groupId, expandedGroupIds);
    return ({
    sessionsByGroupId: {},
    hasMoreSessionsByGroupId: {},
    totalSessionsByGroupId: {},
    isLoadingMoreSessionsByGroupId: {},
    loadMoreSessions: jest.fn(),
    errorByGroupId: {},
    loadMoreErrorByGroupId: {},
    reloadGroup: jest.fn(),
    selectedSession: sessionsMock.selectedSession,
    selectedSessionId: sessionsMock.selectedSession?.sessionId ?? null,
    openSession: mockOpenSession,
    createSessionIn: jest.fn(),
    leaveSession: jest.fn(),
    updateMemberMode: jest.fn(),
    applySessionUpdate: mockApplySessionUpdate,
    favoriteSessionIds: [],
    sessionSearchText: '',
    setSessionSearchText: jest.fn(),
    toggleFavorite: jest.fn(),
    renameSession: jest.fn(),
    deleteSession: jest.fn(),
  });
  },
}));
// GroupWorkspaceArea 直接读 chat.chat.messages / chat.panelRef（头部图标域编排），
// 桩件需提供最小 chat 契约（其余 chat.* 字段仅作为 props 透传给已 mock 的 GroupChatPane）。
jest.mock('@/pages/Workspace/hooks/useGroupChat', () => ({
  useGroupChat: () => ({ chat: { messages: [] }, panelRef: { current: null } }),
}));
jest.mock('@/pages/Workspace/hooks/useGroupManagement', () => ({
  useGroupManagement: () => ({ createShare: mockCreateGroupShare }),
}));
jest.mock('@/pages/Workspace/hooks/useSessionManagement', () => ({
  useSessionManagement: () => ({ createShare: mockCreateSessionShare }),
}));
jest.mock('@/pages/Workspace/hooks/useGroupCreateDialog', () => ({
  useGroupCreateDialog: () => ({ open: false, openModal: jest.fn(), closeModal: jest.fn(), handleCreated: jest.fn() }),
}));
jest.mock('@/pages/Workspace/hooks/useOpenDefaultGroupSession', () => ({
  useOpenDefaultGroupSession: () => jest.fn(),
}));

// GroupChatPane 桩件：暴露按钮触发 onTogglePanel('manage'/'resources')，模拟齿轮与文件入口。
jest.mock('@/pages/Workspace/components/GroupChatPane', () => ({
  GroupChatPane: ({ onTogglePanel }: { onTogglePanel: (panel: string) => void }) => (
    <div data-testid="group-chat-pane">
      <button type="button" onClick={() => onTogglePanel('manage')}>
        打开管理面板
      </button>
      <button type="button" onClick={() => onTogglePanel('sessionManage')}>打开会话管理面板</button>
      <button type="button" onClick={() => onTogglePanel('none')}>关闭临时面板</button>
      <button type="button" onClick={() => onTogglePanel('resources')}>
        打开资源面板
      </button>
    </div>
  ),
}));
// SessionFilesSidebar 桩件按 open 渲染标记元素（内联副屏常挂、open 开合）：
// 守护 resources 面板接线（接线断裂时标记不出现；未激活时 open=false 不出现）。
jest.mock('@/pages/Workspace/components/GroupChatPane/SessionFilesSidebar', () => ({
  SessionFilesSidebar: ({ open }: { open?: boolean }) => (open ? <div data-testid="session-files-sidebar" /> : null),
}));
jest.mock('@/pages/Workspace/components/GroupMembersPanelSlot', () => ({
  GroupMembersPanelSlot: () => null,
}));
jest.mock('@/pages/Workspace/components/Modals/CreateGroupModal', () => ({
  CreateGroupModal: () => null,
}));
jest.mock('@/pages/Workspace/components/WorkspaceManagePanels', () => ({
  WorkspaceManagePanels: ({ onShareGroup, onShareSession }: {
    onShareGroup: (expiresInSeconds: number) => Promise<unknown>;
    onShareSession: (expiresInSeconds: number) => Promise<unknown>;
  }) => (
    <>
      <button type="button" onClick={() => void onShareGroup(604800)}>生成群分享链接</button>
      <button type="button" onClick={() => void onShareSession(2592000)}>生成会话分享链接</button>
    </>
  ),
}));
jest.mock('@/pages/Workspace/components/GroupSidebar', () => {
  const actual = jest.requireActual<typeof import('@/pages/Workspace/components/GroupSidebar')>(
    '@/pages/Workspace/components/GroupSidebar',
  );
  return {
    ...actual,
    GroupSidebar: ({ onManageSession }: { onManageSession: (groupId: string, sessionId: string) => void }) => (
      <aside data-testid="in-flow-group-sidebar">
        <button type="button" onClick={() => onManageSession('g1', 's1')}>侧栏管理会话</button>
      </aside>
    ),
  };
});

const { GroupWorkspaceArea } =
  require('@/pages/Workspace/GroupWorkspaceArea') as typeof import('@/pages/Workspace/GroupWorkspaceArea');

function renderArea() {
  return render(<GroupWorkspaceArea mobileListOpen={false} onCloseMobileList={jest.fn()} />);
}

describe('GroupWorkspaceArea 管理面板打开期间切群补拉详情', () => {
  beforeEach(() => {
    mockState.selectedGroupId = 'g1';
    mockState.selectedGroup = baseGroup;
    mockState.hideGroup = false;
    mockState.expandedGroupIds = {};
    mockSessionLoad.mockClear();
    sessionsMock.selectedSession = null;
    mockOpenSession.mockClear();
    mockOnSelectGroup.mockClear();
    mockGetSessionDetail.mockClear();
    mockApplySessionUpdate.mockClear();
    mockReloadSelectedGroup.mockClear();
    mockCreateGroupShare.mockClear();
    mockCreateSessionShare.mockClear();
  });

  it('筛选隐藏/恢复当前已展开群不改变数据加载的 expandedGroupIds', () => {
    mockState.expandedGroupIds = { g1: true };
    const view = renderArea();
    expect(mockSessionLoad).toHaveBeenLastCalledWith('g1', ['g1']);
    mockState.hideGroup = true;
    view.rerender(<GroupWorkspaceArea mobileListOpen={false} onCloseMobileList={jest.fn()} />);
    expect(mockSessionLoad).toHaveBeenLastCalledWith('g1', ['g1']);
    mockState.hideGroup = false;
    view.rerender(<GroupWorkspaceArea mobileListOpen={false} onCloseMobileList={jest.fn()} />);
    expect(mockSessionLoad).toHaveBeenLastCalledWith('g1', ['g1']);
  });

  it('受限群入口仍只加载 scope 内的展开群', () => {
    mockState.expandedGroupIds = { g1: true, other: true };
    render(<GroupWorkspaceArea
      scope={{ identityId: 'human-1', group: baseGroup, session: null }}
      mobileListOpen={false} onCloseMobileList={jest.fn()}
    />);
    expect(mockSessionLoad).toHaveBeenLastCalledWith('g1', ['g1']);
  });

  it('右缘面板内联于对话区流内（无蒙层抽屉），不再抬高整个会话遮挡持久副屏', () => {
    sessionsMock.selectedSession = { sessionId: 's1', title: '自定义协作会话' };
    renderArea();
    const conversation = screen.getByTestId('group-chat-pane').closest('[data-workspace-conversation]');
    expect(conversation).not.toHaveClass('z-20');

    fireEvent.click(screen.getByRole('button', { name: '打开会话管理面板' }));
    expect(conversation).toHaveClass('isolate');
    expect(conversation).not.toHaveClass('z-20');
    // 内联副屏：面板容器（桩件）渲染在对话区流内，非 portal 悬浮蒙层抽屉。
    expect(conversation).toContainElement(screen.getByRole('button', { name: '生成群分享链接' }));
    fireEvent.click(screen.getByRole('button', { name: '关闭临时面板' }));
    expect(conversation).not.toHaveClass('z-20');
  });

  it('侧栏管理当前会话只补拉详情，不重新选群或触发会话历史刷新', async () => {
    sessionsMock.selectedSession = { sessionId: 's1', title: '自定义协作会话' };
    renderArea();
    fireEvent.click(screen.getByRole('button', { name: '侧栏管理会话' }));

    expect(mockOnSelectGroup).not.toHaveBeenCalled();
    expect(mockOpenSession).not.toHaveBeenCalled();
    expect(mockGetSessionDetail).toHaveBeenCalledWith('s1');
    await waitFor(() => expect(mockApplySessionUpdate).toHaveBeenCalledWith('s1', {
      sessionId: 's1', title: '会话详情',
    }));
    expect(screen.getByTestId('group-chat-pane').closest('[data-workspace-conversation]')).not.toHaveClass('z-20');
  });

  it.each([
    ['g1', 's2'],
    ['g2', 's1'],
  ])('管理非当前会话仍通过 openSession 选中目标（当前 %s/%s）', async (groupId, sessionId) => {
    mockState.selectedGroupId = groupId;
    sessionsMock.selectedSession = { sessionId, title: '其他会话' };
    renderArea();
    fireEvent.click(screen.getByRole('button', { name: '侧栏管理会话' }));

    expect(mockOnSelectGroup).not.toHaveBeenCalled();
    expect(mockOpenSession).toHaveBeenCalledTimes(1);
    expect(mockOpenSession).toHaveBeenCalledWith('g1', 's1');
    await waitFor(() => expect(mockApplySessionUpdate).toHaveBeenCalled());
  });

  it('管理面板分享有效期传入 createShare 的第二个参数，不作为目标 ID', () => {
    renderArea();
    fireEvent.click(screen.getByRole('button', { name: '生成群分享链接' }));
    expect(mockCreateGroupShare).toHaveBeenCalledWith(undefined, 604800);

    fireEvent.click(screen.getByRole('button', { name: '生成会话分享链接' }));
    expect(mockCreateSessionShare).toHaveBeenCalledWith(undefined, 2592000);
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
    view.rerender(<GroupWorkspaceArea mobileListOpen={false} onCloseMobileList={jest.fn()} />);

    expect(mockReloadSelectedGroup).toHaveBeenCalledWith('g2');
  });

  it('面板打开时同群被新列表对象替换（详情未加载）会再次拉取，详情回填后停止', () => {
    const view = renderArea();
    fireEvent.click(screen.getByRole('button', { name: '打开管理面板' }));
    expect(mockReloadSelectedGroup).toHaveBeenCalledTimes(1);
    mockReloadSelectedGroup.mockClear();

    // 列表刷新把同群替换为新的列表对象：摘要存在但完整详情未加载。
    mockState.selectedGroup = {
      ...baseGroup,
      participantSummaries: [{ actorId: 'b1', kind: 'bot', name: '摘要成员' }],
      participantDetailsLoaded: false,
    };
    view.rerender(<GroupWorkspaceArea mobileListOpen={false} onCloseMobileList={jest.fn()} />);
    expect(mockReloadSelectedGroup).toHaveBeenCalledWith('g1');

    // 完整详情回填（participantDetailsLoaded=true）后同一群不再重复请求。
    mockReloadSelectedGroup.mockClear();
    mockState.selectedGroup = {
      ...baseGroup,
      participants: [{ actorId: 'b1', kind: 'bot', name: '完整成员', role: 'owner', mode: 'auto' }],
      participantDetailsLoaded: true,
    };
    view.rerender(<GroupWorkspaceArea mobileListOpen={false} onCloseMobileList={jest.fn()} />);
    expect(mockReloadSelectedGroup).not.toHaveBeenCalled();
  });

  it('面板未打开时切换选中群不拉取详情（保持按需拉取约定）', () => {
    const view = renderArea();
    mockState.selectedGroupId = 'g3';
    view.rerender(<GroupWorkspaceArea mobileListOpen={false} onCloseMobileList={jest.fn()} />);
    expect(mockReloadSelectedGroup).not.toHaveBeenCalled();
  });

  it('资源面板接线守护：resources 激活且选中会话时渲染会话文件副屏（桩件标记元素出现）', () => {
    // 防回归：GroupWorkspaceArea 的 activePanel === 'resources' 分支必须挂载
    // SessionFilesSidebar（副屏化重构后的接线契约，接线断裂时标记不出现）。
    // 渲染条件：选中会话存在（mock 注入）。
    sessionsMock.selectedSession = { sessionId: 's1', title: '验收会话' };
    renderArea();
    expect(screen.queryByTestId('session-files-sidebar')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '打开资源面板' }));
    expect(screen.getByTestId('session-files-sidebar')).toBeInTheDocument();
    const conversation = screen.getByTestId('group-chat-pane').closest('[data-workspace-conversation]');
    expect(conversation).toHaveClass('relative', 'isolate', 'min-w-0');
    expect(conversation).toContainElement(screen.getByTestId('session-files-sidebar'));
    expect(conversation).not.toContainElement(screen.getByTestId('in-flow-group-sidebar'));

    // 切回管理面板：resources 副屏被替换（单副屏互斥）。
    fireEvent.click(screen.getByRole('button', { name: '打开管理面板' }));
    expect(screen.queryByTestId('session-files-sidebar')).not.toBeInTheDocument();

    sessionsMock.selectedSession = null;
  });
});
