/** @jest-environment jsdom */
import { renderHook, act, waitFor } from '@testing-library/react';
import { useErrorNotifyObserver } from '@/hooks/useErrorNotifyObserver';
import { useInviteCodeGateStore } from '@/stores/inviteCodeGateStore';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { toast } from 'sonner';
import type { ConversationBotView } from '@/domain/conversation';
import { useConversationSessionCreation } from '@/pages/Workspace/Chat/hooks/useConversationSessionCreation';
import { useConversationStore } from '@/stores/conversationStore';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';

jest.mock('sonner', () => ({ toast: { error: jest.fn(), success: jest.fn() } }));

const friend: ConversationBotView = {
  section: 'friend',
  bot: { botId: 'b', realBotId: 'b', ownerId: 'owner', displayName: 'Bot', chatable: true, online: true, isFriendBot: true },
};
const views = new Map([['b', friend]]);
const originalFetch = global.fetch;
function response(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300, status,
    headers: { get: () => 'application/json' },
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  jest.clearAllMocks();
  useErrorNotifyStore.getState().reset();
  useLoginRedirectStore.getState().reset();
  useInviteCodeGateStore.getState().reset();
  useExternalAuthStore.getState().reset();
  useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
});
afterEach(() => { global.fetch = originalFetch; });

it('500 shows one friendly notification and only a user retry sends another POST', async () => {
  const fetchMock = jest.fn()
    .mockResolvedValueOnce(response(500, { code: 500000, message: 'Internal Server Error', data: null }))
    .mockResolvedValueOnce(response(200, { code: 200000, data: { session_id: 's1', title: '新会话', message_count: 0, gmt_create: '', gmt_modified: '' } }));
  global.fetch = fetchMock as typeof fetch;
  const { result } = renderHook(() => { useErrorNotifyObserver(); return useConversationSessionCreation('123', views); });
  const before = useConversationStore.getState().selectedSessionId;
  await act(async () => { await result.current.createSession('b'); });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
  expect(toast.error).toHaveBeenCalledWith('创建会话失败，请稍后重试', expect.any(Object));
  expect(useConversationStore.getState().selectedSessionId).toBe(before);
  await act(async () => { await result.current.createSession('b'); });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(useConversationStore.getState().selectedSessionId).toBe('s1');
});

it.each([
  ['oauth 401', 401, { message: 'unauthorized' }, 'oauth-provider'],
  ['login envelope', 200, { actionType: 'LOGIN', buserviceErrorCode: 'USER_NOT_LOGIN', decisionBy: 'ACE', buserviceErrorMsg: 'https://login.example.com/' }, 'ace-gateway'],
  ['invite gate', 403, { code: 40300, data: { error_code: 'invite_code_required' } }, 'oauth-provider'],
] as const)('%s is left to the existing authentication flow', async (_name, status, body, strategy) => {
  useLoginStrategyStore.getState().setLoginStrategy(strategy);
  global.fetch = jest.fn().mockResolvedValue(response(status, body)) as typeof fetch;
  const { result } = renderHook(() => useConversationSessionCreation('123', views));
  await act(async () => { await result.current.createSession('b'); });
  expect(global.fetch).toHaveBeenCalledTimes(1);
  expect(toast.error).not.toHaveBeenCalled();
  expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(_name === 'invite gate');
});

it('network failures are displayed in Chinese once without replay', async () => {
  global.fetch = jest.fn().mockRejectedValue(new TypeError('Failed to fetch')) as typeof fetch;
  const { result } = renderHook(() => useConversationSessionCreation('123', views));
  await act(async () => { await result.current.createSession('b'); });
  expect(global.fetch).toHaveBeenCalledTimes(1);
  expect(toast.error).toHaveBeenCalledTimes(1);
  expect(toast.error).toHaveBeenCalledWith('网络异常或请求超时，请检查网络后重试');
  expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
});

it.each([403, 429])('preserves the existing %s message and does not retry', async (status) => {
  global.fetch = jest.fn().mockResolvedValue(response(status, { message: '原始业务提示' })) as typeof fetch;
  const { result } = renderHook(() => useConversationSessionCreation('123', views));
  await act(async () => { await result.current.createSession('b'); });
  expect(global.fetch).toHaveBeenCalledTimes(1);
  expect(useErrorNotifyStore.getState().queue.map((n) => n.message)).toEqual(['原始业务提示']);
  expect(toast.error).not.toHaveBeenCalled();
});
