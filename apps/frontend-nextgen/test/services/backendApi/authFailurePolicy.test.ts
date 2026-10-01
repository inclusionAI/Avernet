import { resolveAuthFailureDisposition } from '@/services/backendApi/authFailurePolicy';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { afterEach, beforeEach, describe, expect, it } from '@jest/globals';

/**
 * 未登录失败处置决策(external-oauth-login「未登录静默与统一登录处置」):
 * A/B 两请求通道共用的单一决策点——oauth 策略下未登录失败(HTTP 401 / 信封双方言 401 段体)→
 * 弹窗信号 + `login-prompt-silent`;已确认未登录态后的其余失败 → `silent`;
 * ace-gateway 一律 `default`(行为不变)。
 */
describe('resolveAuthFailureDisposition', () => {
  const resetExternalAuth = (): void =>
    useExternalAuthStore.setState({ status: 'unknown', user: null, error: null, isCheckingAuth: false });

  beforeEach(() => {
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    useLoginRedirectStore.getState().reset();
    useErrorNotifyStore.getState().reset();
    resetExternalAuth();
  });

  afterEach(() => {
    useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
    useLoginRedirectStore.getState().reset();
    resetExternalAuth();
  });

  it('HTTP 401(任意响应体)→ single-flight 弹窗信号 + login-prompt-silent', () => {
    expect(resolveAuthFailureDisposition({ status: 401, data: { message: 'unauthorized' } })).toBe(
      'login-prompt-silent',
    );
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
  });

  it('信封双方言 401 段体(HTTP 200 网关误包)→ 弹窗信号 + login-prompt-silent', () => {
    expect(resolveAuthFailureDisposition({ status: 200, data: { code: 40100, message: 'x' } })).toBe(
      'login-prompt-silent',
    );
    expect(resolveAuthFailureDisposition({ data: { code: 401000, message: '未登录' } })).toBe('login-prompt-silent');
    expect(resolveAuthFailureDisposition({ status: 401, data: { data: { error_code: 'unauthenticated' } } })).toBe(
      'login-prompt-silent',
    );
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
  });

  it('已确认未登录态 + 非未登录失败(500)→ silent(不登记弹窗信号)', () => {
    useExternalAuthStore.getState().setUnauthenticated();

    expect(resolveAuthFailureDisposition({ status: 500, data: { code: 500001, message: 'x' } })).toBe('silent');
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
  });

  it('未确认登录态 + 非 401 失败 → default(既有行为)', () => {
    expect(resolveAuthFailureDisposition({ status: 500, data: { code: 500001, message: 'x' } })).toBe('default');
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
  });

  it('ace-gateway 策略 → 一律 default(含 401/未登录体,行为不变)', () => {
    useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
    useExternalAuthStore.getState().setUnauthenticated();

    expect(resolveAuthFailureDisposition({ status: 401, data: { data: { error_code: 'unauthenticated' } } })).toBe(
      'default',
    );
    expect(resolveAuthFailureDisposition({ status: 500, data: null })).toBe('default');
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
  });

  it('单飞:pendingLogin 已登记时不重复处置(首个信号胜出)', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login/example');

    expect(resolveAuthFailureDisposition({ status: 401, data: null })).toBe('login-prompt-silent');
    expect(useLoginRedirectStore.getState().pendingLogin?.mode).toBe('redirect'); // 既有 redirect 信号不被覆盖
  });
});
