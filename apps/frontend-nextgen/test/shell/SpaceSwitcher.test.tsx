/** @jest-environment jsdom */
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const space = {
  spaceId: 10000,
  spaceCode: 'personal',
  spaceName: '个人空间',
  spaceType: 'PERSONAL' as const,
  joinStatus: 'JOINED' as const,
  currentUserRole: 'ADMIN' as const,
};

const mockedInitSpaceContext = jest.fn(async () => undefined);

jest.mock('@/hooks/useSpaceContext', () => ({
  useSpaceContext: (selector: (state: unknown) => unknown) =>
    selector({ currentSpace: space, currentSpaceId: space.spaceId, spaces: [space], loading: false, error: null }),
  initSpaceContext: mockedInitSpaceContext,
  refreshSpaceContext: jest.fn(async () => undefined),
  switchSpaceContext: jest.fn(),
}));

// split-admin-space-ticket-pages：弹层标题行「空间管理」设置入口的形态开关（getAdminSections.spaces）
let mockSpacesVisible = true;
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getAdminSections: () => ({
      status: 'available',
      value: { spaces: mockSpacesVisible, workOrders: true },
    }),
  }),
}));

const mockNavigate = jest.fn();
jest.mock('@umijs/max', () => ({ useNavigate: () => mockNavigate }));

const { SpaceSwitcher } = require('@/shell/SpaceSwitcher') as typeof import('@/shell/SpaceSwitcher');

describe('SpaceSwitcher', () => {
  it('展示管理空间标题和切换卡片样式', () => {
    render(<SpaceSwitcher />);

    expect(screen.getByText('管理空间')).toBeInTheDocument();
    expect(screen.queryByLabelText('管理空间说明')).not.toBeInTheDocument();
    expect(screen.queryByText('“管理”下所有页面数据均按此空间展示')).not.toBeInTheDocument();
    expect(screen.getByRole('img', { name: '个人空间图标' })).toHaveClass('h-6', 'w-6');
    expect(screen.getByText('个人空间', { selector: 'span.truncate' })).toHaveClass('text-xs');
    expect(screen.getByRole('button', { name: /个人空间/ })).toHaveClass(
      'rounded-lg',
      'border',
      'bg-muted/60',
      'min-h-9',
      'px-2.5',
      'py-1.5',
    );
  });

  it('挂载即触发空间上下文初始化：切换器全局常驻侧栏（协作路由也可见），刷新后不再停留「选择空间」', () => {
    // 新骨架（refactor-global-nav-shell）下 compact 切换器挂在 Bot 分组标题右侧、
    // 不再依赖进入 Bot/管理路由才初始化——读者挂载即拉取，localStorage 中的选择在首屏还原。
    mockedInitSpaceContext.mockClear();
    render(<SpaceSwitcher compact />);
    expect(mockedInitSpaceContext).toHaveBeenCalledTimes(1);
  });

  it('弹层标题行渲染「空间管理」设置入口：点击导航 /space-admin 并关闭气泡', async () => {
    mockSpacesVisible = true;
    mockNavigate.mockClear();
    const view = render(<SpaceSwitcher />);
    fireEvent.click(view.getByRole('button', { name: /个人空间/ }));
    const settings = await screen.findByRole('button', { name: '空间管理' });
    fireEvent.click(settings);
    expect(mockNavigate).toHaveBeenCalledWith('/space-admin');
  });

  it('Open Core（spaces=false）形态：弹层不渲染空间管理设置入口', async () => {
    mockSpacesVisible = false;
    const view = render(<SpaceSwitcher />);
    fireEvent.click(view.getByRole('button', { name: /个人空间/ }));
    await screen.findByText('空间切换');
    expect(screen.queryByRole('button', { name: '空间管理' })).toBeNull();
  });
});
