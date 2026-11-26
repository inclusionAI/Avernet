/** @jest-environment jsdom */
import type {
  CodingBotSessionStatusHost,
  CodingBotSessionStatusQueryInput,
  CodingBotSessionStatusQueryResult,
} from '@/capabilities';
import { useCodingSessionStatusPolling } from '@/pages/Workspace/Chat/hooks/useCodingSessionStatusPolling';
import { useConversationStore } from '@/stores/conversationStore';
import { useSessionStatusStore } from '@/stores/sessionStatusStore';
import { act, renderHook } from '@testing-library/react';

const botA = { botId: 'bot-a:owner', realBotId: 'bot-a', ownerId: 'owner' };
const botB = { botId: 'bot-b:owner', realBotId: 'bot-b', ownerId: 'owner' };
const sessionId = 'user:u1:session:s-1:agent:bot-a';
const status = { code: 'running', label: '进行中', tone: 'info' as const };

function knownSessionList() {
  return {
    items: [
      {
        sessionId,
        botId: botA.botId,
        title: 'Session',
        messageCount: 1,
        gmtModified: '',
        gmtCreate: '',
      },
    ],
    page: 1,
    total: 1,
    hasMore: false,
    loading: false,
    error: null,
    isLoadingMore: false,
    loadMoreError: null,
  };
}

function sessionList(ids: string[] = [sessionId]) {
  return {
    items: ids.map((id) => ({
      sessionId: id,
      botId: botA.botId,
      title: 'Session',
      messageCount: 1,
      gmtModified: '',
      gmtCreate: '',
    })),
    page: 1,
    total: ids.length,
    hasMore: false,
    loading: false,
    error: null,
    isLoadingMore: false,
    loadMoreError: null,
  };
}

function setManagedSessionView(origin: 'mine' | 'others' | 'collab', ids: string[]) {
  useConversationStore.getState().setManagedBotCache(botA.botId, {
    botId: botA.botId,
    origin,
    scope: 'all',
    sessions: sessionList(ids),
    friendDirectory: { items: [], loading: false, error: null },
    friendGroups: {},
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  useConversationStore.getState().reset();
  useSessionStatusStore.setState({
    contextId: null,
    botId: null,
    ready: false,
    snapshotReady: false,
    statuses: {},
  });
  useConversationStore.getState().setFriendBotSessions(botA.botId, knownSessionList());
});

afterEach(() => {
  act(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });
  Object.defineProperty(document, 'hidden', { value: false, configurable: true });
});

const flushQuery = async () => {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
};

it('queries immediately, then every twenty seconds without re-creating the context', async () => {
  const query = jest.fn().mockResolvedValue({ ok: true, items: [{ sessionId, status }] });
  const provider: CodingBotSessionStatusHost = { query };
  renderHook(() => useCodingSessionStatusPolling({ activeBot: botA, userId: 'u1', provider }));

  await act(async () => {
    await flushQuery();
  });
  expect(query).toHaveBeenCalledTimes(1);
  const contextId = useSessionStatusStore.getState().contextId;

  for (let tick = 1; tick <= 2; tick += 1) {
    await act(async () => {
      jest.advanceTimersByTime(20_000);
      await flushQuery();
    });
    expect(query).toHaveBeenCalledTimes(tick + 1);
  }
  expect(useSessionStatusStore.getState().contextId).toBe(contextId);
  expect(useSessionStatusStore.getState().ready).toBe(true);
  expect(useSessionStatusStore.getState().snapshotReady).toBe(true);
  expect(useSessionStatusStore.getState().statuses[sessionId]).toEqual(status);
});

it('skips ticks while a query is in flight to avoid request piling', async () => {
  const query = jest.fn(() => new Promise<CodingBotSessionStatusQueryResult>(() => {
    // Never settle to keep the request in flight.
  }));
  const provider: CodingBotSessionStatusHost = { query };
  renderHook(() => useCodingSessionStatusPolling({ activeBot: botA, userId: 'u1', provider }));

  await act(async () => {
    jest.advanceTimersByTime(60_000);
  });
  expect(query).toHaveBeenCalledTimes(1);
});

it('aborts the previous Bot request and switches the status owner when Bot changes', () => {
  let firstSignal: AbortSignal | undefined;
  const query = jest.fn((input: CodingBotSessionStatusQueryInput) =>
    new Promise<CodingBotSessionStatusQueryResult>(() => {
      if (input.viewBotId === botA.botId) firstSignal = input.signal;
    }),
  );
  const provider: CodingBotSessionStatusHost = { query };
  const { rerender } = renderHook(
    ({ bot }) => useCodingSessionStatusPolling({ activeBot: bot, userId: 'u1', provider }),
    { initialProps: { bot: botA } },
  );

  expect(query).toHaveBeenCalledWith(
    expect.objectContaining({ viewBotId: botA.botId, botId: botA.realBotId, signal: expect.any(AbortSignal) }),
  );
  rerender({ bot: botB });

  expect(firstSignal?.aborted).toBe(true);
  expect(query).toHaveBeenLastCalledWith(
    expect.objectContaining({ viewBotId: botB.botId, botId: botB.realBotId }),
  );
  expect(useSessionStatusStore.getState().botId).toBe(botB.botId);
});

it('pauses on hidden visibility and immediately refreshes when visible again', async () => {
  const query = jest.fn(() => Promise.resolve<CodingBotSessionStatusQueryResult>({ ok: true, items: [] }));
  const provider: CodingBotSessionStatusHost = { query };
  renderHook(() => useCodingSessionStatusPolling({ activeBot: botA, userId: 'u1', provider }));
  await act(async () => {
    await flushQuery();
  });
  expect(query).toHaveBeenCalledTimes(1);

  act(() => {
    Object.defineProperty(document, 'hidden', { value: true, configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await act(async () => {
    jest.advanceTimersByTime(20_000);
    await flushQuery();
  });
  expect(query).toHaveBeenCalledTimes(1);

  await act(async () => {
    Object.defineProperty(document, 'hidden', { value: false, configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
    await flushQuery();
  });
  expect(query).toHaveBeenCalledTimes(2);
});

it('keeps the last successful statuses when a provider query fails', async () => {
  const query = jest
    .fn()
    .mockResolvedValueOnce({ ok: true, items: [{ sessionId, status }] })
    .mockResolvedValueOnce({ ok: false, message: 'temporary failure' });
  const provider: CodingBotSessionStatusHost = { query };
  renderHook(() => useCodingSessionStatusPolling({ activeBot: botA, userId: 'u1', provider }));

  await act(async () => {
    await flushQuery();
  });
  expect(useSessionStatusStore.getState().statuses[sessionId]).toEqual(status);

  await act(async () => {
    jest.advanceTimersByTime(20_000);
    await flushQuery();
  });
  expect(query).toHaveBeenCalledTimes(2);
  expect(useSessionStatusStore.getState().statuses[sessionId]).toEqual(status);
});


it.each(['others', 'collab'] as const)(
  'immediately queries again when the same Coding Bot switches from mine to %s',
  async (origin) => {
    const mineSessionId = 'user:u1:session:s-mine:agent:bot-a';
    const switchedSessionId = `user:${origin === 'others' ? 'u2' : 'collab-1'}:session:s-switched:agent:bot-a`;
    act(() => {
      useConversationStore.getState().setFriendBotSessions(botA.botId, sessionList([]));
    });
    act(() => {
      setManagedSessionView('mine', [mineSessionId]);
    });
    const query = jest.fn().mockResolvedValue({ ok: true, items: [] });
    const provider: CodingBotSessionStatusHost = { query };
    renderHook(() => useCodingSessionStatusPolling({ activeBot: botA, userId: 'u1', provider }));
    await act(async () => {
      await flushQuery();
    });
    expect(query).toHaveBeenCalledTimes(1);
    expect(query.mock.calls[0][0].sessionIds).toEqual([mineSessionId]);

    act(() => {
      useConversationStore.getState().setManagedBotOrigin(botA.botId, origin);
      setManagedSessionView(origin, [switchedSessionId]);
    });
    await act(async () => {
      await flushQuery();
    });

    expect(query).toHaveBeenCalledTimes(2);
    expect(query.mock.calls[1][0].sessionIds).toEqual([switchedSessionId]);
    expect(useSessionStatusStore.getState().botId).toBe(botA.botId);
  },
);

it('queries once after a settled load-more page, without recreating the polling context', async () => {
  const loadedMoreSessionId = 'user:u1:session:s-more:agent:bot-a';
  const query = jest.fn().mockResolvedValue({ ok: true, items: [] });
  const provider: CodingBotSessionStatusHost = { query };
  renderHook(() => useCodingSessionStatusPolling({ activeBot: botA, userId: 'u1', provider }));
  await act(async () => {
    await flushQuery();
  });
  expect(query).toHaveBeenCalledTimes(1);
  const contextId = useSessionStatusStore.getState().contextId;

  act(() => {
    useConversationStore.getState().setFriendBotSessions(botA.botId, sessionList([sessionId, loadedMoreSessionId]));
  });
  await act(async () => {
    await flushQuery();
  });

  expect(query).toHaveBeenCalledTimes(2);
  expect(query.mock.calls[1][0].sessionIds).toEqual([sessionId, loadedMoreSessionId]);
  expect(useSessionStatusStore.getState().contextId).toBe(contextId);
});

it('queues one list refresh while an earlier status query is in flight', async () => {
  const loadedMoreSessionId = 'user:u1:session:s-load-more:agent:bot-a';
  let resolveFirst: ((value: CodingBotSessionStatusQueryResult) => void) | undefined;
  const query = jest
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise<CodingBotSessionStatusQueryResult>((resolve) => {
          resolveFirst = resolve;
        }),
    )
    .mockResolvedValue({ ok: true, items: [] });
  const provider: CodingBotSessionStatusHost = { query };
  renderHook(() => useCodingSessionStatusPolling({ activeBot: botA, userId: 'u1', provider }));
  await act(async () => {
    await flushQuery();
  });
  act(() => {
    useConversationStore.getState().setFriendBotSessions(botA.botId, sessionList([sessionId, loadedMoreSessionId]));
  });
  expect(query).toHaveBeenCalledTimes(1);

  await act(async () => {
    resolveFirst?.({ ok: true, items: [] });
    await flushQuery();
  });

  expect(query).toHaveBeenCalledTimes(2);
  expect(query.mock.calls[1][0].sessionIds).toEqual([sessionId, loadedMoreSessionId]);
});
