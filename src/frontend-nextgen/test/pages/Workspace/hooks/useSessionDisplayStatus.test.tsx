/** @jest-environment jsdom */
import { useSessionDisplayStatus, type SessionEnterOutcome } from '@/pages/Workspace/hooks/useSessionDisplayStatus';
import { afterEach, beforeEach, expect, it, jest } from '@jest/globals';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import { act, renderHook } from '@testing-library/react';

interface CaseProps {
  sessionKey: string | null;
  rawStatus: ProviderConnectionStatus;
  enterOutcome: SessionEnterOutcome;
  autoReconnecting?: boolean;
}

const renderStatus = (initial: CaseProps) =>
  renderHook((p: CaseProps) => useSessionDisplayStatus(p), { initialProps: initial });

beforeEach(() => {
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

it('切换会话瞬间立即显示连接中，残留的已连接不透传（AC-4）', () => {
  const { result } = renderStatus({ sessionKey: 's1', rawStatus: 'connected', enterOutcome: 'pending' });

  // 首次 render 即同步推导：进入窗口内吞掉一切中间值（含上一会话残留的 connected）。
  expect(result.current.status).toBe('connecting');
  expect(result.current.isEntering).toBe(true);
});

it('进入窗口内 WS 提前 connected 不透传，就绪后才显示已连接（AC-1/AC-3）', () => {
  const { result, rerender } = renderStatus({ sessionKey: 's1', rawStatus: 'connecting', enterOutcome: 'pending' });

  rerender({ sessionKey: 's1', rawStatus: 'connected', enterOutcome: 'pending' });
  expect(result.current.status).toBe('connecting');

  rerender({ sessionKey: 's1', rawStatus: 'connected', enterOutcome: 'ready' });
  expect(result.current.status).toBe('connected');
  expect(result.current.isEntering).toBe(false);
});

it('就绪后进入信号回落（同会话刷新历史）不闪回连接中（AC-1 边界）', () => {
  const { result, rerender } = renderStatus({ sessionKey: 's1', rawStatus: 'connected', enterOutcome: 'ready' });
  expect(result.current.status).toBe('connected');

  // historyRefreshNonce 重拉历史导致 outcome 短暂回 pending：已就绪的会话保持已连接，不产生跳变。
  rerender({ sessionKey: 's1', rawStatus: 'connected', enterOutcome: 'pending' });
  expect(result.current.status).toBe('connected');
});

it('进入失败一次到位显示连接失败，不再处于进入中（AC-6）', () => {
  const { result, rerender } = renderStatus({ sessionKey: 's1', rawStatus: 'connecting', enterOutcome: 'pending' });

  rerender({ sessionKey: 's1', rawStatus: 'connected', enterOutcome: 'failed' });
  expect(result.current.status).toBe('error');
  expect(result.current.isEntering).toBe(false);
});

it('就绪后静默断开有 5 秒宽限期，恢复连接后宽限计时重置（AC-5）', () => {
  const { result, rerender } = renderStatus({ sessionKey: 's1', rawStatus: 'connected', enterOutcome: 'ready' });

  rerender({ sessionKey: 's1', rawStatus: 'disconnected', enterOutcome: 'pending' });
  expect(result.current.status).toBe('connecting');

  act(() => {
    jest.advanceTimersByTime(5000);
  });
  expect(result.current.status).toBe('disconnected');

  // 恢复在线后再次断开：宽限期从头计，而不是沿用上次已过期的状态。
  rerender({ sessionKey: 's1', rawStatus: 'connected', enterOutcome: 'pending' });
  expect(result.current.status).toBe('connected');
  rerender({ sessionKey: 's1', rawStatus: 'disconnected', enterOutcome: 'pending' });
  expect(result.current.status).toBe('connecting');
});

it('自动重连接管期间显示重连中（AC-5）', () => {
  const { result, rerender } = renderStatus({ sessionKey: 's1', rawStatus: 'connected', enterOutcome: 'ready' });

  rerender({ sessionKey: 's1', rawStatus: 'disconnected', enterOutcome: 'pending', autoReconnecting: true });
  expect(result.current.status).toBe('reconnecting');

  // 重连成功后回到就绪语义。
  rerender({ sessionKey: 's1', rawStatus: 'connected', enterOutcome: 'pending', autoReconnecting: false });
  expect(result.current.status).toBe('connected');
});

it('快速连续切换会话，旧会话的连接事件不污染新会话（AC-4 边界）', () => {
  const { result, rerender } = renderStatus({ sessionKey: 's1', rawStatus: 'connecting', enterOutcome: 'pending' });

  rerender({ sessionKey: 's1', rawStatus: 'connected', enterOutcome: 'ready' });
  expect(result.current.status).toBe('connected');

  // 切到 s2 的同帧，旧会话 s1 的 connected 仍在 rawStatus 上（订阅尚未发出新事件）。
  rerender({ sessionKey: 's2', rawStatus: 'connected', enterOutcome: 'pending' });
  expect(result.current.status).toBe('connecting');
  expect(result.current.isEntering).toBe(true);

  // s2 就绪后才恢复已连接。
  rerender({ sessionKey: 's2', rawStatus: 'connected', enterOutcome: 'ready' });
  expect(result.current.status).toBe('connected');
});

it('宽限计时随会话切换重置（AC-4/AC-5 组合）', () => {
  const { result, rerender } = renderStatus({ sessionKey: 's1', rawStatus: 'connected', enterOutcome: 'ready' });

  // s1 宽限过期，显示已断开。
  rerender({ sessionKey: 's1', rawStatus: 'disconnected', enterOutcome: 'pending' });
  act(() => {
    jest.advanceTimersByTime(5000);
  });
  expect(result.current.status).toBe('disconnected');

  // 切到 s2 并就绪后再断开：必须重新走满 5 秒宽限，而不是立即继承 s1 的已断开。
  rerender({ sessionKey: 's2', rawStatus: 'connected', enterOutcome: 'ready' });
  rerender({ sessionKey: 's2', rawStatus: 'disconnected', enterOutcome: 'pending' });
  expect(result.current.status).toBe('connecting');
  act(() => {
    jest.advanceTimersByTime(4999);
  });
  expect(result.current.status).toBe('connecting');
  act(() => {
    jest.advanceTimersByTime(1);
  });
  expect(result.current.status).toBe('disconnected');
});

it('无会话（sessionKey 为空）透传原始状态并保留断开宽限', () => {
  const { result, rerender } = renderStatus({ sessionKey: null, rawStatus: 'disconnected', enterOutcome: 'pending' });

  expect(result.current.status).toBe('connecting');
  expect(result.current.isEntering).toBe(false);

  act(() => {
    jest.advanceTimersByTime(5000);
  });
  expect(result.current.status).toBe('disconnected');

  rerender({ sessionKey: null, rawStatus: 'connected', enterOutcome: 'pending' });
  expect(result.current.status).toBe('connected');
});
