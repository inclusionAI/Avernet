/** @jest-environment jsdom */
import { extendCapabilities } from '@/capabilities';
import { AppSidebar } from '@/shell/AppSidebar';
import { navigationItems, type NavigationArea } from '@/shell/navigation';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { afterEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

jest.mock('@/shell/SpaceSwitcher', () => ({
  SpaceSwitcher: () => <div data-testid="space-switcher">选择空间</div>,
}));
jest.mock('@/shell/WorkspaceIdentitySwitcher', () => ({
  WorkspaceIdentitySwitcher: () => <div data-testid="identity-switcher">协作身份</div>,
}));

const renderSidebar = (area: NavigationArea) =>
  render(<AppSidebar area={area} activePath="/workspace" collapsed={false} items={[]} onNavigate={jest.fn()} />);

afterEach(() => {
  useWorkspaceStore.getState().reset();
});

it('工作区域在导航顶部展示协作身份入口，不展示空间切换器', () => {
  renderSidebar('work');
  const nav = screen.getByRole('navigation', { name: '工作导航' });
  expect(screen.getByTestId('identity-switcher')).toBeInTheDocument();
  expect(screen.queryByTestId('space-switcher')).not.toBeInTheDocument();
  expect(nav.textContent?.indexOf('协作身份')).toBeLessThan(nav.textContent?.indexOf('工作') ?? -1);
});

it('Open 默认:管理区域不展示空间切换器或协作身份入口', () => {
  renderSidebar('manage');
  expect(screen.queryByTestId('space-switcher')).not.toBeInTheDocument();
  expect(screen.queryByTestId('identity-switcher')).not.toBeInTheDocument();
});

it('用户工作身份在展开与折叠导航中展示我的任务', () => {
  useWorkspaceStore.setState({
    activeIdentityId: 'human-1',
    identities: [{ id: 'human-1', kind: 'user', displayName: '真实用户', online: true }],
  });

  const expanded = render(
    <AppSidebar area="work" activePath="/workspace" collapsed={false} items={navigationItems} onNavigate={jest.fn()} />,
  );
  expect(screen.getByRole('button', { name: '我的任务' })).toBeInTheDocument();
  expanded.unmount();

  render(<AppSidebar area="work" activePath="/workspace" collapsed items={navigationItems} onNavigate={jest.fn()} />);
  expect(screen.getByRole('button', { name: '我的任务' })).toBeInTheDocument();
});

it('Bot 工作身份在展开与折叠导航中继续展示我的任务', () => {
  useWorkspaceStore.setState({
    activeIdentityId: 'bot-1:447147',
    identities: [{ id: 'bot-1:447147', kind: 'bot', displayName: 'Bot A', online: true }],
  });

  const expanded = render(
    <AppSidebar area="work" activePath="/workspace" collapsed={false} items={navigationItems} onNavigate={jest.fn()} />,
  );
  expect(screen.getByRole('button', { name: '我的任务' })).toBeInTheDocument();
  expanded.unmount();

  render(<AppSidebar area="work" activePath="/workspace" collapsed items={navigationItems} onNavigate={jest.fn()} />);
  expect(screen.getByRole('button', { name: '我的任务' })).toBeInTheDocument();
});

// extendCapabilities 合并后无法恢复，capability override 用例置于文件末尾。
it('internal overlay:管理区域在导航顶部展示空间切换器', () => {
  extendCapabilities({
    getShellVisibility: () => ({
      status: 'available',
      value: { adminEntry: true, spaceSwitcher: true, notificationBell: true },
    }),
  });
  renderSidebar('manage');
  const nav = screen.getByRole('navigation', { name: '管理导航' });
  expect(screen.getByTestId('space-switcher')).toBeInTheDocument();
  expect(screen.queryByTestId('identity-switcher')).not.toBeInTheDocument();
  expect(nav.textContent?.indexOf('选择空间')).toBeLessThan(nav.textContent?.indexOf('管理') ?? -1);
});
