/**
 * GroupSidebar 系列测试共享 fixtures。
 *
 * 文件名含 `.testUtils`，不匹配 jest testMatch 的 `*.(test|spec|e2e).(ts|tsx)` 模式，
 * 因此不会被当作独立测试文件收集，仅被同目录各功能域 suite 文件引入。
 * 内容从原 GroupSidebar.test.tsx 原样迁出（断言与渲染逻辑零改动）。
 */
import { GroupSidebar } from '@/pages/Workspace/components/GroupSidebar';
import { jest } from '@jest/globals';
import React from 'react';

// SessionCard 标题截断检测依赖 ResizeObserver（jsdom 需 mock，模式同项目其他测试）。
// 各 suite 文件以 `beforeEach(setupResizeObserverMock)` 注册，保持原文件级 beforeEach 语义。
export const setupResizeObserverMock = () => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}

      unobserve() {}

      disconnect() {}
    },
  });
};

export const baseGroup = {
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

export function makeProps(partial: Partial<React.ComponentProps<typeof GroupSidebar>> = {}) {
  return {
    view: 'group' as 'chat' | 'group',
    onViewChange: jest.fn(),
    viewerKind: 'user' as const,
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
