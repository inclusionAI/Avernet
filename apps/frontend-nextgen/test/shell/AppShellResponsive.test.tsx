/** @jest-environment jsdom */
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

const mockHistoryPush = jest.fn();
const mockHistoryReplace = jest.fn();
const mockLocation = { pathname: '/workspace' };
// 受控视口：true=≥lg（桌面内流侧栏可视），false=<lg（一级导航走抽屉）。
const mockViewport = { desktop: false };

jest.mock('@umijs/max', () => ({
  history: {
    push: (...args: unknown[]) => mockHistoryPush(...args),
    replace: (...args: unknown[]) => mockHistoryReplace(...args),
  },
  useLocation: () => mockLocation,
}));
jest.mock('@/hooks/useMediaQuery', () => ({
  useMinWidth: () => mockViewport.desktop,
  useMediaQuery: () => mockViewport.desktop,
}));
jest.mock('@/hooks/useSpaceContext', () => ({
  initSpaceContext: jest.fn(async () => undefined),
  ensurePersonalSpaceOnAppEntry: jest.fn(async () => undefined),
}));
jest.mock('@/services/workspace/workspaceService', () => ({
  workspaceService: {
    initWorkspace: jest.fn(async () => ({ ok: true as const, data: { defaultActiveId: null } })),
  },
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
// 内流一级侧栏桩化（避免 <lg 时内流与抽屉重复渲染同名导航项）；抽屉内容用的是真实 SidebarNavList。
// 汉堡入口随 AppHeader 退役迁为 AppShell 内悬浮按钮，不再依赖顶栏 mock。
jest.mock('@/shell/AppSidebar', () => ({ AppSidebar: () => <aside data-testid="app-sidebar" /> }));
jest.mock('@/shell/SpaceSwitcher', () => ({ SpaceSwitcher: () => <div data-testid="space-switcher" /> }));

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
const { PrimaryNavToggle } = require('@/components/Workspace/PrimaryNav') as typeof import('@/components/Workspace/PrimaryNav');

function renderShell() {
  return render(
    <AppShell>
      <div data-testid="page" />
    </AppShell>,
  );
}

/** chat/group 页头形态的子树：真实 PrimaryNavToggle + 本地「打开会话列表」代表钮。 */
const headerLikeChildren = (
  <>
    <PrimaryNavToggle />
    <button type="button" aria-label="打开会话列表" />
  </>
);

beforeEach(() => {
  mockLocation.pathname = '/workspace';
  mockHistoryPush.mockClear();
  mockHistoryReplace.mockClear();
  useWorkspaceStore.getState().reset();
});

describe('AppShell <lg 一级导航入口条（内流占位）', () => {
  it('导航入口以正常流条带渲染并先于内容（非绝对定位浮层），不再压住页面自身顶栏控件', () => {
    mockViewport.desktop = false;
    renderShell();
    const navToggle = screen.getByRole('button', { name: '打开导航' });
    const strip = navToggle.parentElement as HTMLElement;
    // 口径：入口条自带占位高度（正常流）——修复前为 absolute 浮层，
    // 与页面内容（如群聊/单聊顶栏「打开会话列表」箭头）重叠导致点击冲突。
    expect(strip.className).not.toContain('absolute');
    // 高度预留仅限 <lg：桌面内流侧栏可见时条带隐藏（lg:hidden）不占高度。
    expect(strip.className).toContain('lg:hidden');
    // 兜底条带Marker：页头内联入口登记后本条被抑制（见下方内联入口用例）。
    expect(strip).toHaveAttribute('data-testid', 'primary-nav-fallback-strip');
    // 条带先于内容渲染：内容相对条带为后续兄弟节点，正常流中各自占位。
    const page = screen.getByTestId('page');
    expect(strip.compareDocumentPosition(page) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('AppShell responsive off-canvas nav (一级)', () => {
  it('below lg: hamburger opens the nav drawer', () => {
    mockViewport.desktop = false;
    renderShell();
    expect(screen.getByRole('button', { name: '打开导航' })).toBeInTheDocument();
    expect(screen.queryByTestId('drawer-content')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '打开导航' }));
    expect(screen.getByTestId('drawer-content')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '对话' })).toBeInTheDocument();
  });

  it('selecting a nav item navigates and closes the drawer', () => {
    mockViewport.desktop = false;
    mockHistoryPush.mockClear();
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: '打开导航' }));
    fireEvent.click(screen.getByRole('button', { name: '对话' }));

    expect(mockHistoryPush).toHaveBeenCalledWith('/workspace/chat');
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

describe('AppShell 移动抽屉隐藏全局工作身份', () => {
  it('低于 lg：打开真实发现群组导航抽屉时不渲染工作身份切换器，导航菜单仍存在', () => {
    mockViewport.desktop = false;
    mockLocation.pathname = '/collaboration-square/groups';

    renderShell();
    fireEvent.click(screen.getByRole('button', { name: '打开导航' }));

    expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发现' })).toBeInTheDocument();
    expect(screen.queryByTestId('identity-switcher')).not.toBeInTheDocument();
  });

  it('低于 lg：我的任务路由打开抽屉也不渲染工作身份切换器', () => {
    mockViewport.desktop = false;
    mockLocation.pathname = '/work/my-task';
    useWorkspaceStore.setState({
      activeIdentityId: 'human-1',
      identities: [{ id: 'human-1', kind: 'user', displayName: '真实用户', online: true }],
    });

    renderShell();
    fireEvent.click(screen.getByRole('button', { name: '打开导航' }));

    expect(screen.queryByTestId('identity-switcher')).not.toBeInTheDocument();
  });
});

describe('AppShell 移动导航不展示全局工作身份', () => {
  it.each(['/ticket-center', '/work/my-task'])('%s 导航抽屉保留菜单但无全局身份入口', (pathname) => {
    mockViewport.desktop = false;
    mockLocation.pathname = pathname;
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: '打开导航' }));
    expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '对话' })).toBeInTheDocument();
    expect(screen.queryByTestId('identity-switcher')).not.toBeInTheDocument();
  });
});

describe('AppShell 工作身份路由保护', () => {
  it('Bot 工作身份可直访我的任务', () => {
    mockLocation.pathname = '/work/my-task';
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:900004',
      identities: [{ id: 'bot-1:900004', kind: 'bot', displayName: 'Bot A', online: true }],
    });

    renderShell();

    expect(mockHistoryReplace).not.toHaveBeenCalled();
    expect(screen.getByTestId('page')).toBeInTheDocument();
  });

  it('Bot 工作身份直访公开协作群时不再重定向，页面正常挂载（Tab 恒显，操作身份固定登录用户）', () => {
    mockLocation.pathname = '/collaboration-square/groups';
    useWorkspaceStore.setState({
      activeIdentityId: 'bot-1:900004',
      identities: [{ id: 'bot-1:900004', kind: 'bot', displayName: 'Bot A', online: true }],
    });

    renderShell();

    expect(mockHistoryReplace).not.toHaveBeenCalled();
    expect(screen.getByTestId('page')).toBeInTheDocument();
  });

  it('用户工作身份可访问我的任务与公开协作群', () => {
    useWorkspaceStore.setState({
      activeIdentityId: 'human-1',
      identities: [{ id: 'human-1', kind: 'user', displayName: '真实用户', online: true }],
    });
    mockLocation.pathname = '/work/my-task';

    const view = renderShell();

    expect(mockHistoryReplace).not.toHaveBeenCalled();
    expect(screen.getByTestId('page')).toBeInTheDocument();

    mockLocation.pathname = '/collaboration-square/groups';
    view.rerender(
      <AppShell>
        <div data-testid="page" />
      </AppShell>,
    );
    expect(mockHistoryReplace).not.toHaveBeenCalled();
    expect(screen.getByTestId('page')).toBeInTheDocument();
  });
});

describe('AppShell 页头内联一级导航入口（2026-10-19 用户反馈：独立条带与页头双行 → 同排单行）', () => {
  it('<lg：导航入口并入页头行、位于「打开会话列表」左侧，h-12 兜底条带不再渲染', () => {
    mockViewport.desktop = false;
    mockLocation.pathname = '/workspace/chat';
    render(<AppShell>{headerLikeChildren}</AppShell>);

    const navEntry = screen.getByRole('button', { name: '打开导航' });
    // 规格对齐页头 Chevron：28px(sm) 图标钮、桌面内流侧栏可见时不占位。
    expect(navEntry).toHaveClass('h-7', 'w-7', 'lg:hidden');
    // 排序在「打开会话列表」之前（同一行内、最左）。
    const sessionListEntry = screen.getByRole('button', { name: '打开会话列表' });
    expect(navEntry.compareDocumentPosition(sessionListEntry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // 页头登记了内联入口：壳的 h-12 兜底条带被抑制（无独立条带占位）。
    expect(screen.queryByTestId('primary-nav-fallback-strip')).not.toBeInTheDocument();
  });

  it('<lg：内联入口打开壳同一抽屉，选中导航项后跳转并收起', () => {
    mockViewport.desktop = false;
    mockLocation.pathname = '/workspace/chat';
    render(<AppShell>{headerLikeChildren}</AppShell>);
    expect(screen.queryByTestId('drawer-content')).not.toBeInTheDocument();

    // 内联入口与原条带入口共用 AppShell 内的同一条抽屉（含真实 SidebarNavList）。
    fireEvent.click(screen.getByRole('button', { name: '打开导航' }));
    expect(screen.getByTestId('drawer-content')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '对话' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '对话' }));
    expect(mockHistoryPush).toHaveBeenCalledWith('/workspace/chat');
    expect(screen.queryByTestId('drawer-content')).not.toBeInTheDocument();
  });

  it('chat 页头卸载后（空 Bot/引导等无页头阶段）兜底条带回归，一级导航不缺失', () => {
    mockViewport.desktop = false;
    mockLocation.pathname = '/workspace/chat';
    const view = render(<AppShell>{headerLikeChildren}</AppShell>);
    expect(screen.queryByTestId('primary-nav-fallback-strip')).not.toBeInTheDocument();

    view.rerender(
      <AppShell>
        <div data-testid="page" />
      </AppShell>,
    );
    // 无页头路由同样保留一级导航入口：登记清零后条带回归。
    expect(screen.getByTestId('primary-nav-fallback-strip')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '打开导航' })).toBeInTheDocument();
  });

  it('精简路由（navigationVisibility=hidden）：页头内联入口为 null，零登记零占位', () => {
    mockViewport.desktop = false;
    mockLocation.pathname = '/workspace/collaboration/group';
    render(<AppShell>{headerLikeChildren}</AppShell>);

    expect(screen.queryByRole('button', { name: '打开导航' })).not.toBeInTheDocument();
    // 「打开会话列表」仍按页头自身装配渲染（壳不干预页头本地入口）。
    expect(screen.getByRole('button', { name: '打开会话列表' })).toBeInTheDocument();
  });

  it('>=lg：内联入口显示类为 lg:hidden，桌面零额外占位、抽屉关闭', () => {
    mockViewport.desktop = true;
    mockLocation.pathname = '/workspace/chat';
    render(<AppShell>{headerLikeChildren}</AppShell>);

    const navEntry = screen.getByRole('button', { name: '打开导航' });
    expect(navEntry).toHaveClass('lg:hidden');
    expect(screen.getByTestId('app-sidebar')).toBeInTheDocument();
    expect(screen.queryByTestId('drawer-content')).not.toBeInTheDocument();
  });
});
