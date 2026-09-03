/** @jest-environment jsdom */
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ExternalAuthUser } from '@/stores/externalAuthStore';

jest.mock('@/services/workspace/identityService', () => ({
  identityService: {
    loadIdentities: jest.fn(),
    isIdentityLoading: jest.fn(() => false),
    isIdentityResolved: jest.fn(() => false),
  },
}));

// require 动态取模块：确保取到 mock 后的 identityService 与真实 store（对齐 shell 测试风格）
const { useHumanIdentity } = require('@/hooks/useHumanIdentity') as typeof import('@/hooks/useHumanIdentity');
const { identityService } = require('@/services/workspace/identityService') as typeof import('@/services/workspace/identityService');
const { useExternalAuthStore } = require('@/stores/externalAuthStore') as typeof import('@/stores/externalAuthStore');
const { useWorkspaceStore } = require('@/stores/workspaceStore') as typeof import('@/stores/workspaceStore');

const MINE_HUMAN = { id: 'human_tc01', kind: 'user' as const, displayName: 'mine占位名', online: true };

const OAUTH_USER: ExternalAuthUser = {
  userId: 'Asbku1dJX8Pe',
  displayName: '福惠',
  provider: 'alipay',
  avatarUrl: 'https://tfs.example/avatar.png',
};

describe('useHumanIdentity', () => {
  beforeEach(() => {
    jest.mocked(identityService.loadIdentities).mockReset();
    jest.mocked(identityService.isIdentityLoading).mockReturnValue(false);
    jest.mocked(identityService.isIdentityResolved).mockReturnValue(false);
    useWorkspaceStore.getState().reset();
    useExternalAuthStore.getState().reset();
  });

  it('mine 落位后返回 mine human 身份（ready）', async () => {
    jest.mocked(identityService.loadIdentities).mockResolvedValue({
      ok: true,
      data: { identities: [MINE_HUMAN], defaultActiveId: MINE_HUMAN.id },
    });

    const { result } = renderHook(() => useHumanIdentity());

    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.identity?.displayName).toBe('mine占位名');
  });

  it('登录回归：/auth/user 晚于 mine 落位时，身份立即刷出外部用户（此前需切 tab 才纠正）', async () => {
    jest.mocked(identityService.loadIdentities).mockResolvedValue({
      ok: true,
      data: { identities: [MINE_HUMAN], defaultActiveId: MINE_HUMAN.id },
    });

    const { result } = renderHook(() => useHumanIdentity());
    await waitFor(() => expect(result.current.identity?.displayName).toBe('mine占位名'));

    // capability 契约：externalAuthStore.user 优先于 mine 兜底；订阅该 store 应驱动重算。
    act(() => {
      useExternalAuthStore.getState().setAuthenticated(OAUTH_USER);
    });

    await waitFor(() => expect(result.current.identity?.displayName).toBe('福惠'));
    expect(result.current.identity?.avatarUrl).toBe(OAUTH_USER.avatarUrl);
  });

  it('登录回归：mine 失败（error）后 /auth/user 落位，无需重试即可恢复 ready（阿里云 mine 不可用场景）', async () => {
    jest.mocked(identityService.loadIdentities).mockResolvedValue({
      ok: false,
      error: { code: 'IDENTITY_LOAD_FAILED', friendlyMessage: '加载可协作身份失败，请稍后重试。', canRetry: true },
    });

    const { result } = renderHook(() => useHumanIdentity());
    await waitFor(() => expect(result.current.status).toBe('error'));

    act(() => {
      useExternalAuthStore.getState().setAuthenticated(OAUTH_USER);
    });

    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.identity?.displayName).toBe('福惠');
  });

  it('登录态先就绪（刷新已登录页）：首帧即外部用户身份，不回退 mine 兜底', async () => {
    useExternalAuthStore.getState().setAuthenticated(OAUTH_USER);
    // mine 已完成加载并把兜底身份写入 store
    useWorkspaceStore.getState().setIdentities([MINE_HUMAN], MINE_HUMAN.id);

    const { result } = renderHook(() => useHumanIdentity());

    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.identity?.displayName).toBe('福惠');
  });
});
