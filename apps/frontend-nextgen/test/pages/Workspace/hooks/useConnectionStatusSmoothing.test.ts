/** @jest-environment jsdom */
import { DEFAULT_GRACE_MS, useConnectionStatusSmoothing } from '@/pages/Workspace/hooks/useConnectionStatusSmoothing';
import { afterEach, beforeEach, expect, it, jest } from '@jest/globals';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import { act, renderHook } from '@testing-library/react';

beforeEach(() => {
  jest.useFakeTimers();
});
afterEach(() => {
  jest.useRealTimers();
});

const render = (initial: ProviderConnectionStatus) =>
  renderHook((props: { v: ProviderConnectionStatus }) => useConnectionStatusSmoothing(props.v), {
    initialProps: { v: initial },
  });

it('冷启动首连：初始 disconnected 即展示 connecting，不闪离线；成功后在线', () => {
  const { result, rerender } = render('disconnected');
  expect(result.current).toBe('connecting'); // 不显离线
  rerender({ v: 'connecting' });
  expect(result.current).toBe('connecting');
  rerender({ v: 'connected' });
  expect(result.current).toBe('connected');
});

it('会话切换：connected→disconnected→connecting→connected 全程连接中优先，不显离线', () => {
  const { result, rerender } = render('connected');
  expect(result.current).toBe('connected');
  rerender({ v: 'disconnected' }); // 新 provider 初态
  expect(result.current).toBe('connecting'); // 切换即先显连接中，不再闪离线
  rerender({ v: 'connecting' });
  expect(result.current).toBe('connecting');
  rerender({ v: 'connected' });
  expect(result.current).toBe('connected');
});

it('真实连不上：disconnected 持续超过宽限期才降级为离线', () => {
  const { result, rerender } = render('disconnected');
  expect(result.current).toBe('connecting'); // 宽限期内仍连接中
  act(() => {
    jest.advanceTimersByTime(DEFAULT_GRACE_MS + 100);
  });
  expect(result.current).toBe('disconnected'); // 超宽限仍未连上 → 离线
  // 之后恢复 connected 立即展示在线
  rerender({ v: 'connected' });
  expect(result.current).toBe('connected');
});

it('会话切换中连不上超过宽限，也降级离线后再恢复', () => {
  const { result, rerender } = render('connected');
  rerender({ v: 'disconnected' });
  expect(result.current).toBe('connecting');
  act(() => {
    jest.advanceTimersByTime(DEFAULT_GRACE_MS + 100);
  });
  expect(result.current).toBe('disconnected');
  rerender({ v: 'connecting' });
  expect(result.current).toBe('connecting');
  rerender({ v: 'connected' });
  expect(result.current).toBe('connected');
});

it('reconnecting / error 立即展示，不走宽限', () => {
  const { result, rerender } = render('connected');
  rerender({ v: 'reconnecting' });
  expect(result.current).toBe('reconnecting');
  rerender({ v: 'error' });
  expect(result.current).toBe('error');
  rerender({ v: 'connected' });
  expect(result.current).toBe('connected');
});

it('连接中→离线→连接中→在线：中间瞬态 disconnected 不显离线', () => {
  // 真实 emit 序列 connecting→disconnected→connecting→connected
  const { result, rerender } = render('connecting');
  expect(result.current).toBe('connecting');
  rerender({ v: 'disconnected' }); // 中间瞬态
  expect(result.current).toBe('connecting'); // 不显离线
  rerender({ v: 'connecting' });
  expect(result.current).toBe('connecting');
  rerender({ v: 'connected' });
  expect(result.current).toBe('connected');
});

it('连接中瞬态 disconnected 超宽限期才降级离线', () => {
  const { result, rerender } = render('connecting');
  rerender({ v: 'disconnected' });
  expect(result.current).toBe('connecting'); // 宽限期内仍连接中
  act(() => {
    jest.advanceTimersByTime(DEFAULT_GRACE_MS + 100);
  });
  expect(result.current).toBe('disconnected'); // 超宽限无后续 connecting -> 离线
  rerender({ v: 'connecting' });
  expect(result.current).toBe('connecting');
  rerender({ v: 'connected' });
  expect(result.current).toBe('connected');
});

it('中间断连 ~4s（覆盖 SDK reconnectDelay=3s 重连等待）不显离线', () => {
  const { result, rerender } = render('connecting');
  rerender({ v: 'disconnected' });
  expect(result.current).toBe('connecting');
  act(() => {
    jest.advanceTimersByTime(4000);
  }); // 仍 < 5000ms 宽限
  expect(result.current).toBe('connecting'); // 不闪离线
  rerender({ v: 'reconnecting' });
  expect(result.current).toBe('reconnecting');
  rerender({ v: 'connected' });
  expect(result.current).toBe('connected');
});

it('DEFAULT_GRACE_MS 默认宽限值为 5000ms', () => {
  expect(DEFAULT_GRACE_MS).toBe(5000);
});
