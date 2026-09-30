/** @jest-environment jsdom */
import type { CapabilityResult, LoginStrategy } from '@/capabilities';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

let mockStrategy: CapabilityResult<LoginStrategy> = { status: 'available', value: 'oauth-provider' };
let mockIsLoggingOut = false;
const mockLogout = jest.fn<() => Promise<void>>();

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getLoginStrategy: () => mockStrategy,
  }),
}));

// useExternalAuth 的退出编排在自身测试覆盖；此处仅验证门控映射与委托
jest.mock('@/hooks/useExternalAuth', () => ({
  useExternalAuth: () => ({ isLoggingOut: mockIsLoggingOut, logout: mockLogout }),
}));

// 动态 import 以确保 mock 生效后再拉 hook
const { useAccountLogout } = require('@/hooks/useAccountLogout') as typeof import('@/hooks/useAccountLogout');

describe('useAccountLogout', () => {
  beforeEach(() => {
    mockStrategy = { status: 'available', value: 'oauth-provider' };
    mockIsLoggingOut = false;
    mockLogout.mockClear();
  });

  it('loginStrategy=oauth-provider（Open Core=阿里云部署）→ canLogout:true', () => {
    const { result } = renderHook(() => useAccountLogout());
    expect(result.current.canLogout).toBe(true);
  });

  it('loginStrategy=ace-gateway（internal overlay）→ canLogout:false', () => {
    mockStrategy = { status: 'available', value: 'ace-gateway' };
    const { result } = renderHook(() => useAccountLogout());
    expect(result.current.canLogout).toBe(false);
  });

  it('strategy 不是 available → canLogout:false', () => {
    mockStrategy = { status: 'unsupported', value: 'oauth-provider', reason: '无登录策略' };
    const { result } = renderHook(() => useAccountLogout());
    expect(result.current.canLogout).toBe(false);
  });

  it('isLoggingOut 透传 useExternalAuthStore', () => {
    mockIsLoggingOut = true;
    const { result } = renderHook(() => useAccountLogout());
    expect(result.current.isLoggingOut).toBe(true);
  });

  it('logout 委托 useExternalAuth.logout（编排不重复实现）', async () => {
    const { result } = renderHook(() => useAccountLogout());
    await act(async () => {
      await result.current.logout();
    });
    expect(mockLogout).toHaveBeenCalledTimes(1);
  });
});
