/** @jest-environment jsdom */
import { extendCapabilities } from '@/capabilities';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/components/Admin/NotificationBell', () => ({
  NotificationBell: () => <div data-testid="notification-bell" />,
}));
jest.mock('@/shell/AccountBadge', () => ({ AccountBadge: () => <div data-testid="account-badge" /> }));
jest.mock('@/shell/HelpMenu', () => ({ HelpMenu: () => <div data-testid="help-menu" /> }));

const { AppHeader } = require('@/shell/AppHeader') as typeof import('@/shell/AppHeader');

describe('AppHeader product area switcher', () => {
  it('uses the segmented control radius hierarchy and marks the active area', () => {
    render(
      <AppHeader
        area="work"
        sidebarCollapsed={false}
        onAreaChange={jest.fn()}
        onToggleSidebar={jest.fn()}
        onOpenMobileNav={jest.fn()}
      />,
    );

    const productArea = screen.getByRole('navigation', { name: '产品区域' });
    const work = screen.getByRole('button', { name: '工作' });
    const manage = screen.getByRole('button', { name: '管理' });

    expect(productArea).toHaveClass('rounded-md', 'p-[3px]');
    expect(productArea).not.toHaveClass('rounded-lg');
    expect(work).toHaveClass('h-[30px]', 'rounded', 'bg-background', 'text-primary');
    expect(work).not.toHaveClass('rounded-lg');
    expect(manage).toHaveClass('h-[30px]', 'rounded', 'text-muted-foreground');
    expect(manage).not.toHaveClass('bg-background');
  });

  it('changes the selected product area when the inactive option is clicked', () => {
    const onAreaChange = jest.fn();
    render(
      <AppHeader
        area="work"
        sidebarCollapsed={false}
        onAreaChange={onAreaChange}
        onToggleSidebar={jest.fn()}
        onOpenMobileNav={jest.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '管理' }));

    expect(onAreaChange).toHaveBeenCalledWith('manage');
  });
});

describe('AppHeader 通知中心门控（getShellVisibility.notificationBell）', () => {
  const renderHeader = () =>
    render(
      <AppHeader
        area="work"
        sidebarCollapsed={false}
        onAreaChange={jest.fn()}
        onToggleSidebar={jest.fn()}
        onOpenMobileNav={jest.fn()}
      />,
    );

  it('Open Core 默认（notificationBell=true）：渲染通知铃铛，HelpMenu/AccountBadge 保留', () => {
    renderHeader();
    expect(screen.getByTestId('notification-bell')).toBeInTheDocument();
    expect(screen.getByTestId('help-menu')).toBeInTheDocument();
    expect(screen.getByTestId('account-badge')).toBeInTheDocument();
  });

  // extendCapabilities 合并后无法恢复，capability override 用例置于文件末尾（沿用 WelcomePage 测试约定）。
  it('internal overlay（notificationBell=true）：渲染通知铃铛', () => {
    extendCapabilities({
      getShellVisibility: () => ({
        status: 'available',
        value: { adminEntry: true, spaceSwitcher: true, notificationBell: true },
      }),
    });
    renderHeader();
    expect(screen.getByTestId('notification-bell')).toBeInTheDocument();
  });
});
