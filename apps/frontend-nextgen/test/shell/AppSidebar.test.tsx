/** @jest-environment jsdom */
// 搜索弹窗（GlobalSearchDialog）的模块图引入 @umijs/max：mock factory 不引用外部 const。
import { extendCapabilities } from '@/capabilities';
import { getMergedNavigationItems } from '@/shell/navigation';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { afterEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn(), replace: jest.fn() },
}));
// factory 保持无外部引用（AppSidebar 的 import 求值早于 const 初始化，引用会得到 undefined）。
jest.mock('@/services/workspace/botSessionService', () => ({
  botSessionService: {
    listOwnedBotsWithMeta: jest.fn(async () => ({
      ok: true as const,
      data: { bots: [], hasAgentCodingBots: false },
    })),
    listSessions: jest.fn(),
  },
}));
jest.mock('@/services/workspace/groupService', () => ({
  groupService: { loadGroups: jest.fn(async () => ({ ok: true as const, data: [] })) },
}));
jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({
    identity: { userId: 'human-1', displayName: '验收用户', online: true },
    status: 'ready' as const,
  }),
}));

jest.mock('@/shell/SpaceSwitcher', () => ({
  SpaceSwitcher: ({ compact }: { compact?: boolean }) => (
    <div data-testid={compact ? 'space-switcher-compact' : 'space-switcher'}>选择空间</div>
  ),
}));
jest.mock('@/components/Admin/NotificationBell', () => ({
  NotificationBell: () => <div data-testid="notification-bell" />,
}));
jest.mock('@/shell/AccountBadge', () => ({
  AccountBadge: ({
    currentUser,
    collapsed,
  }: {
    currentUser?: { displayName: string; avatarUrl?: string } | null;
    collapsed?: boolean;
  }) => (
    <div data-testid={collapsed ? 'account-badge-collapsed' : 'account-badge'}>{currentUser?.displayName ?? ''}</div>
  ),
}));
jest.mock('@/shell/HelpMenu', () => ({
  HelpMenu: ({ variant }: { variant?: 'help' | 'more' }) => (
    <div data-testid={variant === 'more' ? 'more-menu' : 'help-menu'} />
  ),
}));

// 先注册所有 mock，再加载会间接导入 @umijs/max 的侧栏模块。
const { AppSidebar } = require('@/shell/AppSidebar') as typeof import('@/shell/AppSidebar');

const renderSidebar = (props: Partial<Parameters<typeof AppSidebar>[0]> = {}) =>
  render(
    <AppSidebar
      activePath="/workspace"
      collapsed={false}
      items={getMergedNavigationItems()}
      onNavigate={jest.fn()}
      onToggleCollapsed={jest.fn()}
      currentUser={{ displayName: '真实用户' }}
      {...props}
    />,
  );

afterEach(() => {
  useWorkspaceStore.getState().reset();
});

it('展开态：顶部品牌区挂通知中心，底部用户行渲染账号身份与「更多」入口', () => {
  renderSidebar();
  expect(screen.getByTestId('notification-bell')).toBeInTheDocument();
  expect(screen.queryByTestId('identity-switcher')).not.toBeInTheDocument();
  expect(screen.getByTestId('account-badge')).toHaveTextContent('真实用户');
  expect(screen.getByTestId('more-menu')).toBeInTheDocument();
});

it.each(['/ticket-center', '/work/my-task', '/bot-workshop'])(
  '路由 %s：展开与折叠侧栏均不展示全局工作身份，导航与账号保留',
  (activePath) => {
    const view = renderSidebar({ activePath });
    expect(view.queryByTestId('identity-switcher')).not.toBeInTheDocument();
    expect(view.getByTestId('account-badge')).toHaveTextContent('真实用户');
    expect(view.getByRole('region', { name: '协作' })).toBeInTheDocument();
    view.unmount();

    renderSidebar({ activePath, collapsed: true });
    expect(screen.queryByTestId('identity-switcher-collapsed')).not.toBeInTheDocument();
    expect(screen.getByTestId('account-badge-collapsed')).toHaveTextContent('真实用户');
    expect(screen.getByRole('button', { name: 'Bot管理' })).toBeInTheDocument();
  },
);

it('产品侧栏只展示协作 / Bot 双分组（不随路由整体换组）', () => {
  const view = renderSidebar({ activePath: '/admin' });
  expect(view.getByRole('region', { name: '协作' })).toBeInTheDocument();
  expect(view.getByRole('region', { name: 'Bot' })).toBeInTheDocument();
  expect(view.queryByRole('region', { name: '老功能' })).not.toBeInTheDocument();
});

it('同路由改名项归组：协作组含「对话」「协作群」「发现」，Bot 组含「Bot管理」', () => {
  const view = renderSidebar();
  const collab = view.getByRole('region', { name: '协作' });
  const bot = view.getByRole('region', { name: 'Bot' });
  expect(view.getByRole('button', { name: /对话/ })).toBeInTheDocument();
  expect(view.getByRole('button', { name: /协作群/ })).toBeInTheDocument();
  expect(view.getByRole('button', { name: /发现/ })).toBeInTheDocument();
  expect(view.getByRole('button', { name: /Bot管理/ })).toBeInTheDocument();
  expect(collab.textContent).toContain('对话');
  expect(collab.textContent).toContain('协作群');
  expect(collab.textContent).toContain('发现');
  expect(bot.textContent).toContain('Bot管理');
});

it('任务列表、协作权限与管理后台入口均不占产品导航位', () => {
  const view = renderSidebar();
  expect(view.queryByRole('region', { name: '老功能' })).not.toBeInTheDocument();
  expect(view.queryByRole('button', { name: /任务列表/ })).not.toBeInTheDocument();
  expect(view.queryByRole('button', { name: /协作权限/ })).not.toBeInTheDocument();
  expect(view.queryByRole('button', { name: /管理后台/ })).toBeNull();
});

it('折叠态：不展示身份切换入口，账号行仍可见，任务列表入口保持隐藏', () => {
  renderSidebar({ collapsed: true });
  expect(screen.queryByTestId('identity-switcher-collapsed')).not.toBeInTheDocument();
  expect(screen.getByTestId('account-badge-collapsed')).toBeInTheDocument();
  expect(screen.getByTestId('notification-bell')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Bot管理' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /任务列表/ })).not.toBeInTheDocument();
});

it('顶部功能图标排布（设计稿 artboard-016）：搜索入口位于通知中心左侧，点击打开全局搜索弹窗', () => {
  const view = renderSidebar();
  const search = view.getByRole('button', { name: '搜索' });
  const bell = view.getByTestId('notification-bell');
  // DOM 顺序上搜索钮先于通知中心
  expect(search.compareDocumentPosition(bell) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(search);
  expect(screen.getByPlaceholderText('搜索对话名称或你的Agent名称')).toBeInTheDocument();
});

// 展开态品牌区单行回归（设计稿 artboard-016：单行 52px）：compact 锁版与三枚功能图标同排。
// 此前内部字标锁版 ≈102px 同排曾致文字经 min-w-0 折叠钻到图标下被覆盖，终局解法是锁版按单行
// 预算自缩（图标组 96px；设计稿 176px/锁版 ≤80px，品牌锁版换新版后 rail 加宽至 224：200px/≤104px）保持单行；本测试锁结构而非像素：
// Logo 与搜索按钮落在同一位图行容器内（无独立图标行），且行内次序保持设计稿。
it('展开态品牌区单行排布：Logo 锁版与功能图标同容器，行内次序 Logo → 搜索 → 通知 → 折叠', () => {
  const view = renderSidebar();
  const logo = view.container.querySelector('img[alt="Avernet"]');
  expect(logo).toBeInTheDocument();
  const search = view.getByRole('button', { name: '搜索' });
  const row = search.closest('div') as HTMLElement;
  // Logo 与图标钮同属品牌区行容器（单行），Logo 不再独占一行
  expect(row.contains(logo)).toBe(true);
  const bell = view.getByTestId('notification-bell');
  const collapse = view.getByRole('button', { name: '折叠导航' });
  // logo 非空已由上方 toBeInTheDocument 保证；逻辑与既过类型收窄，又保住
  // 「Logo 缺失 → 表达式为 falsy → 断言失败」的原始语义（等价于原 ?. 写法）。
  expect(logo && logo.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(search.compareDocumentPosition(bell) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(bell.compareDocumentPosition(collapse) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it('折叠态：顶部图标列同样提供搜索入口', () => {
  renderSidebar({ collapsed: true });
  expect(screen.getByRole('button', { name: '搜索' })).toBeInTheDocument();
});

// 折叠态工具簇（搜索/通知/展开）与导航项同列且同 32px 同 hover 样式时无法区分（用户报混淆）。
// 区分走「主次层级」而非分隔线（延续 SidebarNavList 纯留白过渡的设计语言）：工具钮收 28px(sm)
// + 静态 muted 色阶（hover 恢复全对比度），导航项保持 32px 全对比度。本用例锁区分信号本身，
// 防后续改动把工具簇悄悄改回与导航同规格；展开态品牌行（52px 行内 96px 图标组）不在此例。
it('折叠态：工具簇图标以 sm 规格 + muted 色阶与导航项区分，导航项维持 32px 全对比度', () => {
  const view = renderSidebar({ collapsed: true });
  const search = view.getByRole('button', { name: '搜索' });
  const expand = view.getByRole('button', { name: '展开导航' });
  for (const btn of [search, expand]) {
    expect(btn).toHaveClass('h-7', 'w-7', 'text-muted-foreground');
  }
  const nav = view.getByRole('button', { name: 'Bot管理' });
  expect(nav).not.toHaveClass('h-7');
  expect(nav).not.toHaveClass('text-muted-foreground');
});

// extendCapabilities 合并后无法恢复，capability override 用例置于文件末尾。
it('internal overlay：空间切换器以 compact 形态挂在 Bot 分组标题右侧', () => {
  extendCapabilities({
    getShellVisibility: () => ({
      status: 'available',
      value: { spaceSwitcher: true, notificationBell: true },
    }),
  });
  const view = renderSidebar();
  const bot = view.getByRole('region', { name: 'Bot' });
  expect(view.getByTestId('space-switcher-compact')).toBeInTheDocument();
  expect(view.queryByTestId('space-switcher')).not.toBeInTheDocument();
  // compact 切换器位于 Bot 分组标题行内（先于组内导航项出现）
  expect(bot.textContent?.indexOf('选择空间')).toBeLessThan(bot.textContent?.indexOf('Bot管理') ?? -1);
});

it('notificationBell=false 形态：通知中心入口不渲染', () => {
  extendCapabilities({
    getShellVisibility: () => ({
      status: 'available',
      value: { spaceSwitcher: false, notificationBell: false },
    }),
  });
  renderSidebar();
  expect(screen.queryByTestId('notification-bell')).not.toBeInTheDocument();
});
