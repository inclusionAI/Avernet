/** @jest-environment jsdom */
import { useViewScopeChangedNotice } from '@/pages/Workspace/hooks/useViewScopeChangedNotice';
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';
import { toast } from 'sonner';

// 自动 mock（不引用 @jest/globals 的 jest 绑定），避免 import 排序把 sonner 前置导致工厂执行时 jest 未初始化。
jest.mock('sonner');

const toastInfo = toast.info as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
});

it('provider 发出 view_scope_changed 时 toast 提示重连', () => {
  let listener: (() => void) | null = null;
  const provider = {
    subscribeToViewScopeChanged: jest.fn((cb: () => void) => {
      listener = cb;
      return () => {
        listener = null;
      };
    }),
  } as unknown as GroupChatProvider;

  renderHook(() => useViewScopeChangedNotice(provider));
  expect(listener).not.toBeNull();
  act(() => listener?.());
  expect(toastInfo).toHaveBeenCalledWith('消息视角已更新，正在重连');
});

it('toast 同时调用 onViewScopeChanged 回调（推送路径刷新历史）', () => {
  let listener: (() => void) | null = null;
  const provider = {
    subscribeToViewScopeChanged: jest.fn((cb: () => void) => {
      listener = cb;
      return () => {
        listener = null;
      };
    }),
  } as unknown as GroupChatProvider;
  const onViewScopeChanged = jest.fn();

  renderHook(() => useViewScopeChangedNotice(provider, onViewScopeChanged));
  act(() => listener?.());
  expect(toastInfo).toHaveBeenCalledWith('消息视角已更新，正在重连');
  expect(onViewScopeChanged).toHaveBeenCalledTimes(1);
});

it('provider 为 null 时不订阅', () => {
  renderHook(() => useViewScopeChangedNotice(null));
  expect(toastInfo).not.toHaveBeenCalled();
});
