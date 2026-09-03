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
    selectedGroupId: null as string | null,
    selectedSessionId: null as string | null,
    onSelectSession: jest.fn(),
    onCreateSession: jest.fn(),
    onManageSession: jest.fn(),
    onToggleFavorite: jest.fn(),
    onClearSessionFilter: jest.fn(),
    onCreateGroup: jest.fn(),
    onAddFriend: jest.fn(),
    onManageGroup: jest.fn(),
    onShareGroup: jest.fn().mockResolvedValue({
      ok: true,
      data: { invitationUrl: 'https://example.com/invite/g1' },
    }),
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

  it('群会话使用更多操作菜单承载会话管理，操作不触发会话选择', () => {
    const onManageSession = jest.fn();
    const onSelectSession = jest.fn();
    const { container } = render(<GroupSidebar {...makeProps({ onManageSession, onSelectSession })} />);

    const moreButtons = screen.getAllByRole('button', { name: '会话更多操作' });
    expect(container.querySelector('.self-start')).not.toBeInTheDocument();
    expect(moreButtons).toHaveLength(2);
    fireEvent.click(moreButtons[0]);
    fireEvent.click(screen.getByRole('button', { name: '管理会话' }));

    expect(onManageSession).toHaveBeenCalledWith('g1', 's1');
    expect(onSelectSession).not.toHaveBeenCalled();
  });

  it('群行将公开标签置于辅助信息首位，并移除成员数量展示', () => {
    render(
      <GroupSidebar
        {...makeProps({
          groups: [{ ...baseGroup, isPublic: true, participantCount: 6, kind: 'task_master_slave' as const }],
        })}
      />,
    );

    const groupTrigger = screen.getByRole('button', { name: '主站群' });
    expect(groupTrigger.textContent).toMatch(/主站群.*公开.*任务协作.*固定群成员/);
    expect(groupTrigger.textContent).not.toContain('6 个成员');
    expect(screen.queryByLabelText('6 个成员')).not.toBeInTheDocument();
  });

  it('does not render session member count', () => {
    const sessions = [
      {
        ...baseGroup.sessions[0],
        participants: [],
        participantCount: 3,
      },
    ];
    render(<GroupSidebar {...makeProps({ sessionsByGroupId: { g1: sessions } })} />);
    expect(screen.queryByText(/3 个成员/)).not.toBeInTheDocument();
  });

  it('将收藏会话藏进更多操作菜单并从菜单切换收藏状态', () => {
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

    expect(screen.queryByRole('button', { name: '取消收藏' })).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: '会话更多操作' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '取消收藏' }));
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

  it('协作群管理菜单的每个操作都使用统一图标', () => {
    render(<GroupSidebar {...makeProps()} />);
    fireEvent.click(screen.getByRole('button', { name: '协作群操作' }));

    for (const label of ['管理协作群', '分享协作群', '解散协作群']) {
      expect(screen.getByRole('button', { name: label }).querySelector('svg')).toBeInTheDocument();
    }
  });

  it('群卡片移除展开箭头，新增入口与管理入口均不触发展开或选中', () => {
    const onCreateSession = jest.fn();
    const onManageGroup = jest.fn();
    const onToggleGroupExpanded = jest.fn();
    render(<GroupSidebar {...makeProps({ onCreateSession, onManageGroup, onToggleGroupExpanded })} />);
    expect(screen.getByRole('button', { name: '协作群操作' })).toHaveClass('rounded-md');
    expect(
      screen.getByRole('button', { name: '主站群' }).querySelector('svg.lucide-chevron-right'),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '协作群操作' }));
    fireEvent.click(screen.getByRole('button', { name: '管理协作群' }));
    expect(onManageGroup).toHaveBeenCalledWith('g1');
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
    const createSessionButton = screen.getByRole('button', { name: '新建会话' });
    expect(createSessionButton).toHaveClass('h-7', 'w-7', 'rounded-md');
    expect(screen.getByRole('group', { name: '会话范围筛选' })).toBeInTheDocument();
    fireEvent.click(createSessionButton);
    expect(onCreateSession).toHaveBeenCalledWith('g1');
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
  });

  it('群会话管理菜单与收藏使用统一图标', () => {
    render(<GroupSidebar {...makeProps()} />);
    fireEvent.click(screen.getAllByRole('button', { name: '会话更多操作' })[0]);

    expect(screen.getByRole('button', { name: '收藏会话' }).querySelector('svg')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '管理会话' }).querySelector('svg')).toBeInTheDocument();
  });

  it('群间列表使用可见分割线，展开会话不再产生左侧缩进', () => {
    const secondGroup = { ...baseGroup, groupId: 'g2', name: '新品发布协作组', sessions: [] };
    render(<GroupSidebar {...makeProps({ groups: [baseGroup, secondGroup] })} />);

    const groupList = screen.getByText('协作群', { selector: 'span.font-medium' }).parentElement?.nextElementSibling;
    expect(groupList).toHaveClass('divide-y', 'divide-border/70');
    const sessionList = screen.getByLabelText('协作群会话列表：主站群');
    expect(sessionList).not.toHaveClass('ml-[60px]', 'border-l');
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
    expect(screen.getByRole('radio', { name: '固定群成员' })).toBeInTheDocument();
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
    expect(filterPanel).toHaveClass('bg-muted', 'border-y', 'px-[18px]', 'py-3');
    expect(screen.getByRole('radiogroup', { name: '协作群类型' })).toHaveClass(
      'grid',
      'min-w-0',
      'grid-cols-[4rem_minmax(0,1fr)]',
      'gap-1',
    );
    expect(screen.getByRole('radiogroup', { name: '协作群类型' }).querySelector('.flex')).toHaveClass(
      'min-w-0',
      'flex-nowrap',
      'overflow-x-auto',
      'scrollbar-hide',
      'gap-x-2',
    );
    expect(screen.getByRole('radiogroup', { name: '协作群参与方式' })).toHaveClass(
      'min-w-0',
      'grid-cols-[4rem_minmax(0,1fr)]',
      'gap-1',
    );
    expect(screen.getByRole('radiogroup', { name: '协作群参与方式' }).querySelector('.flex')).toHaveClass(
      'min-w-0',
      'flex-nowrap',
      'overflow-x-auto',
      'scrollbar-hide',
      'gap-x-2',
    );
    expect(screen.getByRole('radio', { name: '全部' }).querySelector('svg')).toBeInTheDocument();
  });

  it('群名称搜索框保留 focus ring 的左侧可视空间', () => {
    render(<GroupSidebar {...makeProps()} />);
    const searchInput = screen.getByRole('textbox', { name: '搜索协作群' });
    expect(searchInput.parentElement?.parentElement).toHaveClass('px-[18px]');
  });

  it('顶部视图切换与新增按钮等高，未选中 Tab 保持清晰对比', () => {
    render(<GroupSidebar {...makeProps()} />);
    const inactiveTab = screen.getByRole('tab', { name: '对话' });
    const activeTab = screen.getByRole('tab', { name: '协作群' });
    const actionButton = screen.getByRole('button', { name: '添加好友或发起协作' });
    expect(inactiveTab).toHaveClass('h-9', 'border-transparent', 'text-muted-foreground');
    expect(inactiveTab).toHaveClass('hover:bg-transparent', 'hover:text-foreground');
    expect(activeTab).toHaveClass('h-9', 'border-primary', 'text-primary');
    expect(activeTab).toHaveClass('hover:bg-transparent', 'hover:text-primary');
    expect(actionButton).toHaveClass('h-9', 'w-9');
    expect(actionButton).toHaveClass('border-input', 'bg-background', 'text-muted-foreground');
    expect(actionButton).not.toHaveClass('bg-primary', 'text-primary-foreground');
    expect(actionButton).not.toHaveClass('lg:hidden');
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
    expect(groupTrigger.parentElement).toHaveClass('min-h-[72px]', 'px-[18px]', 'py-3');
    expect(groupTrigger).toHaveClass('px-0', 'py-1');
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
    const directBtn = screen.getByRole('radio', { name: '固定群成员' });
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
    const favoriteTab = screen.getByRole('button', { name: /已收藏会话/ });
    const allTab = screen.getByRole('button', { name: /全部会话/ });
    expect(favoriteTab).toHaveClass('text-primary');
    expect(allTab).toHaveClass('text-muted-foreground');
    fireEvent.click(allTab);
    expect(onSessionTabForGroup).toHaveBeenCalledWith('g1', 'all');
  });

  it('会话范围筛选使用弱化的行内文字样式并保留水平内边距', () => {
    render(<GroupSidebar {...makeProps({ totalSessionsByGroupId: { g1: 12 } })} />);

    const allTab = screen.getByRole('button', { name: '全部会话 12' });
    const favoriteTab = screen.getByRole('button', { name: '已收藏会话 0' });
    expect(allTab).toHaveClass('px-2.5', 'border-0', 'rounded-none', 'text-primary');
    expect(favoriteTab).toHaveClass('px-2.5', 'border-0', 'rounded-none', 'text-muted-foreground');
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

  it('群会话总数未知时使用占位符，不用当前已加载条数冒充总数', () => {
    render(<GroupSidebar {...makeProps({ favoriteSessionIds: ['s1'] })} />);

    expect(screen.getByRole('button', { name: '全部会话 …' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '已收藏会话 1' })).toBeInTheDocument();
  });

  it('does not present the loaded-page favorite count as a total while more group sessions remain', () => {
    render(
      <GroupSidebar
        {...makeProps({
          favoriteSessionIds: ['s1'],
          hasMoreSessionsByGroupId: { g1: true },
        })}
      />,
    );

    expect(screen.getByRole('button', { name: '全部会话 …' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '已收藏会话 …' })).toBeInTheDocument();
  });

  it('群会话没有次行内容时使用紧凑行高', () => {
    render(<GroupSidebar {...makeProps()} />);

    const sessionTrigger = screen.getByRole('button', { name: /会话一/ });
    expect(sessionTrigger).toHaveClass('min-h-[56px]', 'py-2');
    expect(sessionTrigger.parentElement).toHaveClass('min-h-[56px]');
    expect(sessionTrigger.textContent).not.toContain('个成员');
  });

  it('协作群列表向下滚动时一级 Tab 吸顶', () => {
    render(<GroupSidebar {...makeProps()} />);

    const tabList = screen.getByRole('tablist', { name: '工作区类型' });
    expect(tabList.parentElement).toHaveClass('sticky', 'top-0', 'z-20', 'bg-muted');
    expect(tabList.parentElement?.parentElement).toHaveClass('bg-muted');
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

  it('再次点击筛选按钮可以收起筛选面板', () => {
    render(<GroupSidebar {...makeProps()} />);
    const filterButton = screen.getByRole('button', { name: '筛选' });
    fireEvent.click(filterButton);
    expect(filterButton).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(filterButton);
    expect(filterButton).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('radio', { name: '全部' })).not.toBeInTheDocument();
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
    expect(filterButton).toHaveClass('bg-primary/5');
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
