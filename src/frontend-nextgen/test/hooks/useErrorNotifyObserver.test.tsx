/** @jest-environment jsdom */
// 顶层观察者:订阅 errorNotifyStore,经 setTimeout(0) 兜底发起 notifyError;cancel 前置可取消默认提示。
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/components/ui/notify');

import { notifyError } from '@/components/ui/notify';
import { useErrorNotifyObserver } from '@/hooks/useErrorNotifyObserver';
import { BackendRequestError, backendRequest } from '@/services/backendApi/httpClient';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { act, renderHook } from '@testing-library/react';

const mockedNotifyError = notifyError as jest.MockedFunction<typeof notifyError>;

beforeEach(() => {
  useErrorNotifyStore.getState().reset();
  mockedNotifyError.mockClear();
  jest.useFakeTimers();
});

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  jest.useRealTimers();
});

describe('useErrorNotifyObserver', () => {
  it('入队后观察者经 0ms 延迟兜底发起一次 notifyError(带 stable id)', () => {
    renderHook(() => useErrorNotifyObserver());

    act(() => {
      useErrorNotifyStore.getState().enqueue({ toastKey: 'k1', message: '创建失败' });
    });
    act(() => {
      jest.runOnlyPendingTimers();
    });

    expect(mockedNotifyError).toHaveBeenCalledTimes(1);
    expect(mockedNotifyError).toHaveBeenCalledWith('创建失败', { id: 'k1' });
  });

  it('观察者挂载前已入队的记录也会被补刷', () => {
    useErrorNotifyStore.getState().enqueue({ toastKey: 'k-pre', message: '编辑失败' });

    renderHook(() => useErrorNotifyObserver());
    act(() => {
      jest.runOnlyPendingTimers();
    });

    expect(mockedNotifyError).toHaveBeenCalledTimes(1);
    expect(mockedNotifyError).toHaveBeenCalledWith('编辑失败', { id: 'k-pre' });
  });

  it('被 cancel 的记录观察者跳过,不发起默认提示(静默)', () => {
    renderHook(() => useErrorNotifyObserver());

    act(() => {
      useErrorNotifyStore.getState().enqueue({ toastKey: 'k-silent', message: '删除失败' });
    });
    act(() => {
      useErrorNotifyStore.getState().cancel('k-silent');
    });
    act(() => {
      jest.runOnlyPendingTimers();
    });

    expect(mockedNotifyError).not.toHaveBeenCalled();
  });

  it('同 tick 多次入队合并为一次 flush,各自非取消项均发起一次', () => {
    renderHook(() => useErrorNotifyObserver());

    act(() => {
      useErrorNotifyStore.getState().enqueue({ toastKey: 'k-a', message: 'a' });
      useErrorNotifyStore.getState().enqueue({ toastKey: 'k-b', message: 'b' });
    });
    act(() => {
      jest.runOnlyPendingTimers();
    });

    expect(mockedNotifyError).toHaveBeenCalledTimes(2);
    expect(mockedNotifyError).toHaveBeenCalledWith('a', { id: 'k-a' });
    expect(mockedNotifyError).toHaveBeenCalledWith('b', { id: 'k-b' });
  });

  it('卸载后不再发起(清理订阅与定时器)', () => {
    const { unmount } = renderHook(() => useErrorNotifyObserver());
    unmount();

    act(() => {
      useErrorNotifyStore.getState().enqueue({ toastKey: 'k-after', message: 'x' });
    });
    act(() => {
      jest.runOnlyPendingTimers();
    });

    expect(mockedNotifyError).not.toHaveBeenCalled();
  });
});

describe('端到端:通道 B 失败 → 观察者 → notifyError(未写 toast 的 Hook 仍被兜底)', () => {
  it('backendRequest 失败经观察者兜底弹一条错误提示(带稳定 id)', async () => {
    global.fetch = jest.fn(async () => ({
      ok: false,
      status: 500,
      headers: { get: () => 'application/json' },
      text: async () => JSON.stringify({ message: 'boom' }),
      json: async () => ({ message: 'boom' }),
    })) as unknown as typeof fetch;

    renderHook(() => useErrorNotifyObserver());

    await act(async () => {
      await expect(backendRequest('/api/e2e', { operation: 'e2e', injectUserId: false })).rejects.toBeInstanceOf(
        BackendRequestError,
      );
    });
    act(() => {
      jest.runOnlyPendingTimers();
    });

    expect(mockedNotifyError).toHaveBeenCalledTimes(1);
    expect(mockedNotifyError).toHaveBeenCalledWith('boom', { id: 'req:/api/e2e:e2e' });
  });
});
