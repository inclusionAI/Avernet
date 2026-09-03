import type { PrivateChatSession } from '@/services/backendApi/privateChat/privateChatController';
import {
  TEAMCLAW_SUPPORT_BOT,
  TeamClawSupportProvider,
  buildPrivateWebsocketUrl,
  mapPrivateHistoryMessages,
  resolvePrivateWebsocketPath,
} from '@/services/workspace';
import { describe, expect, it, jest } from '@jest/globals';
import type { OpenClawProvider } from '@tc-chat/adapters';

jest.mock('@tc-chat/adapters', () => ({
  OpenClawProvider: class MockOpenClawProvider {},
}));

const readySession: PrivateChatSession = {
  session_key: 'agent:main:客服',
  is_new: false,
  need_poll: false,
  connection: {
    type: 'remote',
    target: 'runtime@0:20003',
    token: 'proxy-token',
    engine_type: 'openclaw',
  },
};

describe('workspaceService online TeamClaw support', () => {
  it('builds engine-specific websocket URLs', () => {
    expect(resolvePrivateWebsocketPath('aicoding')).toBe('/api/ws');
    // buildPrivateWebsocketUrl now uses buildWsUrlFromRelative (tern proxy in deployment,
    // same-origin fallback in dev). In node test env (no window/tern config), verify path is correct.
    const wsUrl = buildPrivateWebsocketUrl(readySession.connection!);
    expect(wsUrl).toContain('/proxypass/runtime@0:20003/api/openclaw/ws');

    expect(buildPrivateWebsocketUrl({ ...readySession.connection!, type: 'local', target: '127.0.0.1:20003' })).toBe(
      'ws://127.0.0.1:20003/api/openclaw/ws',
    );
  });

  it('maps history into SDK messages and filters unsupported records', () => {
    const messages = mapPrivateHistoryMessages([
      { id: 'u1', role: 'user', content: '你好', gmt_created: '2026-08-12T08:00:00Z' },
      { id: 'a1', role: 'assistant', content: [{ text: '你好，' }, { text: '有什么可以帮你？' }] },
      { id: 'x1', role: 'unknown', content: 'ignore' },
    ]);

    expect(messages).toHaveLength(2);
    expect(messages[0]).toMatchObject({ id: 'u1', role: 'user', status: 'history' });
    expect(messages[1]).toMatchObject({
      id: 'a1',
      role: 'assistant',
      content: '你好，\n有什么可以帮你？',
      blocks: [{ type: 'text' }],
    });
  });

  it('maps private-session image attachments to the same ImageBlock fallback as group messages', () => {
    const messages = mapPrivateHistoryMessages([
      {
        id: 'u-image',
        role: 'user',
        content: '请看图',
        attachments: [
          {
            attachment_id: 'att-1',
            type: 'image',
            file_name: '截图.png',
            mime_type: 'image/png',
            url: 'https://cdn.example.com/image.png',
          },
        ],
      },
      {
        id: 'u-gone',
        role: 'user',
        content: '',
        attachments: [{ attachment_id: 'att-2', type: 'image', file_name: 'gone.png', url: '' }],
      },
    ]);

    expect(messages[0].blocks?.[0]).toMatchObject({
      type: 'image',
      data: 'https://cdn.example.com/image.png',
      name: '截图.png',
    });
    expect(messages[1].blocks?.[0]).toMatchObject({ type: 'image', name: 'gone.png' });
    expect((messages[1].blocks?.[0] as unknown as { data: string }).data).toContain('data:image/svg+xml');
  });

  it('maps open-claw tool results into SDK tool_execution blocks', () => {
    const messages = mapPrivateHistoryMessages([
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '我先查询一下。',
        history_meta: { conversationRoundId: 'round-1' },
      },
      {
        id: 'tool-1',
        role: 'tool_result',
        metadata: {
          tool_name: 'search',
          tool_call_id: 'call-1',
          arguments: { query: 'TeamClaw' },
          result: { items: ['ok'] },
        },
        history_meta: { conversationRoundId: 'round-1' },
      },
    ]);

    expect(messages).toHaveLength(1);
    expect(messages[0].blocks).toEqual([
      { type: 'text', content: '我先查询一下。' },
      {
        type: 'tool_execution',
        steps: [
          expect.objectContaining({
            id: 'call-1',
            tool: 'search',
            status: 'success',
            input: '{\n  "query": "TeamClaw"\n}',
            output: '{\n  "items": [\n    "ok"\n  ]\n}',
          }),
        ],
      },
    ]);
  });

  it('forwards panel context to OpenClawProvider requests', async () => {
    const inner = {
      isConnected: true,
      connect: jest.fn(async () => undefined),
      disconnect: jest.fn(),
      request: jest.fn(async () => undefined),
      abort: jest.fn(),
      subscribeToConnectionStatus: jest.fn(() => () => undefined),
    } as unknown as OpenClawProvider;
    const provider = new TeamClawSupportProvider({
      getSession: jest.fn(async () => readySession),
      getMessages: jest.fn(async () => []),
      createOpenClawProvider: jest.fn(() => inner),
      getIamToken: jest.fn(async () => 'iam-token'),
      pollIntervalMs: 1,
      pollTimeoutMs: 100,
    });
    const panelContext = {
      panelState: {
        tabs: [{ id: 'teamclaw-support-guide', type: 'aix-card-panel', title: '客服处理建议' }],
        activeTabId: 'teamclaw-support-guide',
      },
      interactions: [
        {
          id: 'interaction-1',
          timestamp: 1,
          source: { type: 'panel', panel: 'teamclaw-support-guide' },
          action: { verb: 'select', subject: 'support_suggestion' },
          description: '用户选择了一条客服处理建议',
        },
      ],
    };

    await provider.request({
      content: '继续处理',
      targetId: TEAMCLAW_SUPPORT_BOT.targetId,
      panelContext,
    });

    expect(inner.request).toHaveBeenCalledWith(
      {
        query: '继续处理',
        sessionKey: readySession.session_key,
        panelContext,
      },
      undefined,
    );
  });
  it('shares one session initialization between history and websocket connect', async () => {
    const getSession = jest.fn(async (...args: [string, string]) => {
      expect(args).toHaveLength(2);
      return readySession;
    });
    const getMessages = jest.fn(
      async (
        ...args: [string, NonNullable<PrivateChatSession['connection']>, { limit?: number; offset?: number }?]
      ) => {
        expect(args).toHaveLength(3);
        return [{ id: 'u1', role: 'user', content: '历史问题' }];
      },
    );
    const inner = {
      isConnected: false,
      connect: jest.fn(async () => undefined),
      disconnect: jest.fn(),
      request: jest.fn(async () => undefined),
      abort: jest.fn(),
      subscribeToConnectionStatus: jest.fn(() => () => undefined),
    } as unknown as OpenClawProvider;
    const createOpenClawProvider = jest.fn((config: ConstructorParameters<typeof OpenClawProvider>[0]) => {
      void config;
      return inner;
    });
    const getIamToken = jest.fn(async () => 'iam-token');
    const provider = new TeamClawSupportProvider({
      getSession,
      getMessages,
      createOpenClawProvider,
      getIamToken,
      pollIntervalMs: 1,
      pollTimeoutMs: 100,
    });

    const [history] = await Promise.all([provider.loadHistory(), provider.connect()]);

    expect(getSession).toHaveBeenCalledTimes(1);
    expect(getSession).toHaveBeenCalledWith(TEAMCLAW_SUPPORT_BOT.botId, TEAMCLAW_SUPPORT_BOT.ownerId);
    expect(createOpenClawProvider).toHaveBeenCalledWith(expect.objectContaining({ xIAMToken: 'iam-token' }));
    expect(getIamToken).toHaveBeenCalledTimes(1);
    expect(getMessages).toHaveBeenCalledWith(readySession.session_key, readySession.connection!, {
      limit: 1000,
      offset: 0,
    });
    expect(history[0]).toMatchObject({ content: '历史问题', status: 'history' });
    expect(inner.connect).toHaveBeenCalledTimes(1);
  });
});
