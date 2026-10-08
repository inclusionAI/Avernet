/** @jest-environment jsdom */
import { checkInviteCodeBinding, useInviteCodeGate, useInviteCodeGateBoot } from '@/hooks/useInviteCodeGate';
import * as inviteCodeService from '@/services/collaboration/inviteCodeService';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useInviteCodeGateStore } from '@/stores/inviteCodeGateStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { reloadCurrentTab } from '@/utils/redirectCurrentTab';
import { afterEach, beforeEach, describe, expect, it } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

// 仅 mock 两个异步接口函数，保留 InviteCodeServiceError 类与纯函数（normalizeCode/friendly）真实，供 instanceof 与文案断言。
jest.mock('@/services/collaboration/inviteCodeService', () => {
  const actual = jest.requireActual('@/services/collaboration/inviteCodeService');
  return { ...actual, getMyInviteCodeBinding: jest.fn(), bindInviteCode: jest.fn() };
});
jest.mock('@/utils/redirectCurrentTab');
const service = inviteCodeService as jest.Mocked<typeof inviteCodeService>;
const mockedReload = reloadCurrentTab as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
  useExternalAuthStore.getState().reset();
  useInviteCodeGateStore.getState().reset();
});

afterEach(() => {
  useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
});

describe('useInviteCodeGate.submitCode', () => {
  it('空输入 → setSubmitError invalid_request，不调 bindInviteCode、不 reload', async () => {
    service.bindInviteCode.mockResolvedValue({ bound: true, bound_at: 1 });
    const { result } = renderHook(() => useInviteCodeGate());
    await act(async () => {
      await result.current.submitCode('   ');
    });
    expect(service.bindInviteCode).not.toHaveBeenCalled();
    expect(useInviteCodeGateStore.getState().submitError).toMatchObject({ code: 'invalid_request' });
    expect(mockedReload).not.toHaveBeenCalled();
  });

  it('成功 → setBinding + clearPrompt + window.location.reload 回流', async () => {
    service.bindInviteCode.mockResolvedValue({ bound: true, bound_at: 1730000000 });
    useInviteCodeGateStore.getState().requestPrompt();
    const { result } = renderHook(() => useInviteCodeGate());
    await act(async () => {
      await result.current.submitCode(' abc 123 ');
    });
    await waitFor(() => expect(mockedReload).toHaveBeenCalled());
    expect(service.bindInviteCode).toHaveBeenCalledWith('ABC123');
    expect(useInviteCodeGateStore.getState().binding).toEqual({ bound: true, bound_at: 1730000000 });
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(false);
  });

  it('无效码 → setSubmitError invite_code_unavailable，不 reload，弹窗保持打开', async () => {
    service.bindInviteCode.mockRejectedValue(
      new inviteCodeService.InviteCodeServiceError('invite_code_unavailable', '邀请码无效，请检查后重试'),
    );
    const { result } = renderHook(() => useInviteCodeGate());
    await act(async () => {
      await result.current.submitCode('ZZZ999');
    });
    expect(useInviteCodeGateStore.getState().submitError).toMatchObject({ code: 'invite_code_unavailable' });
    expect(useInviteCodeGateStore.getState().submitting).toBe(false);
    expect(mockedReload).not.toHaveBeenCalled();
  });

  it('非 InviteCodeServiceError（网络未知）→ code=unknown', async () => {
    service.bindInviteCode.mockRejectedValue(new Error('network down'));
    const { result } = renderHook(() => useInviteCodeGate());
    await act(async () => {
      await result.current.submitCode('ABC123');
    });
    expect(useInviteCodeGateStore.getState().submitError).toMatchObject({ code: 'unknown' });
    expect(mockedReload).not.toHaveBeenCalled();
  });
});

describe('checkInviteCodeBinding（主动检测）', () => {
  it('bound:false → 单飞登记门禁弹窗信号', async () => {
    service.getMyInviteCodeBinding.mockResolvedValue({ bound: false });
    await checkInviteCodeBinding();
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(true);
  });

  it('bound:true → 不弹门禁', async () => {
    service.getMyInviteCodeBinding.mockResolvedValue({ bound: true, bound_at: 1 });
    await checkInviteCodeBinding();
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(false);
  });

  it('失败 → 静默（不弹门禁、不抛错；dev mock 404 不噪音）', async () => {
    service.getMyInviteCodeBinding.mockRejectedValue(new inviteCodeService.InviteCodeServiceError('unknown', 'x'));
    await expect(checkInviteCodeBinding()).resolves.toBeUndefined();
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(false);
  });

  it('ace-gateway 策略 → 不调 /me（门禁不激活）', async () => {
    useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
    await checkInviteCodeBinding();
    expect(service.getMyInviteCodeBinding).not.toHaveBeenCalled();
  });
});

describe('useInviteCodeGateBoot（主动 boot）', () => {
  it('oauth + enabled + authenticated → 主动查 /me 一次', async () => {
    service.getMyInviteCodeBinding.mockResolvedValue({ bound: true, bound_at: 1 });
    useExternalAuthStore.getState().setAuthenticated({ userId: 'u-1', displayName: 'Tomu', provider: 'alipay' });
    renderHook(() => useInviteCodeGateBoot());
    await waitFor(() => expect(service.getMyInviteCodeBinding).toHaveBeenCalledTimes(1));
  });

  it('未登录（status !== authenticated）→ 不查 /me', async () => {
    service.getMyInviteCodeBinding.mockResolvedValue({ bound: false });
    renderHook(() => useInviteCodeGateBoot());
    await new Promise((resolve) => {
      setTimeout(resolve, 50);
    });
    expect(service.getMyInviteCodeBinding).not.toHaveBeenCalled();
  });

  it('ace-gateway → 不查 /me', async () => {
    useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
    useExternalAuthStore.getState().setAuthenticated({ userId: 'u-1', displayName: 'Tomu', provider: 'alipay' });
    renderHook(() => useInviteCodeGateBoot());
    await new Promise((resolve) => {
      setTimeout(resolve, 50);
    });
    expect(service.getMyInviteCodeBinding).not.toHaveBeenCalled();
  });
});
