/** @jest-environment jsdom */
import type { GroupView, ParticipantView } from '@/domain/collaboration';
import { GroupChatBubble } from '@/pages/Workspace/components/GroupChatPane/GroupChatBubble';
import { createSessionToken } from '@/services/backendApi/collaboration/sessionController';
import { createGroupChatProvider } from '@/services/workspace/groupChatProvider';
import { GroupChatProvider as SdkGroupChatProvider } from '@tc-chat/adapters';
import type { ChatMessage, TextBlock } from '@tc-chat/core';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

jest.mock('@/services/backendApi/collaboration/sessionController');
jest.mock('@tc-chat/ui/es/Bubble', () => ({
  Bubble: ({ sender }: { sender: { align: string } }) => <div data-testid="bubble" data-align={sender.align} />,
}));
jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({ aixUiPlugin: {}, fileRefPlugin: {} }));

const group: GroupView = {
  groupId: 'group', name: '协作群', kind: 'free_chat', status: 'active', participants: [], sessions: [],
  participantCount: 2, isPublic: false, deliveryPolicy: 'send_to_driver', createdAt: 1, lastMessageAt: 1,
};
const participants: ParticipantView[] = [
  { actorId: 'human_A', kind: 'human', name: '用户A', avatarUrl: '/A.png', role: 'member', mode: 'present' },
  { actorId: 'human_B', kind: 'human', name: '用户B', avatarUrl: '/B.png', role: 'member', mode: 'present' },
];

// BCS build_workbench_user_event 的真实帧结构；只隔离网络，不替换 SDK 解析器。
const frame = {
  type: 'event', event: 'chat', group_id: 'group', bot_uuid: 'human_A', bot_name: '用户A',
  payload: {
    run_id: 'run_A', session_key: 'session', bcs_session_id: 'session', seq: 0, state: 'final',
    message: { role: 'user', content: [{ type: 'text', text: 'hello' }], from: 'human_A', from_name: '用户A', mentions: [] },
  },
};

type TestTransport = {
  readonly isConnected: boolean;
  connect: () => Promise<void>;
  send: (frame: unknown) => Promise<void>;
  onMessage: (frame: unknown) => void;
};
const providers: ReturnType<typeof createGroupChatProvider>[] = [];

async function connect(viewer: string) {
  const sdk = new SdkGroupChatProvider({ url: 'ws://localhost/unused', groupId: 'group', currentUserId: viewer });
  const transport = (sdk as unknown as { transport: TestTransport }).transport;
  jest.spyOn(transport, 'connect').mockResolvedValue(undefined);
  jest.spyOn(transport, 'send').mockResolvedValue(undefined);
  jest.spyOn(transport, 'isConnected', 'get').mockReturnValue(true);
  const provider = createGroupChatProvider({
    sessionId: 'session', groupId: 'group', identityId: viewer,
    wsOrigin: 'ws://localhost', createSdkProvider: () => sdk,
  });
  providers.push(provider);
  const updates: ChatMessage[] = [];
  const completions: ChatMessage[] = [];
  provider.onMessage = (message) => updates.push(message);
  provider.onComplete = (messages) => completions.push(...messages);
  await provider.connect();
  return { sdk, transport, provider, updates, completions };
}

beforeEach(() => {
  jest.mocked(createSessionToken).mockResolvedValue({
    code: 20000, message: '', request_id: 'request', data: { token: 'test-token', expires_at: 999 },
  });
});
afterEach(() => {
  providers.splice(0).forEach((provider) => provider.disconnect());
  jest.restoreAllMocks();
});

describe.each([false, true])('history hydration buffering=%s', (buffered) => {
  it.each([
    ['A', 'updates', 'right'], ['A', 'completions', 'right'],
    ['B', 'updates', 'left'], ['B', 'completions', 'left'],
  ] as const)('viewer %s sees the actual author through %s', async (viewer, callback, align) => {
    const context = await connect(`human_${viewer}`);
    if (!buffered) context.provider.enterLiveMode();
    context.transport.onMessage(frame);
    expect(context.updates.length > 0).toBe(!buffered);
    expect(context.completions.length > 0).toBe(!buffered);
    if (buffered) context.provider.enterLiveMode();
    const messages = context[callback];
    expect(messages.length).toBeGreaterThan(0);
    const message = messages[messages.length - 1];
    render(<GroupChatBubble message={message} group={group} participants={participants}
      isLastMessage={false} isRequesting={false} userIdentityId={viewer}
      userIdentityName={`用户${viewer}`} userAvatarUrl={`/${viewer}.png`} />);
    expect(screen.getByTestId('bubble')).toHaveAttribute('data-align', align);
    expect(screen.getByTestId('message-sender-meta')).toHaveTextContent('用户A');
    expect(screen.getByRole('img', { name: '用户A' })).toHaveAttribute('src', '/A.png');
    expect(message.extra?.senderId).toBe('human_A');
    expect(message.content).toBe('hello');
  });
});

it('preserves explicit canonical metadata and unrelated message fields on both callbacks', async () => {
  const { provider, sdk, updates, completions } = await connect('human_B');
  const message: ChatMessage = {
    id: 'canonical', role: 'user', content: 'hello', status: 'done',
    blocks: [{ type: 'text', content: 'hello' } as TextBlock],
    extra: { senderId: 'human_A', senderName: '用户A', senderAvatarUrl: '/A.png', runId: 'run_A',
      payload: { message: { from: 'human_old', from_name: '旧名', from_avatar: '/old.png' } } },
  };
  // 直接回调同时覆盖宿主暂存队列（上面的原始帧则覆盖 SDK hydration 暂存）。
  sdk.onMessage?.(message);
  sdk.onComplete?.([message]);
  expect(updates).toHaveLength(0);
  provider.enterLiveMode();
  expect(updates).toEqual([message]);
  expect(completions).toEqual([message]);
});

it.each(['payload', 'sdk'] as const)('maps %s avatar metadata without requiring a participant record', async (source) => {
  const { provider, sdk, updates, completions } = await connect('human_B');
  provider.enterLiveMode();
  const message: ChatMessage = {
    id: 'avatar', role: 'user', content: 'hello', status: 'done',
    extra: { payload: { message: { from: 'human_A', from_name: '用户A',
      ...(source === 'payload' ? { from_avatar: '/A.png' } : {}) } },
      ...(source === 'sdk' ? { senderAvatar: '/A.png' } : {}) },
  };
  sdk.onMessage?.(message);
  sdk.onComplete?.([message]);
  for (const result of [updates[0], completions[0]]) {
    expect(result.extra).toMatchObject({ senderId: 'human_A', senderName: '用户A', senderAvatarUrl: '/A.png' });
  }
  render(<GroupChatBubble message={updates[0]} group={group} participants={[]}
    isLastMessage={false} isRequesting={false} userIdentityId="B" userIdentityName="用户B" userAvatarUrl="/B.png" />);
  expect(screen.getByRole('img', { name: '用户A' })).toHaveAttribute('src', '/A.png');
  expect(message.extra?.senderId).toBeUndefined();
});

it.each([undefined, null, 'invalid', [], { message: null }, { message: { from: 123, from_name: [], from_avatar: {} } }])(
  'does not infer an unknown sender from the viewer or routing target: %j', async (payload) => {
    const { provider, sdk, updates, completions } = await connect('human_B');
    provider.enterLiveMode();
    const message: ChatMessage = {
      id: 'unknown', role: 'user', content: 'hello', status: 'done', extra: { botUuid: 'human_B', payload },
    };
    sdk.onMessage?.(message);
    sdk.onComplete?.([message]);
    for (const result of [updates[0], completions[0]]) {
      expect(result.extra?.senderId).toBeUndefined();
      expect(result.extra?.senderName).toBeUndefined();
      expect(result.extra?.senderAvatarUrl).toBeUndefined();
      expect(result).toEqual(message);
    }
  },
);
