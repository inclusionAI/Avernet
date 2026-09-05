/** @jest-environment jsdom */
import { SquarePageShell } from '@/components/CollaborationSquare/SquarePageShell';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import { history } from '@umijs/max';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import type { AnchorHTMLAttributes, ReactNode } from 'react';

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
  Link: ({ to, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string; children: ReactNode }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));
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
    const taskNav = screen.getByRole('link', { name: /任务广场/ });
    expect(taskNav).toBeInTheDocument();
    expect(taskNav.className).toMatch(/text-foreground/);
    expect(taskNav).toHaveAttribute('aria-current', 'page');
    const description = screen.getByText(/发现公开 BBS 求助任务/);
    const resourceRegion = screen.getByRole('region', { name: '任务广场内容' });
    expect(screen.getByRole('banner')).toContainElement(description);
    expect(resourceRegion).toContainElement(description);
    expect(resourceRegion).toContainElement(screen.getByText('task panel'));
  });

  test('路由 Tab 切换先播放下划线动效，再进入目标页面', () => {
    jest.useFakeTimers();
    render(<SquarePageShell resource="bot" />);

    fireEvent.click(screen.getByRole('link', { name: /公开协作群/ }));

    expect(history.push).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: /公开协作群/ }).className).toMatch(/text-foreground/);
    expect(screen.getByRole('link', { name: /公开 Bot/ }).className).toMatch(/text-muted-foreground/);

    act(() => {
      jest.advanceTimersByTime(200);
    });
    expect(history.push).toHaveBeenCalledWith('/collaboration-square/groups');
    jest.useRealTimers();
  });

  test('resource=bot 渲染 Bot 面板且资源说明归属当前内容区', () => {
    render(<SquarePageShell resource="bot" />);
    expect(screen.getByText('bot panel')).toBeInTheDocument();
    const description = screen.getByText(/可按 Bot 名称或 Owner 用户名称搜索公开 Bot/);
    const resourceRegion = screen.getByRole('region', { name: '公开 Bot内容' });
    expect(screen.getByRole('banner')).toContainElement(description);
    expect(resourceRegion).toContainElement(description);
    expect(resourceRegion).toContainElement(screen.getByText('bot panel'));
    const botNav = screen.getByRole('link', { name: /公开 Bot/ });
    expect(botNav.className).toMatch(/text-foreground/);
    expect(botNav).toHaveAttribute('aria-current', 'page');
    const taskNav = screen.getByRole('link', { name: /任务广场/ });
    expect(taskNav.className).toMatch(/text-muted-foreground/);
    expect(taskNav).not.toHaveAttribute('aria-current');
  });

  test('resource=group 渲染群块且资源说明归属当前内容区', () => {
    render(<SquarePageShell resource="group" />);
    expect(screen.getByText('group section')).toBeInTheDocument();
    const description = screen.getByText('发现协作群，支持基于公开协作群快速创建新会话。');
    const resourceRegion = screen.getByRole('region', { name: '公开协作群内容' });
    expect(screen.getByRole('banner')).toContainElement(description);
    expect(resourceRegion).toContainElement(description);
    expect(resourceRegion).toContainElement(screen.getByText('group section'));
    const groupNav = screen.getByRole('link', { name: /公开协作群/ });
    expect(groupNav.className).toMatch(/text-foreground/);
    expect(groupNav).toHaveAttribute('aria-current', 'page');
  });

  test('三个资源导航始终以命名导航链接呈现', () => {
    render(<SquarePageShell resource="bot" />);
    const navigation = screen.getByRole('navigation', { name: '协作广场资源导航' });
    expect(screen.getByRole('link', { name: /公开 Bot/ })).toHaveAttribute('href', '/collaboration-square/bots');
    expect(screen.getByRole('link', { name: /公开协作群/ })).toHaveAttribute(
      'href',
      '/collaboration-square/groups',
    );
    expect(screen.getByRole('link', { name: /任务广场/ })).toHaveAttribute('href', '/collaboration-square/tasks');
    expect(navigation).toContainElement(screen.getByRole('link', { name: /公开 Bot/ }));
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
