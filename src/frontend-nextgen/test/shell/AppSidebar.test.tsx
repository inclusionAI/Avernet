/** @jest-environment jsdom */
import { extendCapabilities } from '@/capabilities';
import { AppSidebar } from '@/shell/AppSidebar';
import type { NavigationArea } from '@/shell/navigation';
import { expect, it, jest } from '@jest/globals';
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
