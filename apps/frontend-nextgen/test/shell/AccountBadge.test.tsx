/** @jest-environment jsdom */
import type { UseAccountLogoutResult } from '@/hooks/useAccountLogout';
import type { UseHumanIdentityResult } from '@/hooks/useHumanIdentity';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

// 账号栏组件测试只关心「按 canLogout 分支渲染/触发退出菜单」；登录态与退出编排分别在
// useHumanIdentity / useExternalAuth（经 useAccountLogout 收口）自身测试覆盖，这里 mock 隔离。
let mockIdentity: UseHumanIdentityResult = { identity: null, status: 'error' };
let mockAccountLogout: UseAccountLogoutResult;

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => mockIdentity,
}));
jest.mock('@/hooks/useAccountLogout', () => ({
  useAccountLogout: () => mockAccountLogout,
}));

// 动态 import 以确保 mock 生效后再拉组件
const { AccountBadge } = require('@/shell/AccountBadge') as typeof import('@/shell/AccountBadge');

const READY_IDENTITY: UseHumanIdentityResult = {
  identity: { userId: 'u_1', displayName: '验收用户', online: true },
  status: 'ready',
};

describe('AccountBadge', () => {
  beforeEach(() => {
    mockIdentity = { identity: null, status: 'error' };
    mockAccountLogout = { canLogout: false, isLoggingOut: false, logout: jest.fn<() => Promise<void>>() };
  });

  it('显示当前登录用户名称和真实头像，但不显示用户状态', () => {
    render(<AccountBadge currentUser={{ displayName: '验收用户', avatarUrl: 'https://avatar.example/user.png' }} />);

    expect(screen.getByText('验收用户')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '验收用户' })).toHaveAttribute('src', 'https://avatar.example/user.png');
    expect(screen.queryByText('在线')).not.toBeInTheDocument();
    expect(screen.queryByText('离线')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('用户在线')).not.toBeInTheDocument();
    expect(screen.queryByText('张三')).not.toBeInTheDocument();
  });

  it('当前用户信息缺失时使用安全降级文案且不显示状态', () => {
    render(<AccountBadge currentUser={null} />);

    expect(screen.getByText('当前用户')).toBeInTheDocument();
    expect(screen.queryByText('在线')).not.toBeInTheDocument();
    expect(screen.queryByText('离线')).not.toBeInTheDocument();
    expect(screen.queryByText('张三')).not.toBeInTheDocument();
  });

  it('退出登录入口（Open Core 形态）：点头像出现菜单，点击退出登录触发 logout', () => {
    mockAccountLogout = { canLogout: true, isLoggingOut: false, logout: jest.fn<() => Promise<void>>() };
    render(<AccountBadge currentUser={{ displayName: '验收用户' }} />);

    expect(screen.queryByText('退出登录')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('验收用户'));
    expect(screen.getByText('退出登录')).toBeInTheDocument();

    fireEvent.click(screen.getByText('退出登录'));
    expect(mockAccountLogout.logout).toHaveBeenCalledTimes(1);
  });

  it('退出执行中（isLoggingOut）：退出登录行 disabled', () => {
    mockAccountLogout = { canLogout: true, isLoggingOut: true, logout: jest.fn<() => Promise<void>>() };
    render(<AccountBadge currentUser={{ displayName: '验收用户' }} />);

    fireEvent.click(screen.getByText('验收用户'));
    expect(screen.getByText('退出登录').closest('button')).toBeDisabled();
  });

  it('内容形态 ace-gateway（canLogout=false）：点头像不出现退出登录', () => {
    render(<AccountBadge currentUser={{ displayName: '验收用户' }} />);

    fireEvent.click(screen.getByText('验收用户'));
    expect(screen.queryByText('退出登录')).not.toBeInTheDocument();
  });

  it('hook 路径（不传 currentUser）：ready 身份时同样开放退出菜单', () => {
    mockAccountLogout = { canLogout: true, isLoggingOut: false, logout: jest.fn<() => Promise<void>>() };
    mockIdentity = READY_IDENTITY;
    render(<AccountBadge />);

    fireEvent.click(screen.getByText('验收用户'));
    expect(screen.getByText('退出登录')).toBeInTheDocument();
  });

  it('hook 路径未登录（error）：维持「未登录」占位且无退出菜单', () => {
    mockAccountLogout = { canLogout: true, isLoggingOut: false, logout: jest.fn<() => Promise<void>>() };
    render(<AccountBadge />);

    expect(screen.getByText('未登录')).toBeInTheDocument();
    expect(screen.queryByText('退出登录')).not.toBeInTheDocument();
  });
});
