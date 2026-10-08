/** @jest-environment jsdom */
import { useBotFriendHistory } from '@/pages/Workspace/hooks/useBotFriendHistory';
import { botFriendConversationService } from '@/services/workspace/botFriendConversationService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import type { ChatMessage, TextBlock } from '@tc-chat/core';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botFriendConversationService', () => ({
  BOT_FRIEND_MESSAGE_PAGE_SIZE: 50,
  botFriendConversationService: {
    listMessagesPage: require('jest-mock').fn(),
  },
}));

const service = botFriendConversationService as jest.Mocked<typeof botFriendConversationService>;
const message = (id: string, role: 'user' | 'assistant' = 'assistant'): ChatMessage => ({
  id,
  role,
  content: id,
  status: 'history',
  blocks: [{ type: 'text', content: id }] as TextBlock[],
});
const success = (messages: ChatMessage[], total: number) => ({
  ok: true as const,
  data: { messages, total, rawCount: messages.length },
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
  service.listMessagesPage.mockReset();
});

describe('useBotFriendHistory', () => {
  it('loads the first page and decides hasMore from raw counts', async () => {
    service.listMessagesPage.mockResolvedValue(success([message('m2'), message('m1', 'user')], 3));

    const { result } = renderHook(() =>
      useBotFriendHistory({
        botIdentityId: 'bot-a:327325',
        friendUserId: '447147',
        sessionId: 'session-1',
      }),
    );

    await waitFor(() => expect(result.current.messages.map((item) => item.id)).toEqual(['m2', 'm1']));
    expect(result.current.hasMore).toBe(true);
    expect(service.listMessagesPage).toHaveBeenCalledWith('bot-a:327325', '447147', 'session-1', 1, 50);
  });

  it('prepends older messages uniquely and prevents duplicate concurrent requests', async () => {
    const pending = deferred<Awaited<ReturnType<typeof service.listMessagesPage>>>();
    service.listMessagesPage
      .mockResolvedValueOnce(success([message('m2'), message('m1')], 4))
      .mockReturnValueOnce(pending.promise);
    const { result } = renderHook(() =>
      useBotFriendHistory({
        botIdentityId: 'bot-a:327325',
        friendUserId: '447147',
        sessionId: 'session-1',
      }),
    );
    await waitFor(() => expect(result.current.hasMore).toBe(true));

    let first!: Promise<void>;
    let second!: Promise<void>;
    act(() => {
      first = result.current.loadMore();
      second = result.current.loadMore();
    });
    expect(service.listMessagesPage).toHaveBeenCalledTimes(2);

    await act(async () => {
      pending.resolve(success([message('m4'), message('m3'), message('m2')], 4));
      await Promise.all([first, second]);
    });
    expect(result.current.messages.map((item) => item.id)).toEqual(['m4', 'm3', 'm2', 'm1']);
    expect(result.current.hasMore).toBe(false);
  });

  it('keeps existing messages and exposes a retryable load-more error', async () => {
    service.listMessagesPage.mockResolvedValueOnce(success([message('m1')], 2)).mockResolvedValueOnce({
      ok: false,
      error: { code: 'FAILED', friendlyMessage: '加载更早消息失败', canRetry: true },
    });
    const { result } = renderHook(() =>
      useBotFriendHistory({
        botIdentityId: 'bot-a:327325',
        friendUserId: '447147',
        sessionId: 'session-1',
      }),
    );
    await waitFor(() => expect(result.current.hasMore).toBe(true));

    await act(async () => result.current.loadMore());

    expect(result.current.messages.map((item) => item.id)).toEqual(['m1']);
    expect(result.current.loadMoreError).toBe('加载更早消息失败');
  });

  it('does not request without a complete selection and supports first-page retry', async () => {
    const { result, rerender } = renderHook(
      ({ sessionId }) => useBotFriendHistory({ botIdentityId: 'bot-a:327325', friendUserId: '447147', sessionId }),
      { initialProps: { sessionId: null as string | null } },
    );
    expect(service.listMessagesPage).not.toHaveBeenCalled();

    service.listMessagesPage
      .mockResolvedValueOnce({
        ok: false,
        error: { code: 'FAILED', friendlyMessage: '历史消息加载失败', canRetry: true },
      })
      .mockResolvedValueOnce(success([message('m1')], 1));
    rerender({ sessionId: 'session-1' });
    await waitFor(() => expect(result.current.error).toBe('历史消息加载失败'));

    act(() => result.current.retry());
    await waitFor(() => expect(result.current.messages.map((item) => item.id)).toEqual(['m1']));
  });

  it('ignores a late response after switching sessions', async () => {
    const first = deferred<Awaited<ReturnType<typeof service.listMessagesPage>>>();
    service.listMessagesPage.mockReturnValueOnce(first.promise).mockResolvedValueOnce(success([message('new')], 1));
    const { result, rerender } = renderHook(
      ({ sessionId }) => useBotFriendHistory({ botIdentityId: 'bot-a:327325', friendUserId: '447147', sessionId }),
      { initialProps: { sessionId: 'old' } },
    );

    rerender({ sessionId: 'new' });
    await waitFor(() => expect(result.current.messages[0]?.id).toBe('new'));
    await act(async () => {
      first.resolve(success([message('old')], 1));
      await first.promise;
    });
    expect(result.current.messages[0]?.id).toBe('new');
  });
});
