/** @jest-environment jsdom */
// useWorkOrders 的未登录静默守卫：自动加载（fetchList）失败时若处于 oauth-provider + 非 authenticated，
// 不弹业务 toast（统一登录 UX 由 ExternalLoginPromptModal 承担）；已登录 / ace-gateway 仍提示真实失败。
import { useWorkOrders } from '@/hooks/useWorkOrders';
import { workOrderService } from '@/services/admin/workOrderService';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { useWorkOrderStore } from '@/stores/workOrderStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

// auto-mock：把 workOrderService 方法替换为 jest.fn()，避免真实拉取身份/接口。
// 仅 mock 本模块；notificationService 真实 import 但 fetchList 不触达，无网络副作用。
jest.mock('@/services/admin/workOrderService');

const wo = workOrderService as unknown as Record<string, jest.Mock<any>>;

const MISSING_IDENTITY_ERROR = {
  message: '未获取到当前用户身份，请刷新后重试',
  apiPath: '/openapi/v1/work-orders',
};

beforeEach(() => {
  jest.clearAllMocks();
  useWorkOrderStore.getState().reset();
  useExternalAuthStore.getState().reset(); // status 默认 'unknown'（非 authenticated）
  useLoginStrategyStore.getState().setLoginStrategy('ace-gateway'); // 默认非静默口径
});

describe('useWorkOrders 未登录静默守卫', () => {
  it('oauth-provider + 未登录时 fetchList 失败不弹 toast（但仍写入 store.error）', async () => {
    const { toast } = jest.requireActual('sonner') as typeof import('sonner');
    const toastError = jest.spyOn(toast, 'error').mockImplementation(() => 'err' as any);
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    // externalAuthStore.status 默认 'unknown'（非 authenticated）→ shouldMuteNonAuthedToast() 为 true。
    wo.list.mockResolvedValue({ error: MISSING_IDENTITY_ERROR });

    renderHook(() => useWorkOrders());
    await waitFor(() => expect(wo.list).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });

    expect(toastError).not.toHaveBeenCalled();
    // store 错误态仍记录，供已登录/重试路径消费（静默只压 toast，不丢状态）。
    expect(useWorkOrderStore.getState().error).toEqual(MISSING_IDENTITY_ERROR);

    toastError.mockRestore();
  });

  it('oauth-provider + 已登录时 fetchList 失败照常弹 toast（保留真实失败反馈）', async () => {
    const { toast } = jest.requireActual('sonner') as typeof import('sonner');
    const toastError = jest.spyOn(toast, 'error').mockImplementation(() => 'err' as any);
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    useExternalAuthStore.getState().setAuthenticated({ userId: 'u1', displayName: 'U', provider: 'p' });
    wo.list.mockResolvedValue({ error: MISSING_IDENTITY_ERROR });

    renderHook(() => useWorkOrders());
    await waitFor(() => expect(wo.list).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });

    expect(toastError).toHaveBeenCalledWith(MISSING_IDENTITY_ERROR.message);
    toastError.mockRestore();
  });

  it('ace-gateway（internal）fetchList 失败照常弹 toast（未登录静默仅对 oauth-provider 生效）', async () => {
    const { toast } = jest.requireActual('sonner') as typeof import('sonner');
    const toastError = jest.spyOn(toast, 'error').mockImplementation(() => 'err' as any);
    wo.list.mockResolvedValue({ error: MISSING_IDENTITY_ERROR });

    renderHook(() => useWorkOrders());
    await waitFor(() => expect(wo.list).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });

    expect(toastError).toHaveBeenCalledWith(MISSING_IDENTITY_ERROR.message);
    toastError.mockRestore();
  });
});
