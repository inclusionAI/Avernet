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
// 同一模块注册表条目（AppSidebar 内的 './GlobalSearch' 解析到同一 exports 对象），
// 供下方 globalSearch=false 用例 spy GlobalSearchDialog 的挂载调用。
const GlobalSearchModule = require('@/shell/GlobalSearch') as typeof import('@/shell/GlobalSearch');

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
    expect(screen.getByRole('button', { name: 'Bot 管理' })).toBeInTheDocument();
  },
);

it('产品侧栏只展示协作 / Bot 双分组（不随路由整体换组）', () => {
  const view = renderSidebar({ activePath: '/admin' });
  expect(view.getByRole('region', { name: '协作' })).toBeInTheDocument();
  expect(view.getByRole('region', { name: '管理' })).toBeInTheDocument();
  expect(view.queryByRole('region', { name: '老功能' })).not.toBeInTheDocument();
});

it('同路由改名项归组：协作组含「对话」「协作群」「发现」，Bot 组含「Bot 管理」', () => {
  const view = renderSidebar();
  const collab = view.getByRole('region', { name: '协作' });
  const bot = view.getByRole('region', { name: '管理' });
  expect(view.getByRole('button', { name: /对话/ })).toBeInTheDocument();
  expect(view.getByRole('button', { name: /协作群/ })).toBeInTheDocument();
  expect(view.getByRole('button', { name: /发现/ })).toBeInTheDocument();
  expect(view.getByRole('button', { name: /Bot 管理/ })).toBeInTheDocument();
  expect(collab.textContent).toContain('对话');
  expect(collab.textContent).toContain('协作群');
  expect(collab.textContent).toContain('发现');
  expect(bot.textContent).toContain('Bot 管理');
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
  expect(screen.getByRole('button', { name: 'Bot 管理' })).toBeInTheDocument();
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

// 折叠态工具簇与导航项同列时需可区分：主次层级（工具钮收 28px(sm) + 静态 muted 色阶，
// hover 恢复全对比度；导航项保持 32px 全对比度）+ 2026-11 收起态规范补充分隔线
// （三钮簇与导航区之间、实验(室)与 Bot 管理之间各一条发丝线）。本用例锁区分信号本身，
// 防后续改动把工具簇悄悄改回与导航同规格；展开态品牌行（52px 行内 96px 图标组）不在此例。
it('折叠态：工具簇图标以 sm 规格 + muted 色阶与导航项区分，导航项维持 32px 全对比度', () => {
  const view = renderSidebar({ collapsed: true });
  const search = view.getByRole('button', { name: '搜索' });
  const expand = view.getByRole('button', { name: '展开导航' });
  for (const btn of [search, expand]) {
    expect(btn).toHaveClass('h-7', 'w-7', 'text-muted-foreground');
  }
  const nav = view.getByRole('button', { name: 'Bot 管理' });
  expect(nav).not.toHaveClass('h-7');
  expect(nav).not.toHaveClass('text-muted-foreground');
});

// extendCapabilities 合并后无法恢复，capability override 用例置于文件末尾。
it('internal overlay：空间切换器以 compact 形态挂在 Bot 分组标题右侧', () => {
  extendCapabilities({
    getShellVisibility: () => ({
      status: 'available',
      value: { spaceSwitcher: true, notificationBell: true, globalSearch: true },
    }),
  });
  const view = renderSidebar();
  const bot = view.getByRole('region', { name: '管理' });
  expect(view.getByTestId('space-switcher-compact')).toBeInTheDocument();
  expect(view.queryByTestId('space-switcher')).not.toBeInTheDocument();
  // compact 切换器位于 Bot 分组标题行内（先于组内导航项出现）
  expect(bot.textContent?.indexOf('选择空间')).toBeLessThan(bot.textContent?.indexOf('Bot 管理') ?? -1);
});

// 折叠态收起规范（2026-11）：置顶品牌 mark 与展开态同源（brand.Logo compact）；三钮纵向次序
// 展开 → 搜索 → 通知；两条发丝分割线（三钮簇与导航区之间、实验(室)与 Bot 管理之间）。
// 本用例承接上例 spaceSwitcher=true、notificationBell=true 的合并形态。
it('折叠态：品牌 mark 置顶，三钮次序 展开 → 搜索 → 通知，工具簇与导航区之间有分割线', () => {
  const view = renderSidebar({ collapsed: true });
  const logo = view.container.querySelector('img[alt="Avernet"]');
  const expand = view.getByRole('button', { name: '展开导航' });
  const search = view.getByRole('button', { name: '搜索' });
  const bell = view.getByTestId('notification-bell');
  expect(logo).toBeInTheDocument();
  // logo 非空已由上方 toBeInTheDocument 保证；写法沿用品牌行单行用例的逻辑收窄 + 保语义模式
  expect(logo && logo.compareDocumentPosition(expand) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(expand.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(search.compareDocumentPosition(bell) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  // 两条发丝分割线：三钮簇与导航区之间（AppSidebar）+ 实验(室)与 Bot 管理之间（SidebarNavList）
  expect(view.container.querySelectorAll('.h-px.bg-divider')).toHaveLength(2);
});

// 收起态空间切换（2026-11 收起态规范）：导航收起时切换器隐藏，仅展开态可切换（上例已验展开态挂载）。
it('折叠态：空间切换器隐藏，不占收起态图标列', () => {
  const view = renderSidebar({ collapsed: true });
  expect(view.queryByTestId('space-switcher-compact')).not.toBeInTheDocument();
  expect(view.queryByTestId('space-switcher')).not.toBeInTheDocument();
});

it('notificationBell=false 形态：通知中心入口不渲染', () => {
  extendCapabilities({
    getShellVisibility: () => ({
      status: 'available',
      value: { spaceSwitcher: false, notificationBell: false, globalSearch: true },
    }),
  });
  renderSidebar();
  expect(screen.queryByTestId('notification-bell')).not.toBeInTheDocument();
});

// globalSearch 门控三处独立（展开钮 / 折叠钮 / GlobalSearchDialog 挂载），拆掉任何一处都必须有测试变红：
// - 两形态渲染均断言「搜索」钮不存在，且通知中心仍在（正控：确认侧栏整体已渲染、非全军覆没）；
// - GlobalSearchDialog 关闭态本身渲染 null（其内部 `if (!open) return null`），DOM 断言无从区分
//   「挂载但关闭」与「未挂载」，故直接 spy 模块导出的组件挂载调用——删除弹窗挂载门时
//   组件以 open=false 挂载，spy 被调用 → 本用例失败而非安静通过。
it('globalSearch=false 形态：两形态搜索钮与搜索弹窗均不渲染挂载', () => {
  const dialogSpy = jest
    .spyOn(GlobalSearchModule, 'GlobalSearchDialog')
    .mockReturnValue(<div data-testid="global-search-dialog-mounted" />);
  extendCapabilities({
    getShellVisibility: () => ({
      status: 'available',
      value: { spaceSwitcher: false, notificationBell: true, globalSearch: false },
    }),
  });
  // 展开态：品牌行搜索钮不渲染，通知中心正控在场
  const expanded = renderSidebar();
  expect(expanded.queryByRole('button', { name: '搜索' })).not.toBeInTheDocument();
  expect(expanded.getByTestId('notification-bell')).toBeInTheDocument();
  expect(dialogSpy).not.toHaveBeenCalled();
  expanded.unmount();
  // 折叠态：图标列搜索钮同样不渲染（独立于展开态的门）
  renderSidebar({ collapsed: true });
  expect(screen.queryByRole('button', { name: '搜索' })).not.toBeInTheDocument();
  expect(screen.getByTestId('notification-bell')).toBeInTheDocument();
  expect(dialogSpy).not.toHaveBeenCalled();
  dialogSpy.mockRestore();
});
