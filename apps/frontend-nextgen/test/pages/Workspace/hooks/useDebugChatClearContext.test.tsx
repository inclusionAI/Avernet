/** @jest-environment jsdom */
import { useDebugChatClearContext } from '@/pages/Workspace/hooks/useDebugChatClearContext';
import { botSessionService, type ChatBotView } from '@/services/workspace/botSessionService';
import { act, renderHook } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@/services/workspace/botSessionService', () => ({ botSessionService: { clearContext: jest.fn() } }));
jest.mock('sonner');
const clear = jest.mocked(botSessionService.clearContext);
const bot = { botId: 'b', ownerId: 'owner', botType: 'service', runtimeStage: 'draft' } as ChatBotView;
beforeEach(() => jest.clearAllMocks());
it('clears exactly the debug session and reloads only after success', async () => {
  clear.mockResolvedValue({ ok: true, data: null });
  const reload = jest.fn(async () => {});
  const { result } = renderHook(() => useDebugChatClearContext(bot, 'viewer', 'session', reload));
  await act(async () => result.current());
  expect(clear).toHaveBeenCalledWith(bot, 'viewer', 'session');
  expect(reload).toHaveBeenCalledTimes(1);
});
it('reports failure and does not reload', async () => {
  clear.mockResolvedValue({ ok: false, error: { friendlyMessage: 'clear failed' } } as never);
  const reload = jest.fn(async () => {});
  const { result } = renderHook(() => useDebugChatClearContext(bot, 'viewer', 'session', reload));
  await act(async () => result.current());
  expect(toast.error).toHaveBeenCalledWith('clear failed');
  expect(reload).not.toHaveBeenCalled();
});
it.each([[null, 'session'], ['viewer', undefined]])('does not clear without viewer/session', async (viewer, session) => {
  const { result } = renderHook(() => useDebugChatClearContext(bot, viewer ?? null, session ?? undefined, jest.fn()));
  await act(async () => result.current());
  expect(clear).not.toHaveBeenCalled();
});
