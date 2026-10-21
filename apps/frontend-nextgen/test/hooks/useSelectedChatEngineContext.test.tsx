/** @jest-environment jsdom */
import { act, renderHook, waitFor } from '@testing-library/react';
import { useSelectedChatEngineContext } from '@/pages/Workspace/Chat/hooks/useSelectedChatEngineContext';
import { loadChatEngineContext } from '@/services/workspace/chatEngineContextService';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useErrorNotifyObserver } from '@/hooks/useErrorNotifyObserver';
import { notifyError } from '@/components/ui/notify';

jest.mock('@/services/workspace/chatEngineContextService');
jest.mock('@/components/ui/notify');
const load = jest.mocked(loadChatEngineContext);
const bot: ChatBotView = { botId: 'bot:owner', realBotId: 'bot', ownerId: 'owner',
  displayName: 'Bot', online: true, chatable: true, engine: 'openclaw', actions: ['restart'] };
const input = { bot, userId: 'caller', sessionId: 's1', origin: 'mine' as const, section: 'managed' as const };
beforeEach(() => { jest.resetAllMocks(); useErrorNotifyStore.getState().reset(); });

test.each([
  { bot: null }, { sessionId: null }, { section: 'friend' as const }, { origin: 'others' as const },
  { bot: { ...bot, botType: 'desktop' } }, { bot: { ...bot, engine: 'teclaw' } }, { bot: { ...bot, online: false } },
])('does not supplement an unselected or ineligible conversation %j', async (override) => {
  const { result } = renderHook(() => useSelectedChatEngineContext({ ...input, ...override }));
  expect(result.current).toBeNull();
  expect(load).not.toHaveBeenCalled();
});

test('only the selected Bot is loaded, and changing the conversation aborts the old lookup', async () => {
  let resolve!: (value: ChatBotView) => void;
  load.mockImplementationOnce(() => new Promise((done) => { resolve = done; })).mockResolvedValueOnce({ ...bot, botId: 'next:owner', realBotId: 'next' });
  const { result, rerender } = renderHook(({ value }) => useSelectedChatEngineContext(value), { initialProps: { value: input } });
  await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  const signal = load.mock.calls[0][2];
  const next = { ...bot, botId: 'next:owner', realBotId: 'next' };
  rerender({ value: { ...input, bot: next, sessionId: 's2' } });
  expect(signal.aborted).toBe(true);
  await waitFor(() => expect(result.current?.realBotId).toBe('next'));
  await act(async () => resolve(bot));
  expect(result.current?.realBotId).toBe('next');
  expect(load.mock.calls.map(([value]) => value.realBotId)).toEqual(['bot', 'next']);
});

test('404 races do not show a global error while chat input stays unchanged', async () => {
  jest.useFakeTimers();
  load.mockImplementation(async () => {
    useErrorNotifyStore.getState().enqueue({ toastKey: 'all-refused', message: 'Not found' });
    throw new BackendRequestError('Not found', { status: 404, apiPath: '/openapi/v1/bots/all', toastKey: 'all-refused' });
  });
  const hook = renderHook(() => { useErrorNotifyObserver(); return useSelectedChatEngineContext(input); });
  try {
    await act(async () => { await Promise.resolve(); });
    act(() => { jest.runOnlyPendingTimers(); });
    expect(hook.result.current).toBeNull();
    expect(notifyError).not.toHaveBeenCalled();
    expect(input.bot.chatable).toBe(true);
  } finally { hook.unmount(); jest.useRealTimers(); }
});

test('does not reuse a previous user lookup after an identity switch', async () => {
  let resolve!: (value: ChatBotView) => void;
  load.mockImplementationOnce(() => new Promise((done) => { resolve = done; })).mockResolvedValueOnce(null);
  const { result, rerender } = renderHook(({ value }) => useSelectedChatEngineContext(value), { initialProps: { value: input } });
  await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  const signal = load.mock.calls[0][2];
  rerender({ value: { ...input, userId: 'next-user' } });
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(bot));
  expect(result.current).toBeNull();
  expect(load.mock.calls.map(([, user]) => user)).toEqual(['caller', 'next-user']);
});
