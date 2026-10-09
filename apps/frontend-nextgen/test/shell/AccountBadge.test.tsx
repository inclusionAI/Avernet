/** @jest-environment jsdom */
import type { UseAccountLogoutResult } from '@/hooks/useAccountLogout';
import type { UseHumanIdentityResult } from '@/hooks/useHumanIdentity';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

// 账号栏测试只关心「点击打开个人信息弹窗 / 退出入口在弹窗内」（collab-permission-entry-migration
// AC-12~14：原 Popover 退出菜单退役，内外统一弹窗交互）；登录态与退出编排分别在
// useHumanIdentity / useExternalAuth 自身测试覆盖；弹窗内容形态/同步细节在 PersonalInfoDialog.test 覆盖。
let mockIdentity: UseHumanIdentityResult = { identity: null, status: 'error' };
let mockAccountLogout: UseAccountLogoutResult;
const mockPrivacy = {
  loading: false,
  error: null as string | null,
  overview: {
    currentUser: { displayName: '验收用户', employeeNumber: '900004', departmentPath: ['协作平台'] },
    organizationOptions: [],
    bots: [],
  },
  busyAction: null as string | null,
  syncDepartment: jest.fn(),
  load: jest.fn(),
};

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => mockIdentity,
}));
jest.mock('@/hooks/useAccountLogout', () => ({
  useAccountLogout: () => mockAccountLogout,
}));
jest.mock('@/hooks/useCollaborationPrivacy', () => ({
  useCollaborationPrivacy: () => mockPrivacy,
}));
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getUserProfilePresentation: () => ({
      status: 'available',
      value: { preferAuthenticatedUserProfile: false, showDepartment: true },
    }),
  }),
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
    mockPrivacy.loading = false;
    mockPrivacy.error = null;
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

  it('Open Core 形态：点头像打开「个人信息」弹窗，退出登录唯一入口在弹窗内（AC-12/14）', () => {
    mockAccountLogout = { canLogout: true, isLoggingOut: false, logout: jest.fn<() => Promise<void>>() };
    render(<AccountBadge currentUser={{ displayName: '验收用户' }} />);

    expect(screen.queryByText('退出登录')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('验收用户'));
    expect(screen.getByText('个人信息')).toBeInTheDocument();
    expect(screen.getByText('退出登录')).toBeInTheDocument();

    fireEvent.click(screen.getByText('退出登录'));
    expect(mockAccountLogout.logout).toHaveBeenCalledTimes(1);
  });

  it('退出执行中（isLoggingOut）：弹窗内退出登录按钮 disabled', () => {
    mockAccountLogout = { canLogout: true, isLoggingOut: true, logout: jest.fn<() => Promise<void>>() };
    render(<AccountBadge currentUser={{ displayName: '验收用户' }} />);

    fireEvent.click(screen.getByText('验收用户'));
    expect(screen.getByText('退出登录').closest('button')).toBeDisabled();
  });

  it('internal 形态（canLogout=false）：点头像同样打开弹窗，弹窗内无退出登录（AC-12/13）', () => {
    render(<AccountBadge currentUser={{ displayName: '验收用户' }} />);

    fireEvent.click(screen.getByText('验收用户'));
    expect(screen.getByText('个人信息')).toBeInTheDocument();
    expect(screen.queryByText('退出登录')).not.toBeInTheDocument();
  });

  it('hook 路径（不传 currentUser）：ready 身份时同样打开个人信息弹窗', () => {
    mockAccountLogout = { canLogout: true, isLoggingOut: false, logout: jest.fn<() => Promise<void>>() };
    mockIdentity = READY_IDENTITY;
    render(<AccountBadge />);

    fireEvent.click(screen.getByText('验收用户'));
    expect(screen.getByText('个人信息')).toBeInTheDocument();
    expect(screen.getByText('退出登录')).toBeInTheDocument();
  });

  it('hook 路径未登录（error）：维持「未登录」占位，点击不打开弹窗', () => {
    mockAccountLogout = { canLogout: true, isLoggingOut: false, logout: jest.fn<() => Promise<void>>() };
    render(<AccountBadge />);

    expect(screen.getByText('未登录')).toBeInTheDocument();
    expect(screen.queryByText('个人信息')).not.toBeInTheDocument();
  });
});
