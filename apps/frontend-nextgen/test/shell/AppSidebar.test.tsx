/** @jest-environment jsdom */
import { extendCapabilities } from '@/capabilities';
import { AppSidebar } from '@/shell/AppSidebar';
import { getMergedNavigationItems } from '@/shell/navigation';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { afterEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

jest.mock('@/shell/SpaceSwitcher', () => ({
  SpaceSwitcher: ({ compact }: { compact?: boolean }) => (
    <div data-testid={compact ? 'space-switcher-compact' : 'space-switcher'}>选择空间</div>
  ),
}));
jest.mock('@/shell/WorkspaceIdentitySwitcher', () => ({
  WorkspaceIdentitySwitcher: ({ collapsed }: { collapsed?: boolean }) => (
    <div data-testid={collapsed ? 'identity-switcher-collapsed' : 'identity-switcher'}>协作身份</div>
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

it('协作 / Bot 双分组 + 老功能过渡组常驻（不随路由整体换组）', () => {
  const view = renderSidebar({ activePath: '/admin' });
  expect(view.getByRole('region', { name: '协作' })).toBeInTheDocument();
  expect(view.getByRole('region', { name: 'Bot' })).toBeInTheDocument();
  expect(view.getByRole('region', { name: '老功能' })).toBeInTheDocument();
});

it('同路由改名项归组：协作组含「对话」「协作群」「发现」，Bot 组含「Bot管理」，二者无 deprecated 徽标', () => {
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

it('legacy 过渡组：任务列表 带「待移除」徽标，协作权限与管理后台入口均不再占导航位', () => {
  const view = renderSidebar();
  const legacy = view.getByRole('region', { name: '老功能' });
  expect(view.getAllByText('待移除')).toHaveLength(1);
  expect(legacy.textContent).toContain('任务列表');
  expect(legacy.textContent).not.toContain('协作权限');
  expect(view.queryByRole('button', { name: /管理后台/ })).toBeNull();
});

it('折叠态：不展示身份切换入口，账号行仍可见，deprecated 项语义入 aria-label', () => {
  renderSidebar({ collapsed: true });
  expect(screen.queryByTestId('identity-switcher-collapsed')).not.toBeInTheDocument();
  expect(screen.getByTestId('account-badge-collapsed')).toBeInTheDocument();
  expect(screen.getByTestId('notification-bell')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Bot管理' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '任务列表（待移除）' })).toBeInTheDocument();
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
