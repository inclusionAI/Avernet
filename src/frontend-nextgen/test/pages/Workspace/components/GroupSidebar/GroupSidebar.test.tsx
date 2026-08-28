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
    expect(screen.getByText(/3 位成员/)).toBeInTheDocument();
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

  it('clicking a session does not collapse its group (no bubble to card)', () => {
    const onToggleGroupExpanded = jest.fn();
    render(<GroupSidebar {...makeProps({ onToggleGroupExpanded })} />);
    fireEvent.click(screen.getByText('会话一'));
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
  });

  it('kind filter renders all 4 options after expanding 群类型', () => {
    render(<GroupSidebar {...makeProps()} />);
    fireEvent.click(screen.getByRole('button', { name: /群类型/ }));
    expect(screen.getByRole('radio', { name: '全部' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '自由聊天' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '任务协作' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '自定义协同' })).toBeInTheDocument();
  });

  it('group collapse toggle hides sessions', () => {
    render(<GroupSidebar {...makeProps({ expandedGroupIds: {} })} />);
    expect(screen.queryByText('会话一')).not.toBeInTheDocument();
  });

  it('renders 群成员/会话成员 toggle and clicking 会话成员 fires onMembershipChange', () => {
    const onMembershipChange = jest.fn();
    render(<GroupSidebar {...makeProps({ membership: 'direct', onMembershipChange })} />);
    const directBtn = screen.getByRole('button', { name: '群成员' });
    const sessionBtn = screen.getByRole('button', { name: '会话成员' });
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
    // 切回全部触发 onSessionTabForGroup('g1', 'all')；按钮带计数文案
    expect(screen.getByRole('button', { name: '已收藏 (1)' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '全部 (2)' }));
    expect(onSessionTabForGroup).toHaveBeenCalledWith('g1', 'all');
  });
});
