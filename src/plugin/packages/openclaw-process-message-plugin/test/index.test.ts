import { strict as assert } from 'node:assert';
import plugin, {
  annotateAssistantMessageForPersistence,
  appendToolResultToAssistantAggregation,
  createAssistantAggregationState,
  createConversationRoundState,
  createPreviewHistoryMessageTool,
  createProcessMessageCommand,
  createProcessMessageTool,
  extractLatestConversationRound,
  formatHistoryShapeStatus,
  formatConversationRoundForPrompt,
  processMessage,
  previewHistoryMessage,
  resolveConversationRoundIdForMessage,
  resolveHistoryShapePluginConfig,
  resolveProcessMessagePluginConfig,
  rewritePersistedMessage,
  rewriteToolResultForPersistence,
} from '../src/index.js';

type HookHandler = (...args: unknown[]) => unknown;
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function assertUuid(value: unknown): asserts value is string {
  if (typeof value !== 'string') {
    throw new Error(`expected uuid string, got ${typeof value}`);
  }
  assert.match(value, UUID_RE);
}

describe('index.test.ts', () => {
  it('trims input by default', () => {
    assert(processMessage('  hello  ') === 'hello');
  });

  it('applies a prefix when provided', () => {
    assert(processMessage('world', { prefix: 'hello ' }) === 'hello world');
  });

  it('can preserve whitespace when trim is disabled', () => {
    assert(processMessage('  raw  ', { trim: false }) === '  raw  ');
  });

  it('can collapse whitespace and truncate output', () => {
    const config = resolveProcessMessagePluginConfig({
      defaultPrefix: '[P] ',
      collapseWhitespace: true,
      maxLength: 10,
    });

    assert(processMessage('  hello   world  ', {}, config) === '[P] hello ');
  });

  it('rewrites persisted messages with metadata', () => {
    const config = resolveHistoryShapePluginConfig({
      trimTextContent: true,
      redactTopLevelFields: [ 'debug' ],
    });

    const rewritten = rewritePersistedMessage(
      {
        role: 'user',
        content: '  hello history  ',
        debug: true,
      },
      config,
      {
        agentId: 'main',
        sessionKey: 'agent:main:main',
        conversationRoundId: '11111111-1111-4111-8111-111111111111',
        now: 123,
      },
    );

    assert(rewritten);
    assert.equal(rewritten.content, 'hello history');
    assert.equal(Object.hasOwn(rewritten, 'debug'), false);
    assert.deepEqual(rewritten.historyMeta, {
      plugin: 'openclaw-process-message-plugin',
      schemaVersion: 1,
      role: 'user',
      storedAt: 123,
      sessionKey: 'agent:main:main',
      agentId: 'main',
      conversationRoundId: '11111111-1111-4111-8111-111111111111',
    });
  });

  it('strips system blocks and sender metadata from user message content', () => {
    const config = resolveHistoryShapePluginConfig({});

    const rewritten = rewritePersistedMessage(
      {
        role: 'user',
        content:
          'System: [2026-03-29 15:23:06 GMT+8] Exec completed (crisp-wh, code 0) :: ...\n\n' +
          'Sender (untrusted metadata):\n```json\n{\n  "label": "openclaw-control-ui"\n}\n```\n\n' +
          '[Sun 2026-03-29 15:38 GMT+8] 你好呀',
      },
      config,
      {
        agentId: 'main',
        sessionKey: 'agent:main:main',
        conversationRoundId: '11111111-1111-4111-8111-111111111111',
        now: 123,
      },
    );

    assert(rewritten);
    assert.equal(rewritten.content, '[Sun 2026-03-29 15:38 GMT+8] 你好呀');
  });

  it('strips injected round summaries from user message content', () => {
    const config = resolveHistoryShapePluginConfig({});

    const rewritten = rewritePersistedMessage(
      {
        role: 'user',
        content:
          'Latest completed conversation round:\n' +
          'User: Earlier question\n' +
          'Assistant reply: Earlier answer\n' +
          'Counts: messages=2, assistant=1, toolResults=0\n\n' +
          'System: [2026-03-29 15:23:06 GMT+8] Exec completed ...\n\n' +
          '[Sun 2026-03-29 15:38 GMT+8] 实际用户消息',
      },
      config,
      {
        agentId: 'main',
        sessionKey: 'agent:main:main',
        conversationRoundId: '11111111-1111-4111-8111-111111111111',
        now: 123,
      },
    );

    assert(rewritten);
    // extractTimestampedUserLine extracts the last [timestamp] line content
    assert.equal(rewritten.content, '[Sun 2026-03-29 15:38 GMT+8] 实际用户消息');
  });

  it('can block configured roles from persistence', () => {
    const config = resolveHistoryShapePluginConfig({
      blockRoles: [ 'assistant' ],
    });

    const rewritten = rewritePersistedMessage(
      {
        role: 'assistant',
        content: 'hidden',
      },
      config,
    );

    assert.equal(rewritten, null);
  });

  it('rewrites tool results and strips details', () => {
    const config = resolveHistoryShapePluginConfig({});

    const rewritten = rewriteToolResultForPersistence(
      {
        role: 'toolResult',
        content: [{ type: 'text', text: 'ok' }],
        details: {
          huge: true,
        },
      },
      config,
      {
        toolName: 'search_docs',
        toolCallId: 'call_1',
        isSynthetic: true,
      },
    );

    assert.equal(Object.hasOwn(rewritten, 'details'), false);
    assert.deepEqual(rewritten.historyMeta, {
      plugin: 'openclaw-process-message-plugin',
      schemaVersion: 1,
      toolResult: {
        toolName: 'search_docs',
        toolCallId: 'call_1',
        isSynthetic: true,
        detailsDropped: true,
      },
    });
  });

  it('aggregates assistant messages and links tool results by toolCallId', () => {
    const config = resolveHistoryShapePluginConfig({});
    const state = createAssistantAggregationState();

    const assistant = annotateAssistantMessageForPersistence(
      {
        role: 'assistant',
        content: [
          { type: 'toolCall', id: 'call_1', name: 'memory_search', arguments: {} },
          { type: 'text', text: 'Looking this up now.' },
        ],
      },
      config,
      state,
      {
        sessionKey: 'agent:demo:main',
      },
    );

    const assistantAggregation = (
      (assistant.historyMeta as Record<string, unknown>).assistantAggregation as Record<
      string,
      unknown
      >
    );
    assertUuid(assistantAggregation.assistantAggregationId);
    assert.equal(assistantAggregation.toolCallCount, 1);
    assert.equal(assistantAggregation.matchedBy, 'assistant');

    const linkedAggregation = appendToolResultToAssistantAggregation(state, config, {
      toolCallId: 'call_1',
      toolName: 'memory_search',
      message: {
        role: 'toolResult',
        content: [{ type: 'text', text: 'Found 2 matching notes.' }],
      },
    });

    assert(linkedAggregation);
    assert.equal(linkedAggregation.assistantAggregationId, assistantAggregation.assistantAggregationId);
    assert.equal(linkedAggregation.linkedToolResultCount, 1);
    assert.equal(linkedAggregation.matchedBy, 'toolCallId');

    const rewritten = rewriteToolResultForPersistence(
      {
        role: 'toolResult',
        content: [{ type: 'text', text: 'Found 2 matching notes.' }],
        details: { large: true },
      },
      config,
      {
        toolName: 'memory_search',
        toolCallId: 'call_1',
        assistantAggregation: linkedAggregation,
      },
    );

    assert.deepEqual((rewritten.historyMeta as Record<string, unknown>).toolResult, {
      toolName: 'memory_search',
      toolCallId: 'call_1',
      isSynthetic: false,
      assistantAggregation: {
        assistantAggregationId: assistantAggregation.assistantAggregationId,
        toolCallCount: 1,
        linkedToolResultCount: 1,
        toolCalls: [{ toolCallId: 'call_1', toolName: 'memory_search' }],
        assistantTextPreview: 'Looking this up now.',
        matchedBy: 'toolCallId',
      },
      detailsDropped: true,
    });
  });

  it('marks followup assistant messages as continuations of the last tool-calling assistant', () => {
    const config = resolveHistoryShapePluginConfig({});
    const state = createAssistantAggregationState();

    annotateAssistantMessageForPersistence(
      {
        role: 'assistant',
        content: [{ type: 'toolCall', id: 'call_1', name: 'memory_search', arguments: {} }],
      },
      config,
      state,
      {
        sessionKey: 'agent:demo:main',
      },
    );

    const followup = annotateAssistantMessageForPersistence(
      {
        role: 'assistant',
        content: [{ type: 'text', text: 'Here is the final summary.' }],
      },
      config,
      state,
      {
        sessionKey: 'agent:demo:main',
      },
    );

    const followupAssistantAggregation =
      (followup.historyMeta as Record<string, unknown>).assistantAggregation as Record<string, unknown>;
    assertUuid(followupAssistantAggregation.assistantAggregationId);
    assertUuid(followupAssistantAggregation.continuationOfAssistantAggregationId);
    assert.deepEqual(
      followupAssistantAggregation,
      {
        assistantAggregationId: followupAssistantAggregation.assistantAggregationId,
        toolCallCount: 0,
        linkedToolResultCount: 0,
        toolCalls: [],
        assistantTextPreview: 'Here is the final summary.',
        continuationOfAssistantAggregationId: followupAssistantAggregation.continuationOfAssistantAggregationId,
        matchedBy: 'assistant',
      },
    );

    const linkedAggregation = appendToolResultToAssistantAggregation(state, config, {
      toolCallId: 'call_1',
      toolName: 'memory_search',
      message: {
        role: 'toolResult',
        content: [{ type: 'text', text: 'Found 2 matching notes.' }],
      },
    });

    assert.equal(linkedAggregation?.followupAssistantTextPreview, 'Here is the final summary.');
  });

  it('extracts the latest completed conversation round for prompt injection', () => {
    const config = resolveHistoryShapePluginConfig({});
    const userRoundId = '550e8400-e29b-41d4-a716-446655440000';
    const assistantRoundId = '660e8400-e29b-41d4-a716-446655440000';

    const round = extractLatestConversationRound(
      [
        {
          role: 'user',
          content: 'First question',
          historyMeta: {
            conversationRoundId: userRoundId,
          },
        },
        {
          role: 'assistant',
          content: [{ type: 'text', text: 'First answer' }],
          historyMeta: {
            conversationRoundId: assistantRoundId,
          },
        },
        {
          role: 'user',
          content: 'Second question',
        },
      ],
      config,
      {
        excludeTrailingUser: true,
        requireResponse: true,
      },
    );

    assert(round);
    assert.deepEqual(round, {
      roundId: assistantRoundId,
      messageCount: 2,
      assistantMessageCount: 1,
      toolCallCount: 0,
      toolResultCount: 0,
      toolErrorCount: 0,
      userTextPreview: 'First question',
      assistantTextPreview: 'First answer',
      toolCalls: [],
      toolResults: [],
    });

    const promptText = formatConversationRoundForPrompt(round);
    assert.equal(promptText, 'First question');
  });

  it('keeps only the human part of a user message when system blocks are prepended', () => {
    const config = resolveHistoryShapePluginConfig({});
    const round = extractLatestConversationRound(
      [
        {
          role: 'user',
          content: [
            {
              type: 'text',
              text:
                'System: [2026-03-29 15:23:06 GMT+8] Exec completed (crisp-wh, code 0) :: ...\n\n' +
                'Sender (untrusted metadata):\n```json\n{\n  "label": "openclaw-control-ui"\n}\n```\n\n' +
                '[Sun 2026-03-29 15:38 GMT+8] 你好呀',
            },
          ],
        },
        {
          role: 'assistant',
          content: [{ type: 'text', text: '你好，有什么我可以帮你？' }],
        },
      ],
      config,
      {
        requireResponse: true,
      },
    );

    assert(round);
    assert.equal(round.userTextPreview, '你好呀');
  });

  it('drops injected round echoes before reading the actual user message', () => {
    const config = resolveHistoryShapePluginConfig({});
    const round = extractLatestConversationRound(
      [
        {
          role: 'user',
          content: [
            {
              type: 'text',
              text:
                'Latest completed conversation round:\n' +
                'User: Earlier question\n' +
                'Assistant reply: Earlier answer\n' +
                'Counts: messages=2, assistant=1, toolResults=0\n\n' +
                'System: [2026-03-29 15:23:06 GMT+8] Exec completed ...\n\n' +
                '[Sun 2026-03-29 15:38 GMT+8] 你好呀',
            },
          ],
        },
        {
          role: 'assistant',
          content: [{ type: 'text', text: '你好呀' }],
        },
      ],
      config,
      {
        requireResponse: true,
      },
    );

    assert(round);
    assert.equal(round.userTextPreview, '你好呀');
  });

  it('assigns one conversation round uuid across user, assistant, and toolResult messages', () => {
    const config = resolveHistoryShapePluginConfig({});
    const state = createConversationRoundState();

    const userRoundId = resolveConversationRoundIdForMessage(
      state,
      {
        role: 'user',
        content: 'Question',
      },
      config,
      {
        sessionKey: 'agent:demo:main',
      },
    );
    const assistantRoundId = resolveConversationRoundIdForMessage(
      state,
      {
        role: 'assistant',
        content: [{ type: 'text', text: 'Answer' }],
      },
      config,
      {
        sessionKey: 'agent:demo:main',
      },
    );
    const toolResultRoundId = resolveConversationRoundIdForMessage(
      state,
      {
        role: 'toolResult',
        content: [{ type: 'text', text: 'Tool output' }],
      },
      config,
      {
        sessionKey: 'agent:demo:main',
      },
    );

    assertUuid(userRoundId);
    assert.equal(assistantRoundId, userRoundId);
    assert.equal(toolResultRoundId, userRoundId);
  });

  it('formats history rewrite status for inspection', () => {
    const config = resolveHistoryShapePluginConfig({
      metaField: 'audit',
      rewriteRoles: [ 'user', 'toolResult' ],
      blockRoles: [ 'assistant' ],
      redactTopLevelFields: [ 'debug' ],
    });

    const status = formatHistoryShapeStatus(config);
    assert.match(status, /metaField: audit/);
    assert.match(status, /rewriteRoles: user, toolResult/);
    assert.match(status, /blockRoles: assistant/);
    assert.match(status, /aggregateToolResultsByAssistant: true/);
    assert.match(status, /aggregateConversationRounds: true/);
    assert.doesNotMatch(status, /persistConversationRounds/);
  });

  it('previews how a message will be rewritten before persistence', () => {
    const config = resolveHistoryShapePluginConfig({
      metaField: 'audit',
      trimTextContent: true,
    });

    const preview = previewHistoryMessage(
      {
        role: 'user',
        content: '  preview me  ',
      },
      config,
      {
        sessionKey: 'agent:preview:main',
        agentId: 'preview',
        conversationRoundId: '770e8400-e29b-41d4-a716-446655440000',
        now: 99,
      },
    );

    assert.equal(preview.blocked, false);
    assert.equal(preview.message?.content, 'preview me');
    assert.deepEqual(preview.message?.audit, {
      plugin: 'openclaw-process-message-plugin',
      schemaVersion: 1,
      role: 'user',
      storedAt: 99,
      sessionKey: 'agent:preview:main',
      agentId: 'preview',
      conversationRoundId: '770e8400-e29b-41d4-a716-446655440000',
    });
  });

  it('registers tool, command, and persistence hooks', async () => {
    const hooks = new Map<string, HookHandler>();
    const registered: { tools: unknown[]; command?: unknown } = { tools: [] };

    plugin.register({
      pluginConfig: {
        defaultPrefix: '[BOT] ',
        metaField: 'audit',
      },
      registerTool(tool: unknown) {
        registered.tools.push(tool);
      },
      registerCommand(command: unknown) {
        registered.command = command;
      },
      on(name: string, handler: HookHandler) {
        hooks.set(name, handler);
      },
      logger: {},
      config: {},
    } as unknown as Parameters<typeof plugin.register>[0]);

    assert.equal(registered.tools.length, 2);
    assert(registered.command);
    assert.equal(hooks.has('tool_result_persist'), true);
    assert.equal(hooks.has('before_message_write'), true);
    assert.equal(hooks.has('before_prompt_build'), true);

    const tool = createProcessMessageTool(
      resolveProcessMessagePluginConfig({ defaultPrefix: '[BOT] ' }),
    );
    const result = await tool.execute('tool-call', { message: 'hello' });
    assert.equal(result.content[0].type, 'text');
    if (result.content[0].type !== 'text') {
      throw new Error('expected text tool result');
    }
    assert.equal(result.content[0].text, '[BOT] hello');

    const command = createProcessMessageCommand(
      resolveProcessMessagePluginConfig({ defaultPrefix: '[BOT] ' }),
      resolveHistoryShapePluginConfig({ metaField: 'audit' }),
    );
    const reply = await command.handler({ args: 'hello' });
    assert.equal(reply.text, '[BOT] hello');
    const statusReply = await command.handler({ args: 'history-status' });
    assert.match(statusReply.text, /metaField: audit/);

    const previewTool = createPreviewHistoryMessageTool(
      resolveHistoryShapePluginConfig({ metaField: 'audit' }),
    );
    const previewResult = await previewTool.execute('tool-call', {
      role: 'toolResult',
      content: 'ok',
      sessionKey: 'agent:agent-a:main',
      agentId: 'agent-a',
      toolName: 'memory_search',
      toolCallId: 'call_preview',
      isSynthetic: true,
    });
    assert.equal(previewResult.content[0].type, 'text');
    if (previewResult.content[0].type !== 'text') {
      throw new Error('expected text tool result');
    }
    assert.match(previewResult.content[0].text, /"blocked": false/);
    assert.match(previewResult.content[0].text, /call_preview/);

    const toolHook = hooks.get('tool_result_persist');
    const beforeWriteHook = hooks.get('before_message_write');
    const beforePromptBuildHook = hooks.get('before_prompt_build');
    if (!toolHook || !beforeWriteHook || !beforePromptBuildHook) {
      throw new Error('expected hooks to be registered');
    }

    const userPersisted = beforeWriteHook(
      {
        message: {
          role: 'user',
          content: 'Search my notes',
        },
      },
      {
        agentId: 'agent-a',
        sessionKey: 'agent:agent-a:main',
      },
    ) as { message: Record<string, unknown> };

    const assistantPersisted = beforeWriteHook(
      {
        message: {
          role: 'assistant',
          content: [{ type: 'toolCall', id: 'call_2', name: 'memory_search', arguments: {} }],
        },
      },
      {
        agentId: 'agent-a',
        sessionKey: 'agent:agent-a:main',
      },
    ) as { message: Record<string, unknown> };

    const toolResult = toolHook(
      {
        message: {
          role: 'toolResult',
          content: [{ type: 'text', text: 'ok' }],
          details: { keep: false },
        },
        toolName: 'memory_search',
        toolCallId: 'call_2',
        isSynthetic: false,
      },
      {},
    ) as { message: Record<string, unknown> };

    const persistedToolResult = beforeWriteHook(
      {
        message: toolResult.message,
      },
      {
        agentId: 'agent-a',
        sessionKey: 'agent:agent-a:main',
      },
    ) as { message: Record<string, unknown> };

    const assistantAudit = assistantPersisted.message.audit as Record<string, unknown>;
    const assistantAggregation =
      assistantAudit.assistantAggregation as Record<string, unknown>;
    const toolResultAudit = persistedToolResult.message.audit as Record<string, unknown>;
    const toolResultAggregation =
      (toolResultAudit.toolResult as Record<string, unknown>).assistantAggregation as Record<
      string,
      unknown
      >;
    const userAudit = userPersisted.message.audit as Record<string, unknown>;

    assertUuid(assistantAudit.conversationRoundId);
    assert.equal(userAudit.conversationRoundId, assistantAudit.conversationRoundId);
    assert.equal(toolResultAudit.conversationRoundId, assistantAudit.conversationRoundId);
    assertUuid(assistantAggregation.assistantAggregationId);
    assert.equal(Object.hasOwn(assistantAggregation, 'conversationRoundId'), false);
    assert.equal(Object.hasOwn(toolResult.message, 'details'), false);
    assert.equal(
      (toolResultAudit.sessionKey as string),
      'agent:agent-a:main',
    );
    assert.equal(
      toolResultAggregation.assistantAggregationId,
      assistantAggregation.assistantAggregationId,
    );
    assert.equal(Object.hasOwn(toolResultAggregation, 'conversationRoundId'), false);

    const promptMutation = await beforePromptBuildHook(
      {
        prompt: 'What changed next?',
        messages: [
          {
            role: 'user',
            content: 'Earlier question',
          },
          {
            role: 'assistant',
            content: [{ type: 'text', text: 'Earlier answer' }],
          },
          {
            role: 'user',
            content: 'What changed next?',
          },
        ],
      },
      {
        sessionId: 'session-1',
        sessionKey: 'agent:agent-a:main',
      },
    ) as { prependContext?: string } | undefined;

    assert.equal(promptMutation?.prependContext, 'Earlier question');
  });
});
