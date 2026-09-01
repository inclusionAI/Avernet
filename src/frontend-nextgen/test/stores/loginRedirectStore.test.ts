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
