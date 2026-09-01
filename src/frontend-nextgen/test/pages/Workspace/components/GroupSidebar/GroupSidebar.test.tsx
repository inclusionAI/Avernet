/** @jest-environment jsdom */
import { GroupSidebar } from '@/pages/Workspace/components/GroupSidebar';
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
    {
      sessionId: 's2',
      groupId: 'g1',
      title: '会话二',
      kind: 'chat' as const,
      status: 'running' as const,
      participants: [],
      lastMessageAt: 2,
      createdAt: 2,
      favorite: false,
    },
  ],
  lastMessageAt: 1,
  createdAt: 1,
  participantCount: 2,
  isPublic: false,
  deliveryPolicy: 'send_to_driver' as const,
};

function makeProps(partial: Partial<React.ComponentProps<typeof GroupSidebar>> = {}) {
  return {
    view: 'group' as 'chat' | 'group',
    onViewChange: jest.fn(),
    groups: [baseGroup],
    isLoading: false,
    onSelectGroup: jest.fn(),
    groupSearchText: '',
    onSearchTextChange: jest.fn(),
    kindFilter: 'all' as const,
    onKindFilterChange: jest.fn(),
    sortMode: 'createdAt' as const,
    onSortModeChange: jest.fn(),
    expandedGroupIds: { g1: true } as Record<string, true>,
    onToggleGroupExpanded: jest.fn(),
    sessionsByGroupId: { g1: baseGroup.sessions },
    sessionTabsByGroup: {} as Record<string, 'all' | 'favorite'>,
    onSessionTabForGroup: jest.fn(),
    favoriteSessionIds: [] as string[],
    sessionSearchText: '',
    onSessionSearchTextChange: jest.fn(),
    selectedSessionId: null as string | null,
    onSelectSession: jest.fn(),
    onCreateSession: jest.fn(),
    onManageSession: jest.fn(),
    onToggleFavorite: jest.fn(),
    onClearSessionFilter: jest.fn(),
    onCreateGroup: jest.fn(),
    onAddFriend: jest.fn(),
    onManageGroup: jest.fn(),
    onShareGroup: jest.fn(),
    onDissolveGroup: jest.fn(),
    membership: 'direct' as const,
    onMembershipChange: jest.fn(),
    ...partial,
  };
}

describe('GroupSidebar', () => {
  it('empty state with create CTA when no groups', () => {
    const onCreateGroup = jest.fn();
    render(<GroupSidebar {...makeProps({ groups: [], onCreateGroup })} />);
    expect(screen.getByText(/暂无协作群/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '发起协作' }));
    expect(onCreateGroup).toHaveBeenCalled();
  });

  it('顶部蓝色 + 触发发起协作', () => {
    const onCreateGroup = jest.fn();
    render(<GroupSidebar {...makeProps({ onCreateGroup })} />);
    fireEvent.click(screen.getByRole('button', { name: '添加好友或发起协作' }));
    fireEvent.click(screen.getAllByRole('button', { name: '发起协作' })[0]);
    expect(onCreateGroup).toHaveBeenCalled();
  });

  it('nested sessions render under selected group, clicking session fires onSelectSession', () => {
    const onSelectSession = jest.fn();
    render(<GroupSidebar {...makeProps({ onSelectSession })} />);
    fireEvent.click(screen.getByText('会话一'));
    expect(onSelectSession).toHaveBeenCalledWith('g1', 's1');
  });

  it('renders session member count from participantCount without participants detail', () => {
    const sessions = [
      {
        ...baseGroup.sessions[0],
        participants: [],
        participantCount: 3,
      },
    ];
    render(<GroupSidebar {...makeProps({ sessionsByGroupId: { g1: sessions } })} />);
    expect(screen.getByText(/3 个成员/)).toBeInTheDocument();
  });

  it('renders collected session star in favorited state and toggles from card', () => {
    const onToggleFavorite = jest.fn();
    const sessions = [{ ...baseGroup.sessions[0], favorite: false }];
    render(
      <GroupSidebar
        {...makeProps({
          sessionsByGroupId: { g1: sessions },
          favoriteSessionIds: ['s1'],
          onToggleFavorite,
        })}
      />,
    );
    const favoriteButton = screen.getByRole('button', { name: '取消收藏' });
    fireEvent.click(favoriteButton);
    expect(onToggleFavorite).toHaveBeenCalledWith('s1');
  });

  it('clicking the group card toggles collapse (not only the chevron)', () => {
    const onSelectGroup = jest.fn();
    const onToggleGroupExpanded = jest.fn();
    render(<GroupSidebar {...makeProps({ onSelectGroup, onToggleGroupExpanded })} />);
    fireEvent.click(screen.getByText('主站群'));
    expect(onSelectGroup).toHaveBeenCalledWith('g1');
    expect(onToggleGroupExpanded).toHaveBeenCalledWith('g1');
  });

  it('群卡片操作按钮不触发展开或选中', () => {
    const onCreateSession = jest.fn();
    const onManageGroup = jest.fn();
    const onToggleGroupExpanded = jest.fn();
    render(<GroupSidebar {...makeProps({ onCreateSession, onManageGroup, onToggleGroupExpanded })} />);
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    expect(onCreateSession).toHaveBeenCalledWith('g1');
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '管理协作群' }));
    expect(onManageGroup).toHaveBeenCalledWith('g1');
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
  });

  it('clicking a session does not collapse its group (no bubble to card)', () => {
    const onToggleGroupExpanded = jest.fn();
    render(<GroupSidebar {...makeProps({ onToggleGroupExpanded })} />);
    fireEvent.click(screen.getByText('会话一'));
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
  });

  it('filter panel renders participation and all 4 group-kind options', () => {
    render(<GroupSidebar {...makeProps()} />);
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));
    expect(screen.getByRole('radio', { name: '固定协作群成员' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '仅参与临时会话' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '全部' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '自由聊天' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '任务协作' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '自定义协同' })).toBeInTheDocument();
    expect(screen.getAllByRole('radiogroup').map((group) => group.getAttribute('aria-label'))).toEqual([
      '协作群类型',
      '协作群参与方式',
    ]);
    const filterPanel = screen.getByRole('radiogroup', { name: '协作群参与方式' }).parentElement;
    expect(filterPanel).toHaveClass('bg-muted-foreground/10', 'shadow-sm');
  });

  it('群名称搜索框保留 focus ring 的左侧可视空间', () => {
    render(<GroupSidebar {...makeProps()} />);
    const searchInput = screen.getByRole('textbox', { name: '搜索协作群' });
    expect(searchInput.parentElement?.parentElement).toHaveClass('px-1');
  });

  it('顶部视图切换与新增按钮等高，未选中 Tab 保持清晰对比', () => {
    render(<GroupSidebar {...makeProps()} />);
    const inactiveTab = screen.getByRole('button', { name: '对话' });
    const activeTab = screen.getByRole('button', { name: '协作群' });
    const actionButton = screen.getByRole('button', { name: '添加好友或发起协作' });
    expect(inactiveTab.parentElement).toHaveClass('h-9');
    expect(inactiveTab).toHaveClass(
      'bg-background/60',
      'text-foreground/80',
      'hover:bg-background',
      'hover:text-foreground',
    );
    expect(activeTab).toHaveClass('bg-primary/10', 'text-primary', 'hover:bg-primary/15', 'hover:text-primary');
    expect(actionButton).toHaveClass('h-9', 'w-9');
  });

  it('群名称搜索框与筛选按钮等高', () => {
    render(<GroupSidebar {...makeProps()} />);
    expect(screen.getByRole('textbox', { name: '搜索协作群' })).toHaveClass('h-9');
    expect(screen.getByRole('button', { name: '筛选' })).toHaveClass('h-9');
  });

  it('协作身份保持紧凑，群卡片保留可读间距并降低标题字重', () => {
    render(
      <GroupSidebar
        {...makeProps({
          identities: [{ id: 'me', name: '风太', kind: 'user', avatar: '风' }],
          activeIdentityId: 'me',
        })}
      />,
    );
    expect(screen.getByRole('button', { name: '当前协作身份：风太' })).toHaveClass('min-h-10');
    const groupTrigger = screen.getByRole('button', { name: /主站群/ });
    expect(groupTrigger.parentElement).toHaveClass('p-1');
    expect(groupTrigger).toHaveClass('px-2', 'py-1.5');
    expect(screen.getByText('主站群')).toHaveClass('font-medium');
    expect(screen.getByText('主站群')).not.toHaveClass('font-semibold');
  });

  it('group collapse toggle hides sessions', () => {
    render(<GroupSidebar {...makeProps({ expandedGroupIds: {} })} />);
    expect(screen.queryByText('会话一')).not.toBeInTheDocument();
  });

  it('renders explicit participation labels and clicking temporary-session participation fires onMembershipChange', () => {
    const onMembershipChange = jest.fn();
    render(<GroupSidebar {...makeProps({ membership: 'direct', onMembershipChange })} />);
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));
    const directBtn = screen.getByRole('radio', { name: '固定协作群成员' });
    const sessionBtn = screen.getByRole('radio', { name: '仅参与临时会话' });
    expect(directBtn).toBeInTheDocument();
    expect(sessionBtn).toBeInTheDocument();
    fireEvent.click(sessionBtn);
    expect(onMembershipChange).toHaveBeenCalledWith('session_only');
  });

  it('per-group favorite tab shows only favorite sessions and fires onSessionTabForGroup', () => {
    const onSessionTabForGroup = jest.fn();
    render(
      <GroupSidebar
        {...makeProps({
          sessionTabsByGroup: { g1: 'favorite' },
          favoriteSessionIds: ['s1'],
          onSessionTabForGroup,
        })}
      />,
    );
    // 收藏 tab 下仅 s1 可见，s2 被隐藏
    expect(screen.getByText('会话一')).toBeInTheDocument();
    expect(screen.queryByText('会话二')).not.toBeInTheDocument();
    // 切回全部触发 onSessionTabForGroup('g1', 'all')；群会话 Tab 不重复显示数量
    expect(screen.getByRole('button', { name: /已收藏会话/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /全部会话/ }));
    expect(onSessionTabForGroup).toHaveBeenCalledWith('g1', 'all');
  });

  it('renders all and favorite session counts beside collaboration session tabs', () => {
    render(
      <GroupSidebar
        {...makeProps({
          totalSessionsByGroupId: { g1: 12 },
          favoriteSessionIds: ['s1'],
        })}
      />,
    );
    expect(screen.getByRole('button', { name: '全部会话 12' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '已收藏会话 1' })).toBeInTheDocument();
  });

  it('closes the filter panel after selecting a filter', () => {
    const onMembershipChange = jest.fn();
    render(<GroupSidebar {...makeProps({ onMembershipChange })} />);
    const filterButton = screen.getByRole('button', { name: '筛选' });
    fireEvent.click(filterButton);
    fireEvent.click(screen.getByRole('radio', { name: '仅参与临时会话' }));
    expect(onMembershipChange).toHaveBeenCalledWith('session_only');
    expect(screen.queryByRole('radio', { name: '仅参与临时会话' })).not.toBeInTheDocument();
    expect(filterButton).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes the filter panel on outside pointer or Escape', () => {
    render(<GroupSidebar {...makeProps()} />);
    const filterButton = screen.getByRole('button', { name: '筛选' });
    fireEvent.click(filterButton);
    expect(screen.getByRole('radio', { name: '全部' })).toBeInTheDocument();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('radio', { name: '全部' })).not.toBeInTheDocument();

    fireEvent.click(filterButton);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('radio', { name: '全部' })).not.toBeInTheDocument();
  });

  it('marks the filter button when a filter is applied', () => {
    render(<GroupSidebar {...makeProps({ membership: 'session_only' })} />);
    const filterButton = screen.getByRole('button', { name: '筛选' });
    expect(filterButton).toHaveAttribute('aria-pressed', 'true');
    expect(filterButton).toHaveClass('bg-primary/10');
  });

  it('loads more sessions without toggling the group card', () => {
    const onLoadMoreSessions = jest.fn<(groupId: string) => Promise<void>>().mockResolvedValue(undefined);
    const onToggleGroupExpanded = jest.fn();
    render(
      <GroupSidebar
        {...makeProps({
          onLoadMoreSessions,
          onToggleGroupExpanded,
          hasMoreSessionsByGroupId: { g1: true },
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '加载更多' }));
    expect(onLoadMoreSessions).toHaveBeenCalledWith('g1');
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
  });
});
