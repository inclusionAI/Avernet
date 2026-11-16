/** @jest-environment jsdom */
import { notifyError, notifySuccess } from '@/components/ui/notify';
import { useGatewayLoginRedirect } from '@/hooks/useGatewayLoginRedirect';
import { probeGatewaySession } from '@/services/auth/reauthProbe';
import type { GatewayReauthProbeResult } from '@/services/auth/reauthProbe';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { focusLoginTab, openLoginInNewTab, redirectCurrentTab } from '@/utils/redirectCurrentTab';
import { broadcastAuthReturned, listenAuthReturned } from '@/utils/reauthBroadcast';
import { act, renderHook } from '@testing-library/react';
import React from 'react';

/**
 * ace-gateway 重登编排 Hook 语义(ace-gateway-popup-reauth):
 * 等待重登(redirect 模式)→ 全局 Modal 弹显;恢复感知 = 首探立即 + 3s 轮询 + 广播受理;
 * 收口 = bumpAuthEpoch + resolveReauth + 成功提示。探活/广播/开窗/当前页导航均为接缝 mock
 * (jsdom 下 BroadcastChannel/window.open/location 不可依赖)。
 */
jest.mock('@/components/ui/notify');
jest.mock('@/services/auth/reauthProbe');
jest.mock('@/utils/redirectCurrentTab');
jest.mock('@/utils/reauthBroadcast');

const mockedNotifyError = notifyError as jest.MockedFunction<typeof notifyError>;
const mockedNotifySuccess = notifySuccess as jest.MockedFunction<typeof notifySuccess>;
const mockedProbe = probeGatewaySession as jest.MockedFunction<typeof probeGatewaySession>;
const mockedOpenLoginInNewTab = openLoginInNewTab as jest.MockedFunction<typeof openLoginInNewTab>;
const mockedFocusLoginTab = focusLoginTab as jest.MockedFunction<typeof focusLoginTab>;
const mockedRedirectCurrentTab = redirectCurrentTab as jest.MockedFunction<typeof redirectCurrentTab>;
const mockedBroadcast = broadcastAuthReturned as jest.MockedFunction<typeof broadcastAuthReturned>;
const mockedListenAuthReturned = listenAuthReturned as jest.MockedFunction<typeof listenAuthReturned>;

/** 捕获广播受理 handler(用于模拟登录回流 tab 的广播触发)。 */
let broadcastHandler: (() => void) | undefined;

beforeEach(() => {
  jest.useRealTimers();
  useLoginRedirectStore.getState().reset();
  useExternalAuthStore.setState({ authEpoch: 0 });
  jest.clearAllMocks();
  broadcastHandler = undefined;
  mockedListenAuthReturned.mockImplementation((handler) => {
    broadcastHandler = handler;
    return () => {};
  });
  mockedProbe.mockResolvedValue({ state: 'unauthenticated', loginUrl: 'https://login.example/a' });
});

async function flushAsync(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

describe('useGatewayLoginRedirect 弹窗状态', () => {
  it('redirect 模式 → open=true 并透出登录链接', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    const { result } = renderHook(() => useGatewayLoginRedirect());

    expect(result.current.open).toBe(true);
    expect(result.current.loginUrl).toBe('https://login.example/a');
  });

  it('prompt 模式(oauth-provider)与空闲 → open=false,onOpenLoginTab 不开窗', () => {
    const { result: idle } = renderHook(() => useGatewayLoginRedirect());
    expect(idle.current.open).toBe(false);

    useLoginRedirectStore.getState().requestPrompt();
    const { result } = renderHook(() => useGatewayLoginRedirect());

    expect(result.current.open).toBe(false);
    act(() => result.current.onOpenLoginTab());
    expect(mockedOpenLoginInNewTab).not.toHaveBeenCalled();
  });

  it('挂载即广播一次(SPA boot 无条件广播,供登录回流 tab 唤醒原页感知)', () => {
    renderHook(() => useGatewayLoginRedirect());

    expect(mockedBroadcast).toHaveBeenCalledTimes(1);
  });
});

describe('useGatewayLoginRedirect 恢复感知', () => {
  it('等待重登期间立即首探 + 3s 间隔轮询,确认恢复后收口(推进代次/清口/提示重试)', async () => {
    jest.useFakeTimers();
    mockedProbe.mockResolvedValue({ state: 'logged-in' } satisfies GatewayReauthProbeResult);
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    renderHook(() => useGatewayLoginRedirect());

    // 等待重登(reset/resolveReauth)后不再登记新拦截信号,Modal 关闭。
    await flushAsync();
    expect(mockedProbe).toHaveBeenCalledTimes(1);
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
    expect(useExternalAuthStore.getState().authEpoch).toBe(1);
    expect(mockedNotifySuccess).toHaveBeenCalledTimes(1);
    expect(mockedNotifySuccess).toHaveBeenCalledWith('登录已恢复，请重试刚才的操作。');

    // 收口后轮询停止:时间推进不再触发探活/广播受理。
    act(() => {
      jest.advanceTimersByTime(9000);
    });
    expect(mockedProbe).toHaveBeenCalledTimes(1);
    jest.useRealTimers();
  });

  it('探到 ACE 拦截体时刷新待用链接(aceToken 保持新鲜),不收口', async () => {
    mockedProbe.mockResolvedValueOnce({ state: 'unauthenticated', loginUrl: 'https://login.example/fresh' });
    useLoginRedirectStore.getState().requestRedirect('https://login.example/stale');
    const { result } = renderHook(() => useGatewayLoginRedirect());

    await flushAsync();

    expect(result.current.open).toBe(true);
    expect(result.current.loginUrl).toBe('https://login.example/fresh');
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
  });

  it('探活不可判定(unknown)维持等待,不误收口', async () => {
    mockedProbe.mockResolvedValue({ state: 'unknown' } satisfies GatewayReauthProbeResult);
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    const { result } = renderHook(() => useGatewayLoginRedirect());

    await flushAsync();

    expect(result.current.open).toBe(true);
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
  });

  it('收到回流广播 → 立即探活(快路径,免等下一轮轮询)', async () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    renderHook(() => useGatewayLoginRedirect());
    await flushAsync();
    expect(mockedProbe).toHaveBeenCalledTimes(1);

    act(() => {
      broadcastHandler?.();
    });
    await flushAsync();

    expect(mockedProbe).toHaveBeenCalledTimes(2);
  });
});

describe('useGatewayLoginRedirect 弹窗动作', () => {
  function fakeWindow(closed = false): Window {
    return { closed, focus: jest.fn() } as unknown as Window;
  }

  it('onOpenLoginTab 在手势内新标签页打开登录链接', () => {
    mockedOpenLoginInNewTab.mockReturnValue(fakeWindow());
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    const { result } = renderHook(() => useGatewayLoginRedirect());

    act(() => result.current.onOpenLoginTab());

    expect(mockedOpenLoginInNewTab).toHaveBeenCalledTimes(1);
    expect(mockedOpenLoginInNewTab).toHaveBeenCalledWith('https://login.example/a');
    expect(mockedNotifyError).not.toHaveBeenCalled();
  });

  it('登录窗存活期间重复点击聚焦复用,不重复开新 tab', () => {
    const alive = fakeWindow();
    mockedOpenLoginInNewTab.mockReturnValueOnce(alive);
    mockedFocusLoginTab.mockReturnValue(true);
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    const { result } = renderHook(() => useGatewayLoginRedirect());

    act(() => result.current.onOpenLoginTab());
    act(() => result.current.onOpenLoginTab());

    expect(mockedOpenLoginInNewTab).toHaveBeenCalledTimes(1);
    expect(mockedFocusLoginTab).toHaveBeenCalledWith(alive);
  });

  it('弹窗被拦截(null)→ 提示允许弹窗,不静默失败', () => {
    mockedOpenLoginInNewTab.mockReturnValueOnce(null);
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    const { result } = renderHook(() => useGatewayLoginRedirect());

    act(() => result.current.onOpenLoginTab());

    expect(mockedNotifyError).toHaveBeenCalledTimes(1);
    expect(mockedNotifyError).toHaveBeenCalledWith(
      '登录窗口被浏览器拦截，请允许弹窗后重试，或改用「在当前页登录」。',
    );
  });

  it('onCurrentTabLogin → 当前页导航登录(降级兜底)', () => {
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    const { result } = renderHook(() => useGatewayLoginRedirect());

    act(() => result.current.onCurrentTabLogin());

    expect(mockedRedirectCurrentTab).toHaveBeenCalledTimes(1);
    expect(mockedRedirectCurrentTab).toHaveBeenCalledWith('https://login.example/a');
  });

  it('onDismiss → 清口等待状态,轮询停止', async () => {
    const { result } = renderHook(() => useGatewayLoginRedirect());
    useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
    await flushAsync();
    expect(result.current.open).toBe(true);

    act(() => result.current.onDismiss());

    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
    expect(result.current.open).toBe(false);
  });
});

// React StrictMode 双调 effect 下广播幂等(boot 广播允许重复,接收方状态门控忽略重复消息)。
it('StrictMode 双挂载广播最多两次,不破坏流程', () => {
  useLoginRedirectStore.getState().requestRedirect('https://login.example/a');
  renderHook(() => useGatewayLoginRedirect(), {
    wrapper: ({ children }) => React.createElement(React.StrictMode, null, children),
  });

  expect(mockedBroadcast.mock.calls.length).toBeGreaterThan(0);
  expect(mockedBroadcast.mock.calls.length).toBeLessThanOrEqual(2);
});
