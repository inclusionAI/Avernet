/** @jest-environment jsdom */
import { useGroupChatAutoReconnect } from '@/pages/Workspace/hooks/useGroupChatAutoReconnect';
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import { afterEach, beforeEach, expect, it, jest } from '@jest/globals';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import { act, renderHook } from '@testing-library/react';

const makeProvider = () => ({ reconnect: jest.fn<() => Promise<void>>() } as unknown as GroupChatProvider);

const render = (provider: GroupChatProvider, status: ProviderConnectionStatus) =>
  renderHook(
    ({ connectionStatus }) =>
      useGroupChatAutoReconnect(provider, connectionStatus, { delays: [1000, 2000, 4000, 8000, 16000] }),
    { initialProps: { connectionStatus: status } },
  );

beforeEach(() => {
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

it('连接建立后断开会按退避策略自动重连，重连成功后停止后续尝试', async () => {
  const provider = makeProvider();
  provider.reconnect = jest.fn<() => Promise<void>>().mockResolvedValue(undefined);
  const { result, rerender } = render(provider, 'disconnected');

  expect(result.current).toBe(false);
  expect(provider.reconnect).not.toHaveBeenCalled();
  rerender({ connectionStatus: 'connected' });
  rerender({ connectionStatus: 'disconnected' });
  expect(result.current).toBe(true);

  act(() => jest.advanceTimersByTime(1000));
  await act(async () => {});
  expect(provider.reconnect).toHaveBeenCalledTimes(1);

  rerender({ connectionStatus: 'connected' });
  expect(result.current).toBe(false);
  act(() => jest.advanceTimersByTime(10_000));
  expect(provider.reconnect).toHaveBeenCalledTimes(1);
});

it('连续重连失败达到上限后不再自动尝试，保留给手动重连', async () => {
  const provider = makeProvider();
  provider.reconnect = jest.fn<() => Promise<void>>().mockRejectedValue(new Error('断开'));
  const { rerender } = render(provider, 'connected');
  rerender({ connectionStatus: 'disconnected' });

  for (const delay of [1000, 2000, 4000, 8000, 16000]) {
    act(() => jest.advanceTimersByTime(delay));
    await act(async () => {});
  }

  expect(provider.reconnect).toHaveBeenCalledTimes(5);
  act(() => jest.advanceTimersByTime(30_000));
  expect(provider.reconnect).toHaveBeenCalledTimes(5);
});
