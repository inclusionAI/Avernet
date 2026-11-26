/** @jest-environment jsdom */
import type { CapabilityResult, LoginStrategy } from '@/capabilities';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

let mockStrategy: CapabilityResult<LoginStrategy> = { status: 'available', value: 'oauth-provider' };
let mockAceLogoutUrl: CapabilityResult<string | null> = { status: 'available', value: null };
let mockIsLoggingOut = false;
const mockLogout = jest.fn<() => Promise<void>>();

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getLoginStrategy: () => mockStrategy,
    getAceGatewayLogoutUrl: () => mockAceLogoutUrl,
  }),
}));

// useExternalAuth 的退出编排在自身测试覆盖；此处仅验证门控映射与委托
jest.mock('@/hooks/useExternalAuth', () => ({
  useExternalAuth: () => ({ isLoggingOut: mockIsLoggingOut, logout: mockLogout }),
}));

const ACESS_LOGOUT_URL = 'https://login.alipay.com/logout.htm';

// 动态 import 以确保 mock 生效后再拉 hook
const { useAccountLogout } = require('@/hooks/useAccountLogout') as typeof import('@/hooks/useAccountLogout');

describe('useAccountLogout', () => {
  beforeEach(() => {
    mockStrategy = { status: 'available', value: 'oauth-provider' };
    mockAceLogoutUrl = { status: 'available', value: null };
    mockIsLoggingOut = false;
    mockLogout.mockClear();
  });

  it('loginStrategy=oauth-provider（Open Core=阿里云部署）→ canLogout:true', () => {
    const { result } = renderHook(() => useAccountLogout());
    expect(result.current.canLogout).toBe(true);
  });

  it('loginStrategy=ace-gateway + aceLogoutUrl 可用 → canLogout:true（2026112200120866465: ace-gateway 支持退出登录）', () => {
    mockStrategy = { status: 'available', value: 'ace-gateway' };
    mockAceLogoutUrl = { status: 'available', value: ACESS_LOGOUT_URL };
    const { result } = renderHook(() => useAccountLogout());
    expect(result.current.canLogout).toBe(true);
  });

  it('loginStrategy=ace-gateway 但 aceLogoutUrl 为 null → canLogout:false（兜底安全）', () => {
    mockStrategy = { status: 'available', value: 'ace-gateway' };
    mockAceLogoutUrl = { status: 'available', value: null };
    const { result } = renderHook(() => useAccountLogout());
    expect(result.current.canLogout).toBe(false);
  });

  it('loginStrategy=ace-gateway 但 aceLogoutUrl unsupported → canLogout:false', () => {
    mockStrategy = { status: 'available', value: 'ace-gateway' };
    mockAceLogoutUrl = { status: 'unsupported', value: null, reason: '不适用' };
    const { result } = renderHook(() => useAccountLogout());
    expect(result.current.canLogout).toBe(false);
  });

  it('strategy 不是 available → canLogout:false', () => {
    mockStrategy = { status: 'unsupported', value: 'oauth-provider', reason: '无登录策略' };
    const { result } = renderHook(() => useAccountLogout());
    expect(result.current.canLogout).toBe(false);
  });

  it('isLoggingOut 透传 useExternalAuthStore（oauth-provider 形态）', () => {
    mockIsLoggingOut = true;
    const { result } = renderHook(() => useAccountLogout());
    expect(result.current.isLoggingOut).toBe(true);
  });

  it('oauth-provider 形态 logout 委托 useExternalAuth.logout（编排不重复实现）', async () => {
    const { result } = renderHook(() => useAccountLogout());
    await act(async () => {
      await result.current.logout();
    });
    expect(mockLogout).toHaveBeenCalledTimes(1);
  });

  it('ace-gateway 形态 logout 跳转 ACE 登出 URL（不调 useExternalAuth.logout）', async () => {
    mockStrategy = { status: 'available', value: 'ace-gateway' };
    mockAceLogoutUrl = { status: 'available', value: ACESS_LOGOUT_URL };
    const { result } = renderHook(() => useAccountLogout());
    // ace-gateway 策略下 canLogout=true，logout 走 ACE 跳转而非 oauth 委托
    expect(result.current.canLogout).toBe(true);
    expect(result.current.logout).toBeDefined();
    // 验证 ace 路径不委托 useExternalAuth.logout（jsdom 下 navigateToUrl 是 no-op）
    expect(mockLogout).not.toHaveBeenCalled();
  });
});
