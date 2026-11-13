import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { beforeEach, describe, expect, it } from '@jest/globals';

describe('loginRedirectStore', () => {
  beforeEach(() => {
    useLoginRedirectStore.getState().reset();
  });

  it('初始 pendingLoginUrl 为 undefined', () => {
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBeUndefined();
  });

  it('requestRedirect 首次设置 pendingLoginUrl', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe('https://login.example/a');
  });

  it('requestRedirect 已 pending 时幂等(单飞:首 URL 胜出)', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/first');
    useLoginRedirectStore.getState().requestRedirect('https://login.example/second');
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe('https://login.example/first');
  });

  it('requestRedirect 空串/空白串不生效', () => {
    useLoginRedirectStore.getState().requestRedirect('   ');
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBeUndefined();
  });

  it('requestPrompt 置 pendingLogin{mode:"prompt"},pendingLoginUrl 保持 undefined', () => {
    useLoginRedirectStore.getState().requestPrompt();
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBeUndefined();
  });

  it('单飞:requestRedirect 先到则 requestPrompt no-op(同生命周期首个信号胜出)', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    useLoginRedirectStore.getState().requestPrompt();
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'redirect', url: 'https://login.example/a' });
  });

  it('单飞:requestPrompt 先到则 requestRedirect no-op', () => {
    useLoginRedirectStore.getState().requestPrompt();
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
  });
});

// ace-gateway-popup-reauth:redirect 模式 =「等待重登」——代次防线(/晚到旧信号不闪回)、
// 链接持续刷新(aceToken 短时效)与收口清口。
describe('loginRedirectStore 等待重登语义', () => {
  beforeEach(() => {
    useLoginRedirectStore.getState().reset();
    useExternalAuthStore.setState({ authEpoch: 5 });
  });

  it('requestRedirect 携带过期代次(晚到的旧代拦截体)不登记', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/stale', 4);
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
  });

  it('requestRedirect 携带当前代次正常登记', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/fresh', 5);
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({
      mode: 'redirect',
      url: 'https://login.example/fresh',
    });
  });

  it('refreshRedirectUrl 仅在 redirect 模式下更新链接(单飞信号不重新排队)', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/old', 5);
    useLoginRedirectStore.getState().refreshRedirectUrl('https://login.example/new');
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({
      mode: 'redirect',
      url: 'https://login.example/new',
    });
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe('https://login.example/new');
  });

  it('refreshRedirectUrl 在 prompt 模式/空信号下 no-op', () => {
    useLoginRedirectStore.getState().requestPrompt();
    useLoginRedirectStore.getState().refreshRedirectUrl('https://login.example/x');
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
  });

  it('refreshRedirectUrl 非法 url 不生效', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/old', 5);
    useLoginRedirectStore.getState().refreshRedirectUrl('   ');
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe('https://login.example/old');
  });

  it('resolveReauth 清口等待信号(恢复/用户取消),随后新拦截可重新登记', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a', 5);
    useLoginRedirectStore.getState().resolveReauth();
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBeUndefined();

    useLoginRedirectStore.getState().requestRedirect('https://login.example/b', 5);
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe('https://login.example/b');
  });
});
