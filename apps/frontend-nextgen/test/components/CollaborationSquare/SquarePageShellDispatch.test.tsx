/** @jest-environment jsdom */
import { SquarePageShell } from '@/components/CollaborationSquare/SquarePageShell';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import type { AnchorHTMLAttributes, ReactNode } from 'react';

jest.mock('@umijs/max', () => ({
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
    useWorkspaceStore.getState().reset();
  });

  test('legacy resource=task 仍渲染旧任务面板（任务已重定向，路径不可达，仅保留组件渲染兜底）', () => {
    render(<SquarePageShell resource="task" />);
    expect(screen.getByText('task panel')).toBeInTheDocument();
    expect(screen.queryByText('bot panel')).not.toBeInTheDocument();
    expect(screen.queryByText('group section')).not.toBeInTheDocument();
    const description = screen.getByText(/发现公开 BBS 求助任务/);
    const resourceRegion = screen.getByRole('region', { name: '任务广场内容' });
    expect(screen.getByRole('banner')).toContainElement(description);
    expect(resourceRegion).toContainElement(description);
    expect(resourceRegion).toContainElement(screen.getByText('task panel'));
  });

  test('导航使用标准链接直接进入目标页面', () => {
    render(<SquarePageShell resource="bot" />);
    expect(screen.getByRole('link', { name: '公开协作群' })).toHaveAttribute('href', '/collaboration-square/groups');
  });

  test('resource=bot 渲染 Bot 面板且资源说明归属当前内容区', () => {
    render(<SquarePageShell resource="bot" />);
    expect(screen.getByText('bot panel')).toBeInTheDocument();
    const description = screen.getByText(/可按 Bot 名称或 Owner 用户名称搜索公开 Bot/);
    const resourceRegion = screen.getByRole('region', { name: '公开 Bot内容' });
    expect(screen.getByRole('banner')).toContainElement(description);
    expect(resourceRegion).toContainElement(description);
    expect(resourceRegion).toContainElement(screen.getByText('bot panel'));
    const botNav = screen.getByRole('link', { name: '公开 Bot' });
    expect(botNav.className).toMatch(/text-foreground/);
    expect(botNav).toHaveAttribute('aria-current', 'page');
  });

  test('resource=group 渲染群块且资源说明归属当前内容区', () => {
    render(<SquarePageShell resource="group" />);
    expect(screen.getByText('group section')).toBeInTheDocument();
    const description = screen.getByText('发现协作群，支持基于公开协作群快速创建新会话。');
    const resourceRegion = screen.getByRole('region', { name: '公开协作群内容' });
    expect(screen.getByRole('banner')).toContainElement(description);
    expect(resourceRegion).toContainElement(description);
    expect(resourceRegion).toContainElement(screen.getByText('group section'));
    const groupNav = screen.getByRole('link', { name: '公开协作群' });
    expect(groupNav.className).toMatch(/text-foreground/);
    expect(groupNav).toHaveAttribute('aria-current', 'page');
  });

  test('两个资源导航始终以命名导航链接呈现（社区已迁至实验室一级导航）', () => {
    render(<SquarePageShell resource="bot" />);
    const navigation = screen.getByRole('navigation');
    expect(screen.getByRole('link', { name: '公开 Bot' })).toHaveAttribute('href', '/collaboration-square/bots');
    expect(screen.getByRole('link', { name: '公开协作群' })).toHaveAttribute('href', '/collaboration-square/groups');
    expect(navigation).toContainElement(screen.getByRole('link', { name: '公开 Bot' }));
  });

  test('Bot 工作身份下两 Tab 恒显（公开协作群固定登录用户身份，不再隐藏入口）', () => {
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:900004',
      identities: [{ id: 'bot-1:900004', kind: 'bot', displayName: 'Bot A', online: true }],
    });

    render(<SquarePageShell resource="bot" />);

    expect(screen.getByRole('link', { name: '公开 Bot' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '公开协作群' })).toBeInTheDocument();
  });

  test('Shell 与共享导航源码保持 UI、分层和社区迁移约束', () => {
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

    const navigationSource = readFileSync(
      path.join(process.cwd(), 'src/components/CollaborationSquare/SquareNavigation/index.tsx'),
      'utf8',
    );
    expect(navigationSource).not.toContain("label: '社区'");
    expect(navigationSource).not.toContain('/collaboration-square/community');
    expect(navigationSource).not.toContain('任务广场');
  });
});
