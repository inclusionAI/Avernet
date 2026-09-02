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

const renderSidebar = (area: NavigationArea) =>
  render(<AppSidebar area={area} activePath="/workspace" collapsed={false} items={[]} onNavigate={jest.fn()} />);

// Open Core 默认 capabilities（spaceSwitcher=false）下的新语义：
// 空间切换器为形态级入口（getShellVisibility），默认不渲染；空间数据链路不受影响（initSpaceContext 由 AppShell 触发）。
it('Open 默认:工作区域不展示空间切换器', () => {
  renderSidebar('work');
  expect(screen.queryByTestId('space-switcher')).not.toBeInTheDocument();
});

it('Open 默认:管理区域也不展示空间切换器(spaceSwitcher=false)', () => {
  renderSidebar('manage');
  expect(screen.queryByTestId('space-switcher')).not.toBeInTheDocument();
});

// extendCapabilities 合并后无法恢复，capability override 用例置于文件末尾。
it('internal overlay:管理区域展示空间切换器(spaceSwitcher=true)', () => {
  extendCapabilities({
    getShellVisibility: () => ({
      status: 'available',
      value: { adminEntry: true, spaceSwitcher: true, notificationBell: true },
    }),
  });
  renderSidebar('manage');
  expect(screen.getByTestId('space-switcher')).toBeInTheDocument();
});
