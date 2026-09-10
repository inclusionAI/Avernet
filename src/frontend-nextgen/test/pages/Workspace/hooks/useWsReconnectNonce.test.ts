/** @jest-environment jsdom */
import { useWsReconnectNonce } from '@/pages/Workspace/hooks/useWsReconnectNonce';
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';
import { toast } from 'sonner';

// 自动 mock（不引用 @jest/globals 的 jest 绑定），避免 import 排序把 sonner 前置导致工厂执行时 jest 未初始化。
jest.mock('sonner');

const toastError = toast.error as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().resetWorkspace();
});

function makeProvider(reconnect: () => Promise<void>) {
  return { reconnect: jest.fn(reconnect) } as unknown as GroupChatProvider & {
    reconnect: jest.Mock;
  };
}

it('nonce 变化触发 reconnect，resolve 后调用一次 onReconnected', async () => {
  const provider = makeProvider(() => Promise.resolve());
  const onReconnected = jest.fn();
  renderHook(() => useWsReconnectNonce(provider, onReconnected));
  // mount 首跳忽略
  expect(provider.reconnect).not.toHaveBeenCalled();
  act(() => useWorkspaceStore.getState().bumpWsReconnect());
  await waitFor(() => expect(provider.reconnect).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(onReconnected).toHaveBeenCalledTimes(1));
});

it('reconnect reject 时不调用 onReconnected 且 toast.error', async () => {
  const provider = makeProvider(() => Promise.reject(new Error('重连失败原因')));
  const onReconnected = jest.fn();
  renderHook(() => useWsReconnectNonce(provider, onReconnected));
  act(() => useWorkspaceStore.getState().bumpWsReconnect());
  await waitFor(() => expect(toastError).toHaveBeenCalledWith('重连失败原因'));
  expect(onReconnected).not.toHaveBeenCalled();
});

it('未传 onReconnected 时 reconnect 行为不变', async () => {
  const provider = makeProvider(() => Promise.resolve());
  renderHook(() => useWsReconnectNonce(provider));
  act(() => useWorkspaceStore.getState().bumpWsReconnect());
  await waitFor(() => expect(provider.reconnect).toHaveBeenCalledTimes(1));
  expect(toastError).not.toHaveBeenCalled();
});
