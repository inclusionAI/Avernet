/** @jest-environment jsdom */
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

const mockHistoryPush = jest.fn();
// 受控视口：true=≥lg（桌面内流侧栏可视），false=<lg（一级导航走抽屉）。
const mockViewport = { desktop: false };

jest.mock('@umijs/max', () => ({
  history: { push: (...args: unknown[]) => mockHistoryPush(...args) },
  useLocation: () => ({ pathname: '/workspace' }),
}));
jest.mock('@/hooks/useMediaQuery', () => ({
  useMinWidth: () => mockViewport.desktop,
  useMediaQuery: () => mockViewport.desktop,
}));
jest.mock('@/hooks/useSpaceContext', () => ({
  initSpaceContext: jest.fn(async () => undefined),
  ensurePersonalSpaceOnAppEntry: jest.fn(async () => undefined),
}));
jest.mock('@/services/workspace/identityService', () => ({
  identityService: {
    loadIdentities: jest.fn(async () => ({
      ok: true as const,
      data: { identities: [], defaultActiveId: null },
    })),
    // AppShell 经 useHumanIdentity 调用这两个状态访问器推导 loading/ready/error。
    isIdentityLoading: jest.fn(() => false),
    isIdentityResolved: jest.fn(() => false),
  },
}));
// AppHeader 真实渲染（含汉堡按钮），但其重叶子组件桩化以免拉起真实服务。
jest.mock('@/components/Admin/NotificationBell', () => ({ NotificationBell: () => <div data-testid="notif" /> }));
jest.mock('@/shell/AccountBadge', () => ({ AccountBadge: () => <div data-testid="account" /> }));
jest.mock('@/shell/HelpMenu', () => ({ HelpMenu: () => <div data-testid="help" /> }));
// 内流一级侧栏桩化（避免 <lg 时内流与抽屉重复渲染同名导航项）；抽屉内容用的是真实 SidebarNavList。
jest.mock('@/shell/AppSidebar', () => ({ AppSidebar: () => <aside data-testid="app-sidebar" /> }));
jest.mock('@/shell/SpaceSwitcher', () => ({ SpaceSwitcher: () => <div data-testid="space-switcher" /> }));
jest.mock('@/shell/WorkspaceIdentitySwitcher', () => ({
  WorkspaceIdentitySwitcher: () => <div data-testid="identity-switcher" />,
}));

// Drawer 原语桩化为受控组件：open=true 渲染 children，open=false 渲染 null。
// 规避 Radix Dialog 在 jsdom 下退出动画不触发导致内容不卸载的问题。
jest.mock('@/components/ui', () => {
  const actual = jest.requireActual<typeof import('@/components/ui')>('@/components/ui');
  return {
    ...actual,
    Drawer: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
      open ? <div data-testid="drawer">{children}</div> : null,
    DrawerContent: ({ children, bodyClassName }: { children: React.ReactNode; bodyClassName?: string }) => (
      <div data-testid="drawer-content" className={bodyClassName}>
        {children}
      </div>
    ),
    DrawerTitle: ({ children }: { children: React.ReactNode }) => <span data-testid="drawer-title">{children}</span>,
  };
});

const { AppShell } = require('@/shell/AppShell') as typeof import('@/shell/AppShell');

function renderShell() {
  return render(
    <AppShell>
      <div data-testid="page" />
    </AppShell>,
  );
}

describe('AppShell responsive off-canvas nav (一级)', () => {
  it('below lg: hamburger opens the nav drawer', () => {
    mockViewport.desktop = false;
    renderShell();
    expect(screen.getByRole('button', { name: '打开导航' })).toBeInTheDocument();
    expect(screen.queryByTestId('drawer-content')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '打开导航' }));
    expect(screen.getByTestId('drawer-content')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '对话协作' })).toBeInTheDocument();
  });

  it('selecting a nav item navigates and closes the drawer', () => {
    mockViewport.desktop = false;
    mockHistoryPush.mockClear();
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: '打开导航' }));
    fireEvent.click(screen.getByRole('button', { name: '对话协作' }));

    expect(mockHistoryPush).toHaveBeenCalledWith('/workspace');
    expect(screen.queryByTestId('drawer-content')).not.toBeInTheDocument();
  });

  it('auto-closes the drawer when the viewport widens to >=lg', () => {
    mockViewport.desktop = false;
    const view = renderShell();
    fireEvent.click(screen.getByRole('button', { name: '打开导航' }));
    expect(screen.getByTestId('drawer-content')).toBeInTheDocument();

    // 视口回到桌面：useMinWidth(1024) 返回 true → AppShell 副作用收起抽屉。
    mockViewport.desktop = true;
    view.rerender(
      <AppShell>
        <div data-testid="page" />
      </AppShell>,
    );
    expect(screen.queryByTestId('drawer-content')).not.toBeInTheDocument();
  });

  it('at >=lg: in-flow sidebar renders and the drawer starts closed', () => {
    mockViewport.desktop = true;
    renderShell();
    expect(screen.getByTestId('app-sidebar')).toBeInTheDocument();
    expect(screen.queryByTestId('drawer-content')).not.toBeInTheDocument();
  });
});
