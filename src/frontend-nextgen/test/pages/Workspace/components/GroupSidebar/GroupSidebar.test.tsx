/** @jest-environment jsdom */
import { GroupSidebar } from '@/pages/Workspace/components/GroupSidebar';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    fireEvent.click(screen.getByText('发起协作'));
    expect(onCreateGroup).toHaveBeenCalled();
  });

  it('群列表错误不伪装成空态，并提供重试入口', () => {
    const onRetryGroups = jest.fn().mockResolvedValue(undefined);
    render(<GroupSidebar {...makeProps({ groups: [], groupsError: '协作群加载失败', onRetryGroups })} />);
    expect(screen.getByRole('alert')).toHaveTextContent('协作群加载失败');
    expect(screen.queryByText('暂无协作群')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetryGroups).toHaveBeenCalledTimes(1);
  });

  it('群会话错误保留父对象并提供局部重试', () => {
    const onReloadSession = jest.fn().mockResolvedValue(undefined);
    render(
      <GroupSidebar
        {...makeProps({
          sessionsByGroupId: {},
          errorByGroupId: { g1: '群会话加载失败' },
          onReloadSession,
        })}
      />,
    );
    expect(screen.getByText('主站群')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('群会话加载失败');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onReloadSession).toHaveBeenCalledWith('g1');
  });

  it('协作群搜索工具行触发发起协作', () => {
    const onCreateGroup = jest.fn();
    render(<GroupSidebar {...makeProps({ onCreateGroup })} />);
    const actionButton = screen.getByRole('button', { name: '发起协作' });
    const searchInput = screen.getByRole('textbox', { name: '搜索协作群' });
    const toolRow = searchInput.parentElement?.parentElement;
    const tabRow = screen.getByRole('group', { name: '工作区类型' }).parentElement;
    expect(toolRow).toContainElement(actionButton);
    expect(tabRow).not.toContainElement(actionButton);
    fireEvent.click(actionButton);
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
    expect(screen.getByText('主站群')).toHaveClass('text-sm', 'font-normal');
    expect(screen.getByText('主站群')).not.toHaveClass('font-semibold');
    expect(groupTrigger.textContent).toMatch(/主站群.*公开.*任务协作.*固定群成员/);
    expect(groupTrigger.textContent).not.toContain('6 个成员');
    expect(screen.queryByLabelText('6 个成员')).not.toBeInTheDocument();
  });

  it('协作群标签截断时可通过 hover 查看完整内容', async () => {
    render(
      <GroupSidebar
        {...makeProps({
          groups: [{ ...baseGroup, isPublic: true, kind: 'task_dag' as const, membership: 'session_only' as const }],
        })}
      />,
    );

    const metadata = screen.getByLabelText('协作群标签：公开 · 自定义协同 · 仅参与临时会话');
    expect(metadata).toHaveClass('truncate');
    await userEvent.setup().hover(metadata);
    expect(await screen.findByRole('tooltip')).toHaveTextContent('公开 · 自定义协同 · 仅参与临时会话');
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
    expect(screen.getByRole('button', { name: '协作群操作' })).toHaveClass(
      'rounded-md',
      'hover:bg-primary/10',
      'hover:text-primary',
    );
    expect(
      screen.getByRole('button', { name: '主站群' }).querySelector('svg.lucide-chevron-right'),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '协作群操作' }));
    fireEvent.click(screen.getByRole('button', { name: '管理协作群' }));
    expect(onManageGroup).toHaveBeenCalledWith('g1');
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
    const createSessionButton = screen.getByRole('button', { name: '新建会话' });
    expect(createSessionButton).toHaveClass(
      'h-7',
      'w-7',
      'rounded-md',
      'text-muted-foreground',
      'hover:bg-primary/10',
      'hover:text-primary',
    );
    const scopeButton = screen.getByRole('button', { name: '会话范围：全部会话' });
    expect(scopeButton).toHaveClass('h-7', 'w-7');
    expect(scopeButton.querySelector('svg.lucide-list-filter')).toBeInTheDocument();
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

    const groupList = screen.getByText(/^协作群 \(\d+\)$/).nextElementSibling;
    expect(groupList).toHaveClass('divide-y', 'divide-border/70');
    const sessionList = screen.getByLabelText('协作群会话列表：主站群');
    expect(sessionList).toHaveClass('border-t');
    expect(sessionList).not.toHaveClass('border-b', 'ml-[60px]', 'border-l', 'pl-2');
    expect(sessionList.firstElementChild).not.toHaveClass('border-b');
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
    expect(filterPanel).toHaveClass(
      'w-[360px]',
      'max-w-[calc(100vw-1rem)]',
      'rounded-lg',
      'border',
      'bg-popover',
      'p-3',
      'shadow-md',
    );
    expect(filterPanel).not.toHaveClass('mx-[18px]', 'mt-2');
    const sidebarScrollArea = screen.getByText(/^协作群 \(\d+\)$/).parentElement?.parentElement;
    expect(sidebarScrollArea).not.toContainElement(filterPanel);
    expect(screen.getByRole('radiogroup', { name: '协作群类型' })).toHaveClass('min-w-0');
    expect(screen.getByRole('radiogroup', { name: '协作群类型' }).querySelector('.flex')).toHaveClass(
      'min-w-0',
      'flex-wrap',
      'gap-1',
    );
    expect(screen.getByRole('radiogroup', { name: '协作群参与方式' })).toHaveClass('min-w-0', 'border-t', 'pt-3');
    expect(screen.getByRole('radiogroup', { name: '协作群参与方式' }).querySelector('.flex')).toHaveClass(
      'min-w-0',
      'flex-wrap',
      'gap-1',
    );
    expect(screen.getByRole('radio', { name: '全部' }).querySelector('svg')).toBeInTheDocument();
  });

  it('群名称搜索框保留 focus ring 的左侧可视空间', () => {
    render(<GroupSidebar {...makeProps()} />);
    const searchInput = screen.getByRole('textbox', { name: '搜索协作群' });
    expect(searchInput.parentElement?.parentElement).toHaveClass('px-[18px]');
  });

  it('顶部视图切换只承担导航，未选中 Tab 保持清晰对比', () => {
    render(<GroupSidebar {...makeProps()} />);
    const inactiveTab = screen.getByRole('button', { name: '对话' });
    const activeTab = screen.getByRole('button', { name: '协作群' });
    const actionButton = screen.getByRole('button', { name: '发起协作' });
    const tabRow = screen.getByRole('group', { name: '工作区类型' }).parentElement;
    expect(inactiveTab).toHaveAttribute('aria-pressed', 'false');
    expect(activeTab).toHaveAttribute('aria-pressed', 'true');
    expect(activeTab).toHaveClass('bg-background', 'text-primary', 'shadow-sm');
    expect(inactiveTab).toHaveClass('text-muted-foreground');
    expect(tabRow).not.toContainElement(actionButton);
    expect(actionButton).toHaveClass('h-9', 'w-9', 'rounded-md');
    expect(actionButton).toHaveClass('border-primary/20', 'bg-primary/5', 'text-primary');
    expect(actionButton).not.toHaveClass('bg-primary', 'text-primary-foreground');
    expect(actionButton).not.toHaveClass('lg:hidden');
  });

  it('群名称搜索框、筛选按钮与发起协作按钮等高', () => {
    render(<GroupSidebar {...makeProps()} />);
    expect(screen.getByRole('textbox', { name: '搜索协作群' })).toHaveClass('h-9');
    expect(screen.getByRole('button', { name: '筛选' })).toHaveClass('h-9');
    expect(screen.getByRole('button', { name: '发起协作' })).toHaveClass('h-9');
  });

  it('协作身份移出二级侧栏，群卡片保留可读间距并降低标题字重', () => {
    render(<GroupSidebar {...makeProps()} />);
    expect(screen.queryByRole('button', { name: '当前协作身份：风太' })).not.toBeInTheDocument();
    const groupTrigger = screen.getByRole('button', { name: /主站群/ });
    expect(groupTrigger.parentElement).toHaveClass('min-h-16', 'bg-primary/5', 'px-4', 'py-2.5');
    expect(groupTrigger).toHaveClass('px-0', 'py-1');
    expect(screen.getByText('主站群')).toHaveClass('text-sm', 'font-normal');
    expect(screen.getByText('主站群')).not.toHaveClass('font-semibold');
    expect(screen.getByText('自由聊天').parentElement).toHaveClass('text-xs', 'leading-4');
  });

  it('选中协作群暂无会话时展示明确空态', () => {
    render(
      <GroupSidebar
        {...makeProps({
          selectedGroupId: 'g1',
          sessionsByGroupId: { g1: [] },
        })}
      />,
    );

    expect(screen.getByText('当前协作群暂无会话')).toBeInTheDocument();
    expect(screen.queryByText('暂无协作群临时会话')).not.toBeInTheDocument();
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

  it('对象行会话范围 Icon 按群过滤收藏会话', () => {
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
    expect(screen.getByText('会话一')).toBeInTheDocument();
    expect(screen.queryByText('会话二')).not.toBeInTheDocument();
    const scopeButton = screen.getByRole('button', { name: '会话范围：已收藏会话' });
    expect(scopeButton).toHaveAttribute('aria-pressed', 'true');
    expect(scopeButton.textContent).toBe('');
    fireEvent.click(scopeButton);
    expect(screen.getByRole('radio', { name: /已收藏会话/ })).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByRole('radio', { name: /全部会话/ }));
    expect(onSessionTabForGroup).toHaveBeenCalledWith('g1', 'all');
  });

  it('会话范围使用对象行纯 Icon，选项在 Popover 中展示', () => {
    render(<GroupSidebar {...makeProps({ totalSessionsByGroupId: { g1: 12 } })} />);

    const scopeButton = screen.getByRole('button', { name: '会话范围：全部会话' });
    expect(scopeButton.textContent).toBe('');
    fireEvent.click(scopeButton);
    expect(screen.getByRole('radiogroup', { name: '会话范围' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '全部会话 12' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '已收藏会话 0' })).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole('button', { name: '会话范围：全部会话' }));
    expect(screen.getByRole('radio', { name: '全部会话 12' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '已收藏会话 1' })).toBeInTheDocument();
  });

  it('群会话总数未知时使用占位符，不用当前已加载条数冒充总数', () => {
    render(<GroupSidebar {...makeProps({ favoriteSessionIds: ['s1'] })} />);

    fireEvent.click(screen.getByRole('button', { name: '会话范围：全部会话' }));
    expect(screen.getByRole('radio', { name: '全部会话 …' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '已收藏会话 1' })).toBeInTheDocument();
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

    fireEvent.click(screen.getByRole('button', { name: '会话范围：全部会话' }));
    expect(screen.getByRole('radio', { name: '全部会话 …' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '已收藏会话 …' })).toBeInTheDocument();
  });

  it('收起协作群切换会话范围后自动展开对象', () => {
    const onSessionTabForGroup = jest.fn();
    const onToggleGroupExpanded = jest.fn();
    render(
      <GroupSidebar
        {...makeProps({
          expandedGroupIds: {},
          onSessionTabForGroup,
          onToggleGroupExpanded,
        })}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '会话范围：全部会话' }));
    fireEvent.click(screen.getByRole('radio', { name: '已收藏会话 0' }));

    expect(onSessionTabForGroup).toHaveBeenCalledWith('g1', 'favorite');
    expect(onToggleGroupExpanded).toHaveBeenCalledWith('g1');
  });

  it('收藏范围仅检查已加载分页时展示明确空态', () => {
    render(
      <GroupSidebar
        {...makeProps({
          sessionTabsByGroup: { g1: 'favorite' },
          favoriteSessionIds: [],
          hasMoreSessionsByGroupId: { g1: true },
        })}
      />,
    );

    expect(screen.getByText('当前已加载会话中暂无收藏')).toBeInTheDocument();
  });

  it('展开会话区不再渲染旧会话范围工具栏', () => {
    render(<GroupSidebar {...makeProps()} />);

    expect(screen.queryByRole('group', { name: '会话范围筛选' })).not.toBeInTheDocument();
  });

  it('协作群会话与 Bot 会话统一使用消息 Icon', () => {
    const { container } = render(<GroupSidebar {...makeProps()} />);

    expect(container.querySelectorAll('svg.lucide-message-square')).toHaveLength(2);
    expect(container.querySelector('[data-session-indicator].rounded-full')).not.toBeInTheDocument();
  });

  it('群会话没有次行内容时使用紧凑行高', () => {
    render(<GroupSidebar {...makeProps()} />);

    const sessionTrigger = screen.getByRole('button', { name: /会话一/ });
    expect(sessionTrigger).toHaveClass('min-h-12', 'py-2');
    expect(sessionTrigger.parentElement).toHaveClass('min-h-12');
    expect(sessionTrigger.textContent).not.toContain('个成员');
  });

  it('协作群列表向下滚动时一级 Tab 吸顶', () => {
    render(<GroupSidebar {...makeProps()} />);

    const tabGroup = screen.getByRole('group', { name: '工作区类型' });
    expect(tabGroup.parentElement?.parentElement).toHaveClass('sticky', 'top-0', 'z-20', 'bg-muted/20');
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

  it('closes the filter panel on outside pointer or Escape', async () => {
    const user = userEvent.setup();
    render(<GroupSidebar {...makeProps()} />);
    const filterButton = screen.getByRole('button', { name: '筛选' });
    await user.click(filterButton);
    expect(screen.getByRole('radio', { name: '全部' })).toBeInTheDocument();
    await user.click(document.body);
    expect(screen.queryByRole('radio', { name: '全部' })).not.toBeInTheDocument();

    await user.click(filterButton);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('radio', { name: '全部' })).not.toBeInTheDocument();
  });

  it('marks the filter button when a filter is applied', () => {
    render(<GroupSidebar {...makeProps({ membership: 'session_only' })} />);
    const filterButton = screen.getByRole('button', { name: '筛选' });
    expect(filterButton).toHaveAttribute('aria-pressed', 'true');
    expect(filterButton).toHaveClass('bg-primary/5');
  });

  it('收起态保留对话与协作群快捷切换图标', () => {
    const onViewChange = jest.fn();
    render(<GroupSidebar {...makeProps({ onViewChange })} />);

    fireEvent.click(screen.getByRole('button', { name: '收起对话协作左栏' }));

    expect(screen.getByRole('button', { name: '切换到对话' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '切换到协作群' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: '切换到对话' }));
    expect(onViewChange).toHaveBeenCalledWith('chat');
    fireEvent.click(screen.getByRole('button', { name: '展开对话协作左栏' }));
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
    fireEvent.click(screen.getByRole('button', { name: '加载更多会话' }));
    expect(onLoadMoreSessions).toHaveBeenCalledWith('g1');
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
  });
});
