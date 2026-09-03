/** @jest-environment jsdom */
import { SquarePageShell } from '@/components/CollaborationSquare/SquarePageShell';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { history } from '@umijs/max';
import { readFileSync } from 'node:fs';
import path from 'node:path';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@/hooks/useCollaborationSquare', () => ({ useCollaborationSquare: jest.fn() }));
jest.mock('@/components/CollaborationSquare/PublicBotCatalogPanel', () => ({
  PublicBotCatalogPanel: () => <div>bot panel</div>,
}));
jest.mock('@/components/CollaborationSquare/PublicGroupSquareSection', () => ({
  PublicGroupSquareSection: () => <div>group section</div>,
}));
jest.mock('@/components/CollaborationSquare/PublicTaskCatalogPanel', () => ({
  PublicTaskCatalogPanel: () => <div>task panel</div>,
}));

const mockedUseCollaborationSquare = useCollaborationSquare as jest.MockedFunction<typeof useCollaborationSquare>;

function makeSquare() {
  return {
    hasMore: false,
    loading: false,
    loadingMore: false,
    error: null,
    loadMoreError: null,
    loadMore: jest.fn(),
    load: jest.fn(),
    visibleBots: [],
    visibleGroups: [],
    tasks: [],
    botQuery: '',
    groupQuery: '',
    taskQuery: '',
    taskStatusFilter: 'all' as const,
    botSearchMode: 'name' as const,
    busyKeys: [],
    setQuery: jest.fn(),
    setBotSearchMode: jest.fn(),
    primaryBotAction: jest.fn(),
    share: jest.fn(),
    openBotProfile: jest.fn(),
    closeBotProfile: jest.fn(),
    selectedBotId: null,
    botProfile: null,
    detailLoading: false,
    copyBotId: jest.fn(),
    openGroupMembers: jest.fn(),
    createGroupSession: jest.fn(),
    selectedGroupId: null,
    selectedGroup: null,
    groupMembers: [],
    closeGroupMembers: jest.fn(),
    setTaskQuery: jest.fn(),
    setTaskStatusFilter: jest.fn(),
    resetTaskFilters: jest.fn(),
    openTaskDetail: jest.fn(),
  } as unknown as ReturnType<typeof useCollaborationSquare>;
}

describe('SquarePageShell three-way dispatch', () => {
  beforeEach(() => {
    mockedUseCollaborationSquare.mockReturnValue(makeSquare());
    (history.push as jest.Mock).mockClear();
  });

  test('resource=task 渲染任务面板、第三导航高亮、任务描述', () => {
    render(<SquarePageShell resource="task" />);
    expect(screen.getByText('task panel')).toBeInTheDocument();
    expect(screen.queryByText('bot panel')).not.toBeInTheDocument();
    expect(screen.queryByText('group section')).not.toBeInTheDocument();
    const taskNav = screen.getByRole('button', { name: /任务广场/ });
    expect(taskNav).toBeInTheDocument();
    expect(taskNav.className).toMatch(/bg-primary/);
    expect(screen.getByText(/发现公开 BBS 求助任务/)).toBeInTheDocument();
  });

  test('resource=bot 渲染 Bot 面板且第一导航高亮、其它导航不高亮', () => {
    render(<SquarePageShell resource="bot" />);
    expect(screen.getByText('bot panel')).toBeInTheDocument();
    const botNav = screen.getByRole('button', { name: /公开 Bot/ });
    expect(botNav.className).toMatch(/bg-primary/);
    const taskNav = screen.getByRole('button', { name: /任务广场/ });
    expect(taskNav.className).not.toMatch(/bg-primary/);
  });

  test('resource=group 渲染群块且第二导航高亮', () => {
    render(<SquarePageShell resource="group" />);
    expect(screen.getByText('group section')).toBeInTheDocument();
    const groupNav = screen.getByRole('button', { name: /公开协作群/ });
    expect(groupNav.className).toMatch(/bg-primary/);
  });

  test('三个资源导航始终可见', () => {
    render(<SquarePageShell resource="bot" />);
    expect(screen.getByRole('button', { name: /公开 Bot/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /公开协作群/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /任务广场/ })).toBeInTheDocument();
  });

  test('点击任务广场导航 push /collaboration-square/tasks', () => {
    render(<SquarePageShell resource="bot" />);
    fireEvent.click(screen.getByRole('button', { name: /任务广场/ }));
    expect(history.push).toHaveBeenCalledWith('/collaboration-square/tasks');
  });

  test('Shell 源码保持 UI 与分层约束且含任务描述', () => {
    const source = readFileSync(
      path.join(process.cwd(), 'src/components/CollaborationSquare/SquarePageShell/index.tsx'),
      'utf8',
    );
    expect(source).not.toContain('<button');
    expect(source).not.toContain('<dialog');
    expect(source).not.toContain('<select');
    expect(source).not.toContain('animate-pulse');
    expect(source).not.toContain('bg-gray-');
    expect(source).not.toContain('message.');
    expect(source).not.toContain('src/internal');
    expect(source).toContain('可按 Bot 名称或 Owner 用户名称搜索公开 Bot');
    expect(source).toContain('发现协作群，支持基于公开协作群快速创建新会话。');
    expect(source).toContain('发现公开 BBS 求助任务');
  });
});
