/** @jest-environment jsdom */
import type { UseHumanIdentityResult } from '@/hooks/useHumanIdentity';
import { PersonalInfoDialog } from '@/shell/PersonalInfoDialog';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

// PersonalInfoDialog 单测：账号栏/登录态由 AccountBadge 测试覆盖，这里只测弹窗内容形态与交互
// （collab-permission-entry-migration AC-12~15）。数据面 mock useCollaborationPrivacy 的 currentUser 快照。
let mockIdentity: UseHumanIdentityResult = {
  identity: { userId: '900004', displayName: '验收用户', online: true },
  status: 'ready',
};
let mockPresentation: { preferAuthenticatedUserProfile: boolean; showDepartment: boolean } = {
  preferAuthenticatedUserProfile: false,
  showDepartment: true,
};
const mockPrivacy = {
  loading: false,
  error: null as string | null,
  overview: {
    currentUser: { displayName: '验收用户', employeeNumber: '900004', departmentPath: ['协作平台', '前端组'] },
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
jest.mock('@/hooks/useCollaborationPrivacy', () => ({
  useCollaborationPrivacy: () => mockPrivacy,
}));
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getUserProfilePresentation: () => ({ status: 'available', value: mockPresentation }),
  }),
}));

describe('PersonalInfoDialog（collab-permission-entry-migration AC-12~15）', () => {
  const baseProps = {
    user: { displayName: '验收用户', avatarUrl: undefined },
    canLogout: false,
    isLoggingOut: false,
    onLogout: jest.fn<() => Promise<void>>(),
    onClose: jest.fn(),
  };

  beforeEach(() => {
    mockPresentation = { preferAuthenticatedUserProfile: false, showDepartment: true };
    mockPrivacy.loading = false;
    mockPrivacy.error = null;
    mockPrivacy.busyAction = null;
    mockPrivacy.overview = {
      currentUser: { displayName: '验收用户', employeeNumber: '900004', departmentPath: ['协作平台', '前端组'] },
      organizationOptions: [],
      bots: [],
    };
    jest.clearAllMocks();
  });

  it('internal 形态：展示工号、部门路径与 icon 同步按钮，无退出登录（AC-13/14）', () => {
    render(<PersonalInfoDialog {...baseProps} />);

    expect(screen.getByText('个人信息')).toBeInTheDocument();
    expect(screen.getByText('验收用户')).toBeInTheDocument();
    expect(screen.getByText('工号 900004')).toBeInTheDocument();
    expect(screen.getByText('协作平台 / 前端组')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '同步用户部门信息' })).toBeInTheDocument();
    expect(screen.queryByText('退出登录')).not.toBeInTheDocument();
  });

  it('internal 形态：点击同步触发部门刷新（AC-15）', () => {
    render(<PersonalInfoDialog {...baseProps} />);

    fireEvent.click(screen.getByRole('button', { name: '同步用户部门信息' }));
    expect(mockPrivacy.syncDepartment).toHaveBeenCalledTimes(1);
  });

  it('Open Core 形态：无工号/部门/同步按钮，底部为退出登录（AC-13/14）', () => {
    mockPresentation = { preferAuthenticatedUserProfile: true, showDepartment: false };
    const onLogout = jest.fn<() => Promise<void>>();
    render(<PersonalInfoDialog {...baseProps} canLogout onLogout={onLogout} />);

    expect(screen.getByText('个人信息')).toBeInTheDocument();
    expect(screen.getByText('验收用户')).toBeInTheDocument();
    expect(screen.queryByText(/工号/)).not.toBeInTheDocument();
    expect(screen.queryByText('协作平台 / 前端组')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '同步用户部门信息' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('退出登录'));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it('加载中展示骨架占位（无障碍标签：正在加载个人信息）', () => {
    mockPrivacy.loading = true;
    render(<PersonalInfoDialog {...baseProps} />);

    expect(screen.getByLabelText('正在加载个人信息')).toBeInTheDocument();
  });

  it('加载失败展示错误态与重试入口', () => {
    mockPrivacy.overview = null as never;
    mockPrivacy.error = '网络异常';
    render(<PersonalInfoDialog {...baseProps} />);

    expect(screen.getByText('个人信息加载失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(mockPrivacy.load).toHaveBeenCalledTimes(1);
  });

  it('同步执行中：同步按钮 loading 禁用防重复点击（AC-15）', () => {
    mockPrivacy.busyAction = 'syncDepartment';
    render(<PersonalInfoDialog {...baseProps} />);

    expect(screen.getByRole('button', { name: '同步用户部门信息' }).closest('button')).toBeDisabled();
  });
});
