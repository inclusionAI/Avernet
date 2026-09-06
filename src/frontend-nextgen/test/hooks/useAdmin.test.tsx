/** @jest-environment jsdom */
// useAdmin 的未登录静默守卫：自动加载（fetchList）失败时若处于 oauth-provider + 非 authenticated，
// 不弹业务 toast（统一登录 UX 由 ExternalLoginPromptModal 承担）；已登录 / ace-gateway 仍提示真实失败。
import { useAdmin } from '@/hooks/useAdmin';
import { adminService } from '@/services/admin';
import { useAdminStore } from '@/stores/adminStore';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

// auto-mock admin barrel（export *）：adminService 方法变 jest.fn()，避免真实拉取身份/接口。
// notify 不 mock，真实走 sonner toast，下面 spy toast.error 校验是否被弹。
jest.mock('@/services/admin');

const as = adminService as unknown as Record<string, jest.Mock<any>>;

const MISSING_IDENTITY_ERROR = {
  message: '未获取到当前用户身份，请刷新后重试',
  apiPath: '/openapi/v1/spaces',
  requestId: 'req-1',
};

beforeEach(() => {
  jest.clearAllMocks();
  useAdminStore.getState().reset();
  useExternalAuthStore.getState().reset(); // status 默认 'unknown'（非 authenticated）
  useLoginStrategyStore.getState().setLoginStrategy('ace-gateway'); // 默认非静默口径
});

describe('useAdmin 未登录静默守卫', () => {
  it('oauth-provider + 未登录时 fetchList 失败不弹 toast（但仍写入 store.error）', async () => {
    const { toast } = jest.requireActual('sonner') as typeof import('sonner');
    const toastError = jest.spyOn(toast, 'error').mockImplementation(() => 'err' as any);
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    // externalAuthStore.status 默认 'unknown'（非 authenticated）→ shouldMuteNonAuthedToast() 为 true。
    as.listSpaces.mockResolvedValue({ error: MISSING_IDENTITY_ERROR });

    renderHook(() => useAdmin());
    await waitFor(() => expect(as.listSpaces).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });

    expect(toastError).not.toHaveBeenCalled();
    // store 错误态仍记录（静默只压 toast，不丢状态）。
    expect(useAdminStore.getState().error).toEqual(MISSING_IDENTITY_ERROR);

    toastError.mockRestore();
  });

  it('oauth-provider + 已登录时 fetchList 失败照常弹 toast（保留真实失败反馈）', async () => {
    const { toast } = jest.requireActual('sonner') as typeof import('sonner');
    const toastError = jest.spyOn(toast, 'error').mockImplementation(() => 'err' as any);
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    useExternalAuthStore.getState().setAuthenticated({ userId: 'u1', displayName: 'U', provider: 'p' });
    as.listSpaces.mockResolvedValue({ error: MISSING_IDENTITY_ERROR });

    renderHook(() => useAdmin());
    await waitFor(() => expect(as.listSpaces).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });

    // notifyError 单行分支：toast.error(message, { duration })。
    expect(toastError).toHaveBeenCalledWith(
      MISSING_IDENTITY_ERROR.message,
      expect.objectContaining({ duration: expect.any(Number) }),
    );
    toastError.mockRestore();
  });

  it('ace-gateway（internal）fetchList 失败照常弹 toast（未登录静默仅对 oauth-provider 生效）', async () => {
    const { toast } = jest.requireActual('sonner') as typeof import('sonner');
    const toastError = jest.spyOn(toast, 'error').mockImplementation(() => 'err' as any);
    as.listSpaces.mockResolvedValue({ error: MISSING_IDENTITY_ERROR });

    renderHook(() => useAdmin());
    await waitFor(() => expect(as.listSpaces).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });

    expect(toastError).toHaveBeenCalledWith(
      MISSING_IDENTITY_ERROR.message,
      expect.objectContaining({ duration: expect.any(Number) }),
    );
    toastError.mockRestore();
  });
});
