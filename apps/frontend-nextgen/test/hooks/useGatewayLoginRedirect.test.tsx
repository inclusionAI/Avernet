/** @jest-environment jsdom */
import { notifyError } from '@/components/ui/notify';
import { useGatewayLoginRedirect } from '@/hooks/useGatewayLoginRedirect';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { redirectCurrentTab } from '@/utils/redirectCurrentTab';
import { renderHook } from '@testing-library/react';
import React from 'react';

// notifyError 与 redirectCurrentTab 均 auto-mock:toast 不需 sonner 真渲染,跳转接缝避免 jsdom location 不可重定义。
jest.mock('@/components/ui/notify');
jest.mock('@/utils/redirectCurrentTab');

const mockedNotifyError = notifyError as jest.MockedFunction<typeof notifyError>;
const mockedRedirectCurrentTab = redirectCurrentTab as jest.MockedFunction<typeof redirectCurrentTab>;

beforeEach(() => {
  useLoginRedirectStore.getState().reset();
  mockedNotifyError.mockClear();
  mockedRedirectCurrentTab.mockClear();
});

describe('useGatewayLoginRedirect', () => {
  it('pendingLoginUrl 已设置 → 恰好一次 toast + 一次当前标签页跳转', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    renderHook(() => useGatewayLoginRedirect());

    expect(mockedNotifyError).toHaveBeenCalledTimes(1);
    expect(mockedNotifyError).toHaveBeenCalledWith('未登录，正在跳转登录…');
    expect(mockedRedirectCurrentTab).toHaveBeenCalledTimes(1);
    expect(mockedRedirectCurrentTab).toHaveBeenCalledWith('https://login.example/a');
  });

  it('无 pendingLoginUrl → 不 toast、不跳转', () => {
    renderHook(() => useGatewayLoginRedirect());

    expect(mockedNotifyError).not.toHaveBeenCalled();
    expect(mockedRedirectCurrentTab).not.toHaveBeenCalled();
  });

  it('并发多次 requestRedirect 仍只一次 toast + 一次跳转(单飞:首 URL 胜出)', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/first');
    useLoginRedirectStore.getState().requestRedirect('https://login.example/second');
    renderHook(() => useGatewayLoginRedirect());

    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe('https://login.example/first');
    expect(mockedNotifyError).toHaveBeenCalledTimes(1);
    expect(mockedRedirectCurrentTab).toHaveBeenCalledTimes(1);
    expect(mockedRedirectCurrentTab).toHaveBeenCalledWith('https://login.example/first');
  });

  it('React StrictMode 双调 effect 不重复(firedRef 守卫)', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/x');
    renderHook(() => useGatewayLoginRedirect(), {
      wrapper: ({ children }) => React.createElement(React.StrictMode, null, children),
    });

    expect(mockedNotifyError).toHaveBeenCalledTimes(1);
    expect(mockedRedirectCurrentTab).toHaveBeenCalledTimes(1);
  });

  it('探测点先于 hook 挂载 set store → 挂载后补触发一次', () => {
    // 模拟 boot 期 httpClient 先 set store,之后 rootContainer 才挂载观察者。
    useLoginRedirectStore.getState().requestRedirect('https://login.example/late');
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe('https://login.example/late');

    renderHook(() => useGatewayLoginRedirect());

    expect(mockedNotifyError).toHaveBeenCalledTimes(1);
    expect(mockedRedirectCurrentTab).toHaveBeenCalledWith('https://login.example/late');
  });

  it('prompt 模式(prompt 信号)→ 不 toast、不跳转(由全局 ExternalLoginPromptModal 消费)', () => {
    useLoginRedirectStore.getState().requestPrompt();
    renderHook(() => useGatewayLoginRedirect());

    expect(mockedNotifyError).not.toHaveBeenCalled();
    expect(mockedRedirectCurrentTab).not.toHaveBeenCalled();
  });
});
