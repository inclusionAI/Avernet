/** @jest-environment jsdom */
import { useConversationHistory } from '@/pages/Workspace/Chat/hooks/useConversationHistory';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { managedBotConversationService } from '@/services/workspace/managedBotConversationService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import type { ChatMessage, TextBlock } from '@tc-chat/core';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/managedBotConversationService', () => ({
  managedBotConversationService: {
    listOtherMessages: require('jest-mock').fn(),
  },
}));

const service = managedBotConversationService as jest.Mocked<typeof managedBotConversationService>;

const managedBot: ChatBotView = {
  botId: '327325:1',
  realBotId: '327325',
  ownerId: '1',
  displayName: '皮皮虾',
  online: true,
  chatable: true,
};

const message = (id: string, role: 'user' | 'assistant' = 'assistant'): ChatMessage => ({
  id,
  role,
  content: id,
  status: 'history',
  blocks: [{ type: 'text', content: id }] as TextBlock[],
});

const success = (messages: ChatMessage[], total: number, page = 1, hasMore?: boolean) => ({
  ok: true as const,
  data: {
    messages,
    page,
    total,
    rawCount: messages.length,
    hasMore: hasMore ?? page * 50 < total,
  },
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

beforeEach(() => {
  jest.clearAllMocks();
  service.listOtherMessages.mockReset();
});

describe('useConversationHistory', () => {
  it('loads the first page and decides hasMore from raw count/total', async () => {
    service.listOtherMessages.mockResolvedValue(success([message('new-2'), message('new-1', 'user')], 3, 1, true));

    const { result } = renderHook(() =>
      useConversationHistory({ bot: managedBot, friendUserId: '447147', sessionId: 's1' }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.messages.map((item) => item.id)).toEqual(['new-2', 'new-1']);
    expect(result.current.hasMore).toBe(true);
    expect(result.current.error).toBeNull();
    expect(service.listOtherMessages).toHaveBeenCalledWith(managedBot, '447147', 's1', 1);
  });

  it('prepends older messages and keeps hasMore based on raw response count', async () => {
    service.listOtherMessages
      .mockResolvedValueOnce(success([message('new-1')], 2, 1, true))
      .mockResolvedValueOnce(success([message('old-1')], 2, 2, false));

    const { result } = renderHook(() =>
      useConversationHistory({ bot: managedBot, friendUserId: '447147', sessionId: 's1' }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => result.current.loadMore());
    expect(result.current.messages.map((item) => item.id)).toEqual(['old-1', 'new-1']);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.isLoadingMore).toBe(false);
  });

  it('guards against concurrent load-more requests and surfaces a retryable error', async () => {
    const pending = deferred<Awaited<ReturnType<typeof service.listOtherMessages>>>();
    service.listOtherMessages
      .mockResolvedValueOnce(success([message('m1')], 3, 1, true))
      .mockReturnValueOnce(pending.promise);
    const { result } = renderHook(() =>
      useConversationHistory({ bot: managedBot, friendUserId: '447147', sessionId: 's1' }),
    );
    await waitFor(() => expect(result.current.hasMore).toBe(true));

    let first!: Promise<void>;
    let second!: Promise<void>;
    act(() => {
      first = result.current.loadMore();
      second = result.current.loadMore();
    });
    expect(service.listOtherMessages).toHaveBeenCalledTimes(2);

    await act(async () => {
      pending.resolve({
        ok: false as const,
        error: { code: 'FAILED', friendlyMessage: '加载更早消息失败', canRetry: true },
      });
      await Promise.all([first, second]);
    });
    expect(result.current.messages.map((item) => item.id)).toEqual(['m1']);
    expect(result.current.loadMoreError).toBe('加载更早消息失败');
    expect(result.current.isLoadingMore).toBe(false);
  });

  it('does not request without a complete selection and supports first-page retry', async () => {
    const { result, rerender } = renderHook(
      ({ bot, friendUserId, sessionId }) => useConversationHistory({ bot, friendUserId, sessionId }),
      {
        initialProps: {
          bot: null as ChatBotView | null,
          friendUserId: null as string | null,
          sessionId: null as string | null,
        },
      },
    );
    expect(service.listOtherMessages).not.toHaveBeenCalled();
    expect(result.current.messages).toEqual([]);
    expect(result.current.loading).toBe(false);

    service.listOtherMessages
      .mockResolvedValueOnce({
        ok: false as const,
        error: { code: 'FAILED', friendlyMessage: '加载好友用户历史消息失败，请稍后重试。', canRetry: true },
      })
      .mockResolvedValueOnce(success([message('m1')], 1, 1, false));
    rerender({ bot: managedBot, friendUserId: '447147', sessionId: 's1' });
    await waitFor(() => expect(result.current.error).toBe('加载好友用户历史消息失败，请稍后重试。'));
    expect(result.current.messages).toEqual([]);

    act(() => result.current.retry());
    await waitFor(() => expect(result.current.messages.map((item) => item.id)).toEqual(['m1']));
    expect(result.current.error).toBeNull();
  });

  it('ignores a stale response after switching sessions', async () => {
    const stale = deferred<Awaited<ReturnType<typeof service.listOtherMessages>>>();
    service.listOtherMessages
      .mockReturnValueOnce(stale.promise)
      .mockResolvedValueOnce(success([message('new')], 1, 1, false));
    const { result, rerender } = renderHook(
      ({ sessionId }) => useConversationHistory({ bot: managedBot, friendUserId: '447147', sessionId }),
      { initialProps: { sessionId: 'old-session' } },
    );

    rerender({ sessionId: 'new-session' });
    await waitFor(() => expect(result.current.messages[0]?.id).toBe('new'));
    await act(async () => {
      stale.resolve(success([message('old')], 1, 1, false));
      await stale.promise;
    });
    expect(result.current.messages.map((item) => item.id)).toEqual(['new']);
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });
});
