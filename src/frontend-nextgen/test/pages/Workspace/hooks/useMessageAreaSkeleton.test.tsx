/** @jest-environment jsdom */
import { useMessageAreaSkeleton } from '@/pages/Workspace/hooks/useMessageAreaSkeleton';
import { afterEach, beforeEach, expect, it, jest } from '@jest/globals';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import type { ChatMessage } from '@tc-chat/core';
import { act, renderHook } from '@testing-library/react';

interface CaseProps {
  messageCount: number;
  status: ProviderConnectionStatus;
}

const message = (i: number) =>
  ({ id: `m${i}`, role: 'user', content: `消息${i}`, status: 'history' } as never as ChatMessage);

const renderSkeleton = (initial: CaseProps) =>
  renderHook(
    (p: CaseProps) =>
      useMessageAreaSkeleton({
        messages: Array.from({ length: p.messageCount }, (_, i) => message(i)),
        status: p.status,
      }),
    {
      initialProps: initial,
    },
  );

beforeEach(() => {
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

it('消息为空且未在线：一律骨架屏（进入窗口，不受空态确认宽限影响）', () => {
  const { result } = renderSkeleton({ messageCount: 0, status: 'connecting' });

  expect(result.current).toBe(true);
  act(() => {
    jest.advanceTimersByTime(5000);
  });
  // 非在线的空消息区永远骨架屏——不确认空态。
  expect(result.current).toBe(true);
});

it('消息为空且已在线：宽限期内骨架屏，超时后才确认空态（真空会话终态）', () => {
  const { result, rerender } = renderSkeleton({ messageCount: 0, status: 'connecting' });

  act(() => {
    rerender({ messageCount: 0, status: 'connected' });
  });
  expect(result.current).toBe(true);

  act(() => {
    jest.advanceTimersByTime(999);
  });
  expect(result.current).toBe(true);

  act(() => {
    jest.advanceTimersByTime(1);
  });
  expect(result.current).toBe(false);
});

it('宽限期内消息到达：直接撤骨架屏显示内容，空态从未出现', () => {
  const { result, rerender } = renderSkeleton({ messageCount: 0, status: 'connected' });

  act(() => {
    jest.advanceTimersByTime(600);
  });
  expect(result.current).toBe(true);

  // 1~2 秒级的历史内容到达（宽限期内）——骨架屏直切内容，无空态中间态。
  act(() => {
    rerender({ messageCount: 3, status: 'connected' });
  });
  expect(result.current).toBe(false);
});

it('消息已到达但连接未完成：骨架屏持续到连接完成（顶栏绿与历史同帧出现）', () => {
  const { result, rerender } = renderSkeleton({ messageCount: 0, status: 'connecting' });

  // 历史先于 WebSocket 连接到达（单聊 HTTP 快、WS 慢）——骨架屏不撤，历史暂不显示。
  act(() => {
    rerender({ messageCount: 3, status: 'connecting' });
  });
  expect(result.current).toBe(true);

  // 连接完成：顶栏 connected 与历史消息内容同帧出现。
  act(() => {
    rerender({ messageCount: 3, status: 'connected' });
  });
  expect(result.current).toBe(false);
});

it('空态确认后掉线：重新进入等待空态确认，再次上线且仍空则再等宽限', () => {
  const { result, rerender } = renderSkeleton({ messageCount: 0, status: 'connected' });
  act(() => {
    jest.advanceTimersByTime(1000);
  });
  expect(result.current).toBe(false);

  // 掉线（重连中）→ 回到骨架屏（不再维持已确认的空态）。
  act(() => {
    rerender({ messageCount: 0, status: 'reconnecting' });
  });
  expect(result.current).toBe(true);

  // 重新上线且仍空：从头计宽限，而不是立即空态。
  act(() => {
    rerender({ messageCount: 0, status: 'connected' });
  });
  expect(result.current).toBe(true);
  act(() => {
    jest.advanceTimersByTime(500);
  });
  expect(result.current).toBe(true);
  act(() => {
    jest.advanceTimersByTime(500);
  });
  expect(result.current).toBe(false);
});
