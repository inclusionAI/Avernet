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
// dmore：bot Tab 的搜索栏 + 身份选择器上移页头；固定 mock 避免真实 hooks 触发请求。
jest.mock('@/components/CollaborationSquare/SquareSearchBar', () => ({
  __esModule: true,
  default: ({ className }: { className?: string }) => <div data-testid="square-search" className={className} />,
}));
jest.mock('@/components/CollaborationSquare/SquareIdentityPicker', () => ({
  SquareIdentityPicker: () => <div data-testid="square-identity-picker" />,
}));
// dmore artboard-004 Frame 2：群 Tab 页头工具组（固定登录身份展示）；mock 避免真实用户信息请求。
jest.mock('@/components/CollaborationSquare/PublicGroupSquareSection/GroupIdentityDisplay', () => ({
  GroupIdentityDisplay: () => <div data-testid="square-group-identity" />,
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

  test('resource=bot 头部注入搜索栏与身份选择器（dmore Frame 2 右端工具组）', () => {
    render(<SquarePageShell resource="bot" />);
    const navigation = screen.getByRole('navigation');
    const search = screen.getByTestId('square-search');
    const picker = screen.getByTestId('square-identity-picker');
    expect(navigation).toContainElement(search);
    expect(navigation).toContainElement(picker);
    // dmore artboard-002：下拉弹层 338px —— 触发器容器与其保持等宽（用户实测 222 偏短）。
    expect(picker.parentElement).toHaveClass('md:w-[338px]');
    // 搜索在前、身份选择器在后的顺序。
    expect(search.compareDocumentPosition(picker) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    // 内容区不再重复出现身份选择器。
    const resourceRegion = screen.getByRole('region', { name: '公开 Bot内容' });
    expect(resourceRegion).not.toContainElement(picker);
  });

  test('resource=group 页头注入搜索栏与固定身份展示（dmore artboard-004 Frame 2 群视图工具组）', () => {
    render(<SquarePageShell resource="group" />);
    const navigation = screen.getByRole('navigation');
    const search = screen.getByTestId('square-search');
    const groupIdentity = screen.getByTestId('square-group-identity');
    expect(navigation).toContainElement(search);
    expect(navigation).toContainElement(groupIdentity);
    // 搜索在前、身份展示在后的顺序。
    expect(search.compareDocumentPosition(groupIdentity) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    // 内容区不再重复出现工具栏。
    const resourceRegion = screen.getByRole('region', { name: '公开协作群内容' });
    expect(resourceRegion).not.toContainElement(search);
    expect(resourceRegion).not.toContainElement(groupIdentity);
    expect(screen.getByText('group section')).toBeInTheDocument();
  });

  test('resource=bot 保留导航和面板，不再渲染内容区标题及说明', () => {
    render(<SquarePageShell resource="bot" />);
    expect(screen.getByText('bot panel')).toBeInTheDocument();
    const resourceRegion = screen.getByRole('region', { name: '公开 Bot内容' });
    expect(screen.queryByRole('heading', { name: '公开 Bot' })).not.toBeInTheDocument();
    expect(screen.queryByText(/可按 Bot 名称或 Owner 用户名称搜索公开 Bot/)).not.toBeInTheDocument();
    expect(resourceRegion).toContainElement(screen.getByText('bot panel'));
    const botNav = screen.getByRole('link', { name: '公开 Bot' });
    expect(botNav).toHaveClass('bg-primary/10', 'text-primary');
    expect(resourceRegion).toHaveClass('space-y-4');
    // dmore：页头下 16px 起内容，左右 20px 边距，内容列宽 1200。
    expect(resourceRegion.parentElement).toHaveClass('pt-4', 'px-5');
    expect(resourceRegion.parentElement).not.toHaveClass('max-w-[1200px]');
    expect(botNav).toHaveAttribute('aria-current', 'page');
  });

  test('resource=group 保留导航与群块，不显示重复标题和说明', () => {
    render(<SquarePageShell resource="group" />);
    expect(screen.getByText('group section')).toBeInTheDocument();
    expect(screen.queryByText('发现协作群，支持基于公开协作群快速创建新会话。')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '公开协作群' })).not.toBeInTheDocument();
    const resourceRegion = screen.getByRole('region', { name: '公开协作群内容' });
    expect(screen.queryByRole('banner')).not.toBeInTheDocument();
    expect(resourceRegion).toContainElement(screen.getByText('group section'));
    const groupNav = screen.getByRole('link', { name: '公开协作群' });
    expect(groupNav).toHaveClass('bg-primary/10', 'text-primary');
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
    expect(source).not.toContain('可按 Bot 名称或 Owner 用户名称搜索公开 Bot');
    expect(source).not.toContain('发现协作群，支持基于公开协作群快速创建新会话。');

    const navigationSource = readFileSync(
      path.join(process.cwd(), 'src/components/CollaborationSquare/SquareNavigation/index.tsx'),
      'utf8',
    );
    expect(navigationSource).not.toContain("label: '社区'");
    expect(navigationSource).not.toContain('/collaboration-square/community');
    expect(navigationSource).not.toContain('任务广场');
  });
});

test('公开 Bot 页头搜索/身份选择器容器允许压缩：长名称与引擎标签截断而不是整行溢出', () => {
  render(<SquarePageShell resource="bot" />);
  const search = screen.getByTestId('square-search');
  const picker = screen.getByTestId('square-identity-picker');
  // md 以上仍为 348/338 设计宽度，但更窄的窗口允许收缩（内部已有 truncate）。
  expect(search).toHaveClass('min-w-0', 'md:w-[348px]');
  expect(search).not.toHaveClass('md:shrink-0');
  expect(picker.parentElement).toHaveClass('min-w-0', 'md:w-[338px]');
  expect(picker.parentElement).not.toHaveClass('shrink-0');
});

test('公开协作群页头固定身份容器同样允许压缩', () => {
  render(<SquarePageShell resource="group" />);
  const identity = screen.getByTestId('square-group-identity');
  expect(identity.parentElement).toHaveClass('min-w-0', 'md:w-[222px]');
  expect(identity.parentElement).not.toHaveClass('shrink-0');
});
