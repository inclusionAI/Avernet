/** @jest-environment jsdom */
import type { GroupView } from '@/domain/collaboration';
import { GroupItem } from '@/pages/Workspace/components/GroupSidebar/GroupItem';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

Object.defineProperty(globalThis, 'ResizeObserver', {
  configurable: true,
  value: class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
});

const group: GroupView = {
  groupId: 'g1',
  name: '协作群',
  kind: 'free_chat',
  status: 'active',
  participants: [],
  sessions: [],
  lastMessageAt: 1,
  createdAt: 1,
  participantCount: 0,
  isPublic: false,
  deliveryPolicy: 'send_to_driver' as const,
};

function renderItem(
  onCreateSession: (groupId: string, scope?: 'full' | 'participant') => void,
  viewerKind: 'user' | 'bot',
) {
  return render(
    <GroupItem
      group={group}
      viewerKind={viewerKind}
      expanded={false}
      sessions={[]}
      sessionTab="all"
      onSessionTabChange={jest.fn()}
      favoriteSessionIds={[]}
      selectedGroupId={null}
      selectedSessionId={null}
      onSelectGroup={jest.fn()}
      onToggleGroupExpanded={jest.fn()}
      onSelectSession={jest.fn()}
      onToggleFavorite={jest.fn()}
      onCreateSession={onCreateSession}
      onManageGroup={jest.fn()}
      onManageSession={jest.fn()}
      onShareGroup={jest.fn()}
      onDissolveGroup={jest.fn()}
      hasMoreSessions={false}
      isLoadingMoreSessions={false}
      onLoadMoreSessions={jest.fn()}
    />,
  );
}

describe('GroupItem 新建会话视角菜单', () => {
  it('human 身份：点「+」打开菜单，选参与者视角以该 scope 创建', () => {
    const onCreateSession = jest.fn();
    renderItem(onCreateSession, 'user');
    fireEvent.click(screen.getByRole('button', { name: /新建会话/ }));
    fireEvent.click(screen.getByText('参与者视角'));
    expect(onCreateSession).toHaveBeenCalledWith('g1', 'participant');
  });

  it('human 身份：选完整视角以 full 创建', () => {
    const onCreateSession = jest.fn();
    renderItem(onCreateSession, 'user');
    fireEvent.click(screen.getByRole('button', { name: /新建会话/ }));
    fireEvent.click(screen.getByText('完整视角'));
    expect(onCreateSession).toHaveBeenCalledWith('g1', 'full');
  });

  it('bot 身份：点「+」不弹菜单，直接以单参创建（不传 scope）', () => {
    const onCreateSession = jest.fn();
    renderItem(onCreateSession, 'bot');
    fireEvent.click(screen.getByRole('button', { name: /新建会话/ }));
    expect(screen.queryByText('参与者视角')).toBeNull();
    expect(onCreateSession).toHaveBeenCalledWith('g1');
  });
});
