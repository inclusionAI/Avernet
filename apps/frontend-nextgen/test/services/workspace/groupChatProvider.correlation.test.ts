import { createGroupChatProvider } from '@/services/workspace/groupChatProvider';
import { createSessionToken } from '@/services/backendApi/collaboration/sessionController';
import { describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/sessionController');
jest.mock('@tc-chat/adapters', () => ({ GroupChatProvider: class {} }));

describe('groupChatProvider request correlation', () => {
  it.each([
    [undefined, undefined, 1],
    ['assistant-1', undefined, 2],
    ['assistant-1', 'client-1', 3],
    [undefined, 'client-2', 3],
  ])('separates assistant=%s and client=%s (arity %s)', async (assistantId, clientId, arity) => {
    jest.mocked(createSessionToken).mockResolvedValue({
      code: 20000, message: '', request_id: 'request', data: { token: 'test-token', expires_at: 999 },
    });
    const inner = {
      isConnected: true,
      transport: { send: jest.fn<() => Promise<void>>().mockResolvedValue(undefined) },
      request: jest.fn<(...args: unknown[]) => Promise<void>>().mockResolvedValue(undefined),
      connect: jest.fn<() => Promise<void>>().mockResolvedValue(undefined),
      subscribeToConnectionStatus: () => () => {},
    };
    const provider = createGroupChatProvider({
      sessionId: 'session', groupId: 'group', identityId: 'human_1', wsOrigin: 'wss://test.example',
      createSdkProvider: () => inner as never,
    });
    await provider.request({ content: 'same content', sessionId: 'session' }, assistantId as string | undefined, clientId as string | undefined);
    const args = inner.request.mock.calls[0];
    expect(args).toEqual([expect.any(Object), assistantId, clientId].slice(0, Number(arity)));
  });
});
