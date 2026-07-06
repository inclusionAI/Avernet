import { randomUUID } from 'node:crypto';
import type { AnyAgentTool, OpenClawPluginApi } from 'openclaw/plugin-sdk/core';

export type ProcessMessagePluginConfig = {
  defaultPrefix?: string;
  trimByDefault?: boolean;
  collapseWhitespace?: boolean;
  maxLength?: number;
};

export type ResolvedProcessMessagePluginConfig = {
  defaultPrefix: string;
  trimByDefault: boolean;
  collapseWhitespace: boolean;
  maxLength?: number;
};

export interface ProcessMessageOptions {
  prefix?: string;
  trim?: boolean;
  collapseWhitespace?: boolean;
  maxLength?: number;
}

export type HistoryMessage = Record<string, unknown> & {
  role?: string;
  content?: unknown;
  details?: unknown;
  isError?: boolean;
};

export type HistoryShapePluginConfig = {
  metaField?: string;
  rewriteRoles?: string[];
  blockRoles?: string[];
  redactTopLevelFields?: string[];
  trimTextContent?: boolean;
  includeStoredAt?: boolean;
  includeSessionRef?: boolean;
  includeAgentId?: boolean;
  dropToolResultDetails?: boolean;
  annotateSyntheticToolResults?: boolean;
  aggregateToolResultsByAssistant?: boolean;
  includeAssistantAggregationOnAssistant?: boolean;
  includeAssistantAggregationOnToolResult?: boolean;
  aggregateAssistantTextLimit?: number;
  aggregateAssistantMaxItems?: number;
  aggregateConversationRounds?: boolean;
  injectLatestRoundIntoPrompt?: boolean;
};

export type ResolvedHistoryShapePluginConfig = {
  metaField: string;
  rewriteRoles: Set<string>;
  blockRoles: Set<string>;
  redactTopLevelFields: string[];
  trimTextContent: boolean;
  includeStoredAt: boolean;
  includeSessionRef: boolean;
  includeAgentId: boolean;
  dropToolResultDetails: boolean;
  annotateSyntheticToolResults: boolean;
  aggregateToolResultsByAssistant: boolean;
  includeAssistantAggregationOnAssistant: boolean;
  includeAssistantAggregationOnToolResult: boolean;
  aggregateAssistantTextLimit: number;
  aggregateAssistantMaxItems: number;
  aggregateConversationRounds: boolean;
  injectLatestRoundIntoPrompt: boolean;
};

export type HistoryRewriteContext = {
  agentId?: string;
  sessionKey?: string;
  conversationRoundId?: string;
  now?: number;
};

export type AssistantToolCall = {
  toolCallId: string;
  toolName?: string;
};

export type AggregatedToolResultItem = {
  toolCallId?: string;
  toolName?: string;
  textPreview?: string;
  isError: boolean;
};

export type AssistantAggregationSnapshot = {
  assistantAggregationId: string;
  toolCallCount: number;
  linkedToolResultCount: number;
  toolCalls: AssistantToolCall[];
  assistantTextPreview?: string;
  followupAssistantTextPreview?: string;
  continuationOfAssistantAggregationId?: string;
  matchedBy?: 'assistant' | 'toolCallId';
};

export type ConversationRoundSnapshot = {
  roundId: string;
  messageCount: number;
  assistantMessageCount: number;
  toolCallCount: number;
  toolResultCount: number;
  toolErrorCount: number;
  userTextPreview?: string;
  assistantTextPreview?: string;
  toolCalls: AssistantToolCall[];
  toolResults: AggregatedToolResultItem[];
};

export interface ConversationRoundExtractionOptions {
  excludeTrailingUser?: boolean;
  requireResponse?: boolean;
}

export type ToolResultRewriteContext = {
  toolName?: string;
  toolCallId?: string;
  isSynthetic?: boolean;
  assistantAggregation?: AssistantAggregationSnapshot;
};

type AssistantAggregationBucket = {
  assistantAggregationId: string;
  conversationRoundId?: string;
  toolCalls: AssistantToolCall[];
  toolResults: AggregatedToolResultItem[];
  assistantTextPreview?: string;
  followupAssistantTextPreview?: string;
};

type AssistantAggregationState = {
  assistantSequence: number;
  assistants: Map<string, AssistantAggregationBucket>;
  toolCallToAssistant: Map<string, string>;
  latestToolCallingAssistantBySession: Map<string, string>;
};

type ConversationRoundState = {
  currentRoundBySession: Map<string, string>;
};

const DEFAULT_COMMAND_NAME = 'process';
const PROCESS_MESSAGE_TOOL_NAME = 'process_message';
const PREVIEW_HISTORY_TOOL_NAME = 'preview_history_message';
const DEFAULT_META_FIELD = 'historyMeta';
const PROCESS_MESSAGE_PLUGIN_ID = 'openclaw-process-message-plugin';
const DEFAULT_AGGREGATE_ASSISTANT_TEXT_LIMIT = 160;
const DEFAULT_AGGREGATE_ASSISTANT_MAX_ITEMS = 8;
const MAX_TRACKED_ASSISTANTS = 256;
const MAX_TRACKED_TOOL_CALLS = 1024;
const MAX_TRACKED_SESSIONS = 128;
const DEFAULT_ROUND_PROMPT_HEADING = 'Latest completed conversation round:';
const TOOL_CALL_TYPES = new Set([ 'toolCall', 'toolUse', 'functionCall' ]);

export const processMessagePluginConfigSchema = {
  type: 'object',
  additionalProperties: false,
  properties: {
    defaultPrefix: {
      type: 'string',
      description: 'Optional prefix added to every processed message.',
    },
    trimByDefault: {
      type: 'boolean',
      description: 'Trim leading and trailing whitespace before formatting.',
      default: true,
    },
    collapseWhitespace: {
      type: 'boolean',
      description: 'Collapse repeated whitespace to a single space.',
      default: false,
    },
    maxLength: {
      type: 'number',
      description: 'Optional maximum output length.',
    },
    metaField: {
      type: 'string',
      description: 'Top-level field name used to store plugin-generated history metadata.',
    },
    rewriteRoles: {
      type: 'array',
      items: { type: 'string' },
      description: 'Message roles whose persisted structure should be rewritten.',
    },
    blockRoles: {
      type: 'array',
      items: { type: 'string' },
      description: 'Message roles that should be skipped entirely during transcript persistence.',
    },
    redactTopLevelFields: {
      type: 'array',
      items: { type: 'string' },
      description: 'Top-level message fields removed before persistence.',
    },
    trimTextContent: {
      type: 'boolean',
      description: 'Trim string and text-block content before writing the transcript.',
      default: false,
    },
    includeStoredAt: {
      type: 'boolean',
      description: 'Include a storedAt timestamp in the generated metadata field.',
      default: true,
    },
    includeSessionRef: {
      type: 'boolean',
      description: 'Include sessionKey in the generated metadata field when available.',
      default: true,
    },
    includeAgentId: {
      type: 'boolean',
      description: 'Include agentId in the generated metadata field when available.',
      default: true,
    },
    dropToolResultDetails: {
      type: 'boolean',
      description: 'Remove toolResult.details before the message is written to history.',
      default: true,
    },
    annotateSyntheticToolResults: {
      type: 'boolean',
      description: 'Record whether a persisted tool result was synthesized by the guard layer.',
      default: true,
    },
    aggregateToolResultsByAssistant: {
      type: 'boolean',
      description: 'Link persisted toolResult metadata back to assistant messages by toolCallId.',
      default: true,
    },
    includeAssistantAggregationOnAssistant: {
      type: 'boolean',
      description: 'Attach assistant aggregation metadata to persisted assistant messages.',
      default: true,
    },
    includeAssistantAggregationOnToolResult: {
      type: 'boolean',
      description: 'Attach linked assistant aggregation metadata to persisted toolResult messages.',
      default: true,
    },
    aggregateAssistantTextLimit: {
      type: 'number',
      description: 'Maximum characters stored for assistant and toolResult text previews.',
      default: DEFAULT_AGGREGATE_ASSISTANT_TEXT_LIMIT,
    },
    aggregateAssistantMaxItems: {
      type: 'number',
      description: 'Maximum tool calls or linked tool results retained in assistant aggregation metadata.',
      default: DEFAULT_AGGREGATE_ASSISTANT_MAX_ITEMS,
    },
    aggregateConversationRounds: {
      type: 'boolean',
      description: 'Build latest-round conversation summaries from user, assistant, and toolResult messages.',
      default: true,
    },
    injectLatestRoundIntoPrompt: {
      type: 'boolean',
      description: 'Inject the latest completed conversation-round summary into before_prompt_build.',
      default: true,
    },
  },
} as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .map(entry => (typeof entry === 'string' ? entry.trim() : ''))
    .filter(Boolean);
}

function readPluginConfigRecord(pluginConfig: unknown): Record<string, unknown> {
  return isRecord(pluginConfig) ? pluginConfig : {};
}

function clampPositiveInteger(
  value: unknown,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return fallback;
  }

  return Math.max(minimum, Math.min(maximum, Math.trunc(value)));
}

function collapseWhitespaceRuns(input: string): string {
  return input.replace(/\s+/g, ' ').trim();
}

function trimToLimit(value: string, limit: number): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return '';
  }

  return trimmed.length > limit ? `${trimmed.slice(0, limit)}...` : trimmed;
}

function cloneTextBlocks(content: unknown): unknown {
  if (typeof content === 'string') {
    return content.trim();
  }

  if (!Array.isArray(content)) {
    return content;
  }

  return content.map(block => {
    if (!isRecord(block) || typeof block.text !== 'string') {
      return block;
    }

    return {
      ...block,
      text: block.text.trim(),
    };
  });
}

function mergeMetaField(
  message: HistoryMessage,
  metaField: string,
  nextMeta: Record<string, unknown>,
): HistoryMessage {
  const existing = isRecord(message[metaField]) ? message[metaField] : {};

  return {
    ...message,
    [metaField]: {
      ...existing,
      ...nextMeta,
    },
  };
}

function collectTextPreview(content: unknown, limit: number): string | undefined {
  if (typeof content === 'string') {
    const preview = trimToLimit(content, limit);
    return preview || undefined;
  }

  if (!Array.isArray(content)) {
    return undefined;
  }

  const preview = trimToLimit(
    content
      .filter(isRecord)
      .map(block => (typeof block.text === 'string' ? block.text : ''))
      .filter(Boolean)
      .join('\n'),
    limit,
  );

  return preview || undefined;
}

function collectRawText(content: unknown): string {
  if (typeof content === 'string') {
    return content;
  }

  if (!Array.isArray(content)) {
    return '';
  }

  return content
    .filter(isRecord)
    .map(block => (typeof block.text === 'string' ? block.text : ''))
    .filter(Boolean)
    .join('\n');
}

function stripSenderMetadataBlocks(text: string): string {
  return text.replace(
    /\n{2,}Sender \(untrusted metadata\):\n```json[\s\S]*?```\s*/g,
    '\n\n',
  );
}

function stripInjectedRoundSummaries(text: string): string {
  let next = text.trim();

  while (next.startsWith(DEFAULT_ROUND_PROMPT_HEADING)) {
    const marker = next.indexOf('\n\n[');
    if (marker < 0) {
      return '';
    }
    next = next.slice(marker + 2).trim();
  }

  return next;
}

function extractTimestampedUserLine(text: string): string | undefined {
  const matches = [ ...text.matchAll(/(?:^|\n)\[([^\n\]]+)\]\s*([^\n]+)/g) ];
  const last = matches.at(-1);
  if (!last) {
    return undefined;
  }

  return last[2]?.trim() || undefined;
}

function extractUserFacingTextPreview(content: unknown, limit: number): string | undefined {
  const rawText = collectRawText(content).trim();
  if (!rawText) {
    return undefined;
  }

  const withoutInjectedRounds = stripInjectedRoundSummaries(rawText);
  const withoutSenderMetadata = stripSenderMetadataBlocks(withoutInjectedRounds).trim();
  const timestampedLine = extractTimestampedUserLine(withoutSenderMetadata);
  if (timestampedLine) {
    return trimToLimit(timestampedLine, limit) || undefined;
  }

  const paragraphs = withoutSenderMetadata
    .split(/\n{2,}/)
    .map(section => section.trim())
    .filter(Boolean)
    .filter(section => {
      if (section.startsWith(DEFAULT_ROUND_PROMPT_HEADING)) {
        return false;
      }
      if (section.startsWith('Sender (untrusted metadata):')) {
        return false;
      }
      if (section.startsWith('System:')) {
        return false;
      }
      return true;
    });

  const bestCandidate = paragraphs.at(-1) ?? withoutSenderMetadata;
  const preview = trimToLimit(bestCandidate, limit);
  return preview || undefined;
}

function cleanUserMessageContent(content: unknown): unknown {
  if (typeof content === 'string') {
    const withoutInjectedRounds = stripInjectedRoundSummaries(content);
    const withoutSenderMetadata = stripSenderMetadataBlocks(withoutInjectedRounds);

    const paragraphs = withoutSenderMetadata
      .split(/\n{2,}/)
      .map(section => section.trim())
      .filter(Boolean)
      .filter(section => {
        if (section.startsWith(DEFAULT_ROUND_PROMPT_HEADING)) {
          return false;
        }
        if (section.startsWith('Sender (untrusted metadata):')) {
          return false;
        }
        if (section.startsWith('System:')) {
          return false;
        }
        return true;
      });

    const bestCandidate = paragraphs.at(-1) ?? withoutSenderMetadata;
    return bestCandidate || content;
  }

  if (!Array.isArray(content)) {
    return content;
  }

  return content.map(block => {
    if (!isRecord(block) || typeof block.text !== 'string') {
      return block;
    }

    const withoutInjectedRounds = stripInjectedRoundSummaries(block.text);
    const withoutSenderMetadata = stripSenderMetadataBlocks(withoutInjectedRounds);

    const paragraphs = withoutSenderMetadata
      .split(/\n{2,}/)
      .map(section => section.trim())
      .filter(Boolean)
      .filter(section => {
        if (section.startsWith(DEFAULT_ROUND_PROMPT_HEADING)) {
          return false;
        }
        if (section.startsWith('Sender (untrusted metadata):')) {
          return false;
        }
        if (section.startsWith('System:')) {
          return false;
        }
        return true;
      });

    const bestCandidate = paragraphs.at(-1) ?? withoutSenderMetadata;
    return {
      ...block,
      text: bestCandidate || block.text,
    };
  });
}

export function extractAssistantToolCalls(message: HistoryMessage): AssistantToolCall[] {
  if (message.role !== 'assistant' || !Array.isArray(message.content)) {
    return [];
  }

  const toolCalls: AssistantToolCall[] = [];
  for (const block of message.content) {
    if (!isRecord(block)) {
      continue;
    }

    const blockType = typeof block.type === 'string' ? block.type : '';
    const toolCallId = typeof block.id === 'string' ? block.id.trim() : '';
    if (!toolCallId || !TOOL_CALL_TYPES.has(blockType)) {
      continue;
    }

    toolCalls.push({
      toolCallId,
      ...(typeof block.name === 'string' && block.name.trim()
        ? { toolName: block.name.trim() }
        : {}),
    });
  }

  return toolCalls;
}

function touchTrackedMap<TKey, TValue>(map: Map<TKey, TValue>, key: TKey, value: TValue, max: number) {
  if (map.has(key)) {
    map.delete(key);
  }
  map.set(key, value);

  while (map.size > max) {
    const oldest = map.keys().next().value;
    if (oldest === undefined) {
      break;
    }
    map.delete(oldest);
  }
}

function normalizeHistoryMessages(messages: unknown[]): HistoryMessage[] {
  return messages.filter(isRecord).map(message => message as HistoryMessage);
}

function roleOfMessage(message: HistoryMessage | undefined): string {
  return typeof message?.role === 'string' ? message.role : '';
}

function readToolResultMeta(
  message: HistoryMessage,
  config: ResolvedHistoryShapePluginConfig,
): Record<string, unknown> {
  const historyMeta = isRecord(message[config.metaField])
    ? (message[config.metaField] as Record<string, unknown>)
    : {};
  return isRecord(historyMeta.toolResult) ? historyMeta.toolResult : {};
}

function collectAggregatedToolResultItem(
  message: HistoryMessage,
  config: ResolvedHistoryShapePluginConfig,
): AggregatedToolResultItem {
  const toolResultMeta = readToolResultMeta(message, config);
  const toolCallId =
    typeof toolResultMeta.toolCallId === 'string'
      ? toolResultMeta.toolCallId.trim()
      : typeof message.toolCallId === 'string'
        ? message.toolCallId.trim()
        : '';
  const toolName =
    typeof toolResultMeta.toolName === 'string'
      ? toolResultMeta.toolName.trim()
      : typeof message.toolName === 'string'
        ? message.toolName.trim()
        : '';

  return {
    ...(toolCallId ? { toolCallId } : {}),
    ...(toolName ? { toolName } : {}),
    ...(collectTextPreview(message.content, config.aggregateAssistantTextLimit)
      ? { textPreview: collectTextPreview(message.content, config.aggregateAssistantTextLimit) }
      : {}),
    isError: message.isError === true,
  };
}

function normalizeSessionKey(sessionKey: string | undefined): string | undefined {
  const normalizedSessionKey = sessionKey?.trim();
  return normalizedSessionKey || undefined;
}

function resolveSessionTrackingKey(
  sessionKey: string | undefined,
  agentId: string | undefined,
): string | undefined {
  const normalizedSessionKey = normalizeSessionKey(sessionKey);
  if (normalizedSessionKey) {
    return normalizedSessionKey;
  }

  const normalizedAgentId = agentId?.trim();
  return normalizedAgentId ? `agent:${normalizedAgentId}` : undefined;
}

function readConversationRoundId(
  message: HistoryMessage,
  config: ResolvedHistoryShapePluginConfig,
): string | undefined {
  const historyMeta = isRecord(message[config.metaField])
    ? (message[config.metaField] as Record<string, unknown>)
    : {};
  const conversationRoundId = typeof historyMeta.conversationRoundId === 'string'
    ? historyMeta.conversationRoundId.trim()
    : '';

  return conversationRoundId || undefined;
}

function readConversationRoundIdForAggregation(
  message: HistoryMessage,
  config: ResolvedHistoryShapePluginConfig,
): string | undefined {
  const role = roleOfMessage(message);
  if (role !== 'assistant' && role !== 'toolResult') {
    return undefined;
  }

  return readConversationRoundId(message, config);
}

export function resolveProcessMessagePluginConfig(
  pluginConfig: unknown,
): ResolvedProcessMessagePluginConfig {
  const raw = readPluginConfigRecord(pluginConfig);

  const defaultPrefix =
    typeof raw.defaultPrefix === 'string' ? raw.defaultPrefix : '';
  const trimByDefault =
    typeof raw.trimByDefault === 'boolean' ? raw.trimByDefault : true;
  const collapseWhitespace =
    typeof raw.collapseWhitespace === 'boolean' ? raw.collapseWhitespace : false;
  const maxLength =
    typeof raw.maxLength === 'number' &&
    Number.isFinite(raw.maxLength) &&
    raw.maxLength > 0
      ? Math.trunc(raw.maxLength)
      : undefined;

  return {
    defaultPrefix,
    trimByDefault,
    collapseWhitespace,
    maxLength,
  };
}

export function resolveHistoryShapePluginConfig(
  pluginConfig: unknown,
): ResolvedHistoryShapePluginConfig {
  const raw = readPluginConfigRecord(pluginConfig);
  const rewriteRoles = normalizeStringList(raw.rewriteRoles);
  const blockRoles = normalizeStringList(raw.blockRoles);

  return {
    metaField:
      typeof raw.metaField === 'string' && raw.metaField.trim()
        ? raw.metaField.trim()
        : DEFAULT_META_FIELD,
    rewriteRoles: new Set(rewriteRoles.length > 0 ? rewriteRoles : [ 'user', 'assistant', 'toolResult' ]),
    blockRoles: new Set(blockRoles),
    redactTopLevelFields: normalizeStringList(raw.redactTopLevelFields),
    trimTextContent: typeof raw.trimTextContent === 'boolean' ? raw.trimTextContent : false,
    includeStoredAt: typeof raw.includeStoredAt === 'boolean' ? raw.includeStoredAt : true,
    includeSessionRef: typeof raw.includeSessionRef === 'boolean' ? raw.includeSessionRef : true,
    includeAgentId: typeof raw.includeAgentId === 'boolean' ? raw.includeAgentId : true,
    dropToolResultDetails:
      typeof raw.dropToolResultDetails === 'boolean' ? raw.dropToolResultDetails : true,
    annotateSyntheticToolResults:
      typeof raw.annotateSyntheticToolResults === 'boolean'
        ? raw.annotateSyntheticToolResults
        : true,
    aggregateToolResultsByAssistant:
      typeof raw.aggregateToolResultsByAssistant === 'boolean'
        ? raw.aggregateToolResultsByAssistant
        : true,
    includeAssistantAggregationOnAssistant:
      typeof raw.includeAssistantAggregationOnAssistant === 'boolean'
        ? raw.includeAssistantAggregationOnAssistant
        : true,
    includeAssistantAggregationOnToolResult:
      typeof raw.includeAssistantAggregationOnToolResult === 'boolean'
        ? raw.includeAssistantAggregationOnToolResult
        : true,
    aggregateAssistantTextLimit: clampPositiveInteger(
      raw.aggregateAssistantTextLimit,
      DEFAULT_AGGREGATE_ASSISTANT_TEXT_LIMIT,
      32,
      4000,
    ),
    aggregateAssistantMaxItems: clampPositiveInteger(
      raw.aggregateAssistantMaxItems,
      DEFAULT_AGGREGATE_ASSISTANT_MAX_ITEMS,
      1,
      50,
    ),
    aggregateConversationRounds:
      typeof raw.aggregateConversationRounds === 'boolean'
        ? raw.aggregateConversationRounds
        : true,
    injectLatestRoundIntoPrompt:
      typeof raw.injectLatestRoundIntoPrompt === 'boolean'
        ? raw.injectLatestRoundIntoPrompt
        : true,
  };
}

export function processMessage(
  message: string,
  options: ProcessMessageOptions = {},
  defaults: ResolvedProcessMessagePluginConfig = resolveProcessMessagePluginConfig({}),
): string {
  const shouldTrim = options.trim ?? defaults.trimByDefault;
  const shouldCollapse = options.collapseWhitespace ?? defaults.collapseWhitespace;

  let normalized = shouldTrim ? message.trim() : message;

  if (shouldCollapse) {
    normalized = collapseWhitespaceRuns(normalized);
  }

  const prefix = options.prefix ?? defaults.defaultPrefix;
  const maxLength = options.maxLength ?? defaults.maxLength;
  const prefixed = prefix ? `${prefix}${normalized}` : normalized;

  if (typeof maxLength === 'number' && maxLength > 0 && prefixed.length > maxLength) {
    return prefixed.slice(0, maxLength);
  }

  return prefixed;
}

function formatProcessMessageUsage(config: ResolvedProcessMessagePluginConfig): string {
  const defaults = [
    `defaultPrefix: ${config.defaultPrefix || '(none)'}`,
    `trimByDefault: ${String(config.trimByDefault)}`,
    `collapseWhitespace: ${String(config.collapseWhitespace)}`,
    `maxLength: ${config.maxLength ?? '(none)'}`,
  ].join('\n');

  return [
    'Usage: /process <message>',
    '',
    'The command applies the plugin defaults and returns the processed message.',
    '',
    defaults,
  ].join('\n');
}

export function formatHistoryShapeStatus(
  config: ResolvedHistoryShapePluginConfig,
): string {
  const listOrNone = (values: string[]) => (values.length > 0 ? values.join(', ') : '(none)');

  return [
    'History rewrite status:',
    `- metaField: ${config.metaField}`,
    `- rewriteRoles: ${listOrNone([ ...config.rewriteRoles ])}`,
    `- blockRoles: ${listOrNone([ ...config.blockRoles ])}`,
    `- redactTopLevelFields: ${listOrNone(config.redactTopLevelFields)}`,
    `- trimTextContent: ${String(config.trimTextContent)}`,
    `- includeStoredAt: ${String(config.includeStoredAt)}`,
    `- includeSessionRef: ${String(config.includeSessionRef)}`,
    `- includeAgentId: ${String(config.includeAgentId)}`,
    `- dropToolResultDetails: ${String(config.dropToolResultDetails)}`,
    `- annotateSyntheticToolResults: ${String(config.annotateSyntheticToolResults)}`,
    `- aggregateToolResultsByAssistant: ${String(config.aggregateToolResultsByAssistant)}`,
    `- includeAssistantAggregationOnAssistant: ${String(
      config.includeAssistantAggregationOnAssistant,
    )}`,
    `- includeAssistantAggregationOnToolResult: ${String(
      config.includeAssistantAggregationOnToolResult,
    )}`,
    `- aggregateAssistantTextLimit: ${config.aggregateAssistantTextLimit}`,
    `- aggregateAssistantMaxItems: ${config.aggregateAssistantMaxItems}`,
    `- aggregateConversationRounds: ${String(config.aggregateConversationRounds)}`,
    `- injectLatestRoundIntoPrompt: ${String(config.injectLatestRoundIntoPrompt)}`,
  ].join('\n');
}

export function previewHistoryMessage(
  message: HistoryMessage,
  historyConfig: ResolvedHistoryShapePluginConfig,
  context: HistoryRewriteContext = {},
  toolContext: ToolResultRewriteContext = {},
): { blocked: boolean; message: HistoryMessage | null } {
  const toolPrepared = rewriteToolResultForPersistence(message, historyConfig, toolContext);
  const rewritten = rewritePersistedMessage(toolPrepared, historyConfig, context);

  return {
    blocked: rewritten === null,
    message: rewritten,
  };
}

export function createAssistantAggregationState(): AssistantAggregationState {
  return {
    assistantSequence: 0,
    assistants: new Map<string, AssistantAggregationBucket>(),
    toolCallToAssistant: new Map<string, string>(),
    latestToolCallingAssistantBySession: new Map<string, string>(),
  };
}

function nextAssistantAggregationId(state: AssistantAggregationState): string {
  state.assistantSequence += 1;
  return randomUUID();
}

export function createConversationRoundState(): ConversationRoundState {
  return {
    currentRoundBySession: new Map<string, string>(),
  };
}

export function resolveConversationRoundIdForMessage(
  state: ConversationRoundState,
  message: HistoryMessage,
  config: ResolvedHistoryShapePluginConfig,
  context: HistoryRewriteContext = {},
): string | undefined {
  const existingRoundId = readConversationRoundId(message, config);
  if (existingRoundId) {
    const trackingKey = resolveSessionTrackingKey(context.sessionKey, context.agentId);
    if (trackingKey) {
      touchTrackedMap(state.currentRoundBySession, trackingKey, existingRoundId, MAX_TRACKED_SESSIONS);
    }
    return existingRoundId;
  }

  const role = roleOfMessage(message);
  const trackingKey = resolveSessionTrackingKey(context.sessionKey, context.agentId);
  if (role === 'user') {
    const conversationRoundId = randomUUID();
    if (trackingKey) {
      touchTrackedMap(
        state.currentRoundBySession,
        trackingKey,
        conversationRoundId,
        MAX_TRACKED_SESSIONS,
      );
    }
    return conversationRoundId;
  }

  if (trackingKey) {
    const currentRoundId = state.currentRoundBySession.get(trackingKey);
    if (currentRoundId) {
      return currentRoundId;
    }
  }

  if (role === 'assistant' || role === 'toolResult') {
    const conversationRoundId = randomUUID();
    if (trackingKey) {
      touchTrackedMap(
        state.currentRoundBySession,
        trackingKey,
        conversationRoundId,
        MAX_TRACKED_SESSIONS,
      );
    }
    return conversationRoundId;
  }

  return undefined;
}

function buildAssistantAggregationSnapshot(
  bucket: AssistantAggregationBucket,
  config: ResolvedHistoryShapePluginConfig,
  extras: Partial<AssistantAggregationSnapshot> = {},
): AssistantAggregationSnapshot {
  return {
    assistantAggregationId: bucket.assistantAggregationId,
    toolCallCount: bucket.toolCalls.length,
    linkedToolResultCount: bucket.toolResults.length,
    toolCalls: bucket.toolCalls.slice(0, config.aggregateAssistantMaxItems),
    ...(bucket.assistantTextPreview ? { assistantTextPreview: bucket.assistantTextPreview } : {}),
    ...(bucket.followupAssistantTextPreview
      ? { followupAssistantTextPreview: bucket.followupAssistantTextPreview }
      : {}),
    ...extras,
  };
}

function registerAssistantBucket(
  state: AssistantAggregationState,
  bucket: AssistantAggregationBucket,
): void {
  touchTrackedMap(
    state.assistants,
    bucket.assistantAggregationId,
    bucket,
    MAX_TRACKED_ASSISTANTS,
  );
}

function registerLatestToolCallingAssistant(
  state: AssistantAggregationState,
  sessionKey: string | undefined,
  assistantAggregationId: string,
): void {
  const normalizedSessionKey = normalizeSessionKey(sessionKey);
  if (!normalizedSessionKey) {
    return;
  }

  touchTrackedMap(
    state.latestToolCallingAssistantBySession,
    normalizedSessionKey,
    assistantAggregationId,
    MAX_TRACKED_SESSIONS,
  );
}

export function annotateAssistantMessageForPersistence(
  message: HistoryMessage,
  config: ResolvedHistoryShapePluginConfig,
  state: AssistantAggregationState,
  context: HistoryRewriteContext = {},
): HistoryMessage {
  if (message.role !== 'assistant') {
    return message;
  }

  const toolCalls = extractAssistantToolCalls(message).slice(0, config.aggregateAssistantMaxItems);
  const assistantTextPreview = collectTextPreview(
    message.content,
    config.aggregateAssistantTextLimit,
  );
  const continuationOfAssistantAggregationId =
    toolCalls.length === 0 && context.sessionKey
      ? state.latestToolCallingAssistantBySession.get(context.sessionKey)
      : undefined;

  if (!config.includeAssistantAggregationOnAssistant && toolCalls.length === 0) {
    if (continuationOfAssistantAggregationId && assistantTextPreview) {
      const linkedBucket = state.assistants.get(continuationOfAssistantAggregationId);
      if (linkedBucket) {
        linkedBucket.followupAssistantTextPreview = assistantTextPreview;
        registerAssistantBucket(state, linkedBucket);
      }
    }
    return message;
  }

  const assistantAggregationId = nextAssistantAggregationId(state);
  const bucket: AssistantAggregationBucket = {
    assistantAggregationId,
    ...(context.conversationRoundId ? { conversationRoundId: context.conversationRoundId } : {}),
    toolCalls,
    toolResults: [],
    ...(assistantTextPreview ? { assistantTextPreview } : {}),
  };

  if (continuationOfAssistantAggregationId && assistantTextPreview) {
    const linkedBucket = state.assistants.get(continuationOfAssistantAggregationId);
    if (linkedBucket) {
      linkedBucket.followupAssistantTextPreview = assistantTextPreview;
      registerAssistantBucket(state, linkedBucket);
    }
  }

  registerAssistantBucket(state, bucket);

  for (const toolCall of toolCalls) {
    touchTrackedMap(
      state.toolCallToAssistant,
      toolCall.toolCallId,
      assistantAggregationId,
      MAX_TRACKED_TOOL_CALLS,
    );
  }

  if (toolCalls.length > 0) {
    registerLatestToolCallingAssistant(
      state,
      context.sessionKey,
      assistantAggregationId,
    );
  }

  if (!config.includeAssistantAggregationOnAssistant) {
    return message;
  }

  return mergeMetaField(message, config.metaField, {
    assistantAggregation: buildAssistantAggregationSnapshot(bucket, config, {
      matchedBy: 'assistant',
      ...(continuationOfAssistantAggregationId
        ? { continuationOfAssistantAggregationId }
        : {}),
    }),
  });
}

function extractToolResultTextPreview(
  message: HistoryMessage,
  limit: number,
): string | undefined {
  return collectTextPreview(message.content, limit);
}

export function appendToolResultToAssistantAggregation(
  state: AssistantAggregationState,
  config: ResolvedHistoryShapePluginConfig,
  params: {
    toolCallId?: string;
    toolName?: string;
    message: HistoryMessage;
  },
): AssistantAggregationSnapshot | undefined {
  const toolCallId = params.toolCallId?.trim();
  if (!toolCallId) {
    return undefined;
  }

  const assistantAggregationId = state.toolCallToAssistant.get(toolCallId);
  if (!assistantAggregationId) {
    return undefined;
  }

  const bucket = state.assistants.get(assistantAggregationId);
  if (!bucket) {
    return undefined;
  }

  const nextItem: AggregatedToolResultItem = {
    ...(toolCallId ? { toolCallId } : {}),
    ...(params.toolName?.trim() ? { toolName: params.toolName.trim() } : {}),
    ...(extractToolResultTextPreview(params.message, config.aggregateAssistantTextLimit)
      ? {
        textPreview: extractToolResultTextPreview(
          params.message,
          config.aggregateAssistantTextLimit,
        ),
      }
      : {}),
    isError: params.message.isError === true,
  };

  bucket.toolResults = [ ...bucket.toolResults, nextItem ].slice(
    -config.aggregateAssistantMaxItems,
  );
  registerAssistantBucket(state, bucket);

  return buildAssistantAggregationSnapshot(bucket, config, {
    matchedBy: 'toolCallId',
  });
}

export function extractLatestConversationRound(
  messages: unknown[],
  config: ResolvedHistoryShapePluginConfig,
  options: ConversationRoundExtractionOptions = {},
): ConversationRoundSnapshot | undefined {
  const normalizedMessages = normalizeHistoryMessages(messages);
  if (normalizedMessages.length === 0) {
    return undefined;
  }

  let endIndex = normalizedMessages.length - 1;
  while (endIndex >= 0 && !roleOfMessage(normalizedMessages[endIndex])) {
    endIndex -= 1;
  }

  if (
    options.excludeTrailingUser === true &&
    endIndex >= 0 &&
    roleOfMessage(normalizedMessages[endIndex]) === 'user'
  ) {
    endIndex -= 1;
  }

  if (endIndex < 0) {
    return undefined;
  }

  let startIndex = -1;
  for (let index = endIndex; index >= 0; index -= 1) {
    if (roleOfMessage(normalizedMessages[index]) === 'user') {
      startIndex = index;
      break;
    }
  }

  if (startIndex < 0) {
    return undefined;
  }

  const roundMessages = normalizedMessages.slice(startIndex, endIndex + 1);
  const assistantMessages = roundMessages.filter(message => roleOfMessage(message) === 'assistant');
  const toolResultMessages = roundMessages.filter(message => roleOfMessage(message) === 'toolResult');
  const hasResponse = assistantMessages.length > 0 || toolResultMessages.length > 0;
  if (options.requireResponse === true && !hasResponse) {
    return undefined;
  }

  const toolCallsById = new Map<string, AssistantToolCall>();
  let assistantTextPreview: string | undefined;
  for (const assistantMessage of assistantMessages) {
    for (const toolCall of extractAssistantToolCalls(assistantMessage)) {
      if (!toolCallsById.has(toolCall.toolCallId)) {
        toolCallsById.set(toolCall.toolCallId, toolCall);
      }
      if (toolCallsById.size >= config.aggregateAssistantMaxItems) {
        break;
      }
    }

    const preview = collectTextPreview(
      assistantMessage.content,
      config.aggregateAssistantTextLimit,
    );
    if (preview) {
      assistantTextPreview = preview;
    }
  }

  const aggregatedToolResults = toolResultMessages
    .map(message => collectAggregatedToolResultItem(message, config))
    .slice(-config.aggregateAssistantMaxItems);
  const conversationRoundId = roundMessages
    .map(message => readConversationRoundIdForAggregation(message, config))
    .find(Boolean);

  return {
    roundId: conversationRoundId ?? randomUUID(),
    messageCount: roundMessages.length,
    assistantMessageCount: assistantMessages.length,
    toolCallCount: toolCallsById.size,
    toolResultCount: aggregatedToolResults.length,
    toolErrorCount: aggregatedToolResults.filter(message => message.isError).length,
    ...(extractUserFacingTextPreview(
      roundMessages[0]?.content,
      config.aggregateAssistantTextLimit,
    )
      ? {
        userTextPreview: extractUserFacingTextPreview(
          roundMessages[0]?.content,
          config.aggregateAssistantTextLimit,
        ),
      }
      : {}),
    ...(assistantTextPreview ? { assistantTextPreview } : {}),
    toolCalls: [ ...toolCallsById.values() ].slice(0, config.aggregateAssistantMaxItems),
    toolResults: aggregatedToolResults,
  };
}

export function formatConversationRoundForPrompt(
  round: ConversationRoundSnapshot,
): string {
  if (round.userTextPreview) {
    return round.userTextPreview;
  }

  if (round.assistantTextPreview) {
    return round.assistantTextPreview;
  }

  const toolResultPreview = round.toolResults
    .map(toolResult => toolResult.textPreview)
    .find(Boolean);
  if (toolResultPreview) {
    return toolResultPreview;
  }

  const toolCallPreview = round.toolCalls
    .map(toolCall => toolCall.toolName ?? toolCall.toolCallId)
    .find(Boolean);
  return toolCallPreview ?? '';
}

export function rewriteToolResultForPersistence(
  message: HistoryMessage,
  config: ResolvedHistoryShapePluginConfig,
  context: ToolResultRewriteContext = {},
): HistoryMessage {
  if (message.role !== 'toolResult') {
    return message;
  }

  let next: HistoryMessage = { ...message };
  const detailsDropped = config.dropToolResultDetails && Object.hasOwn(next, 'details');

  if (detailsDropped) {
    delete next.details;
  }

  const currentMeta: Record<string, unknown> = isRecord(next[config.metaField])
    ? (next[config.metaField] as Record<string, unknown>)
    : {};
  const currentToolMeta: Record<string, unknown> = isRecord(currentMeta.toolResult)
    ? currentMeta.toolResult
    : {};

  next = mergeMetaField(next, config.metaField, {
    ...currentMeta,
    plugin: PROCESS_MESSAGE_PLUGIN_ID,
    schemaVersion: 1,
    toolResult: {
      ...currentToolMeta,
      ...(context.toolName ? { toolName: context.toolName } : {}),
      ...(context.toolCallId ? { toolCallId: context.toolCallId } : {}),
      ...(config.annotateSyntheticToolResults
        ? { isSynthetic: context.isSynthetic === true }
        : {}),
      ...(config.includeAssistantAggregationOnToolResult && context.assistantAggregation
        ? { assistantAggregation: context.assistantAggregation }
        : {}),
      detailsDropped,
    },
  });

  return next;
}

export function rewritePersistedMessage(
  message: HistoryMessage,
  config: ResolvedHistoryShapePluginConfig,
  context: HistoryRewriteContext = {},
): HistoryMessage | null {
  const role = typeof message.role === 'string' ? message.role : '';

  if (role && config.blockRoles.has(role)) {
    return null;
  }

  if (role && !config.rewriteRoles.has(role)) {
    return message;
  }

  const next: HistoryMessage = { ...message };

  if (role === 'user') {
    next.content = cleanUserMessageContent(next.content);
  } else if (config.trimTextContent) {
    next.content = cloneTextBlocks(next.content);
  }

  for (const field of config.redactTopLevelFields) {
    delete next[field];
  }

  const meta: Record<string, unknown> = {
    plugin: PROCESS_MESSAGE_PLUGIN_ID,
    schemaVersion: 1,
  };

  if (role) {
    meta.role = role;
  }
  if (config.includeStoredAt) {
    meta.storedAt = context.now ?? Date.now();
  }
  if (config.includeSessionRef && context.sessionKey) {
    meta.sessionKey = context.sessionKey;
  }
  if (config.includeAgentId && context.agentId) {
    meta.agentId = context.agentId;
  }
  if (context.conversationRoundId) {
    meta.conversationRoundId = context.conversationRoundId;
  }

  return mergeMetaField(next, config.metaField, meta);
}

export function createProcessMessageTool(
  defaults: ResolvedProcessMessagePluginConfig,
): AnyAgentTool {
  return {
    name: PROCESS_MESSAGE_TOOL_NAME,
    label: 'Process Message',
    description:
      'Normalize message text by trimming, collapsing whitespace, adding a prefix, and optionally truncating it.',
    parameters: {
      type: 'object',
      properties: {
        message: {
          type: 'string',
          description: 'The message text to normalize.',
        },
        prefix: {
          type: 'string',
          description: 'Optional prefix that overrides the plugin default prefix.',
        },
        trim: {
          type: 'boolean',
          description: 'Whether to trim leading and trailing whitespace before formatting.',
        },
        collapseWhitespace: {
          type: 'boolean',
          description: 'Whether to collapse repeated whitespace to single spaces.',
        },
        maxLength: {
          type: 'number',
          description: 'Optional maximum output length.',
        },
      },
      required: [ 'message' ],
    },
    async execute(_toolCallId, params: Record<string, unknown>) {
      const message = typeof params.message === 'string' ? params.message : '';
      if (!message) {
        throw new Error('message required');
      }

      const output = processMessage(
        message,
        {
          prefix: typeof params.prefix === 'string' ? params.prefix : undefined,
          trim: typeof params.trim === 'boolean' ? params.trim : undefined,
          collapseWhitespace:
            typeof params.collapseWhitespace === 'boolean'
              ? params.collapseWhitespace
              : undefined,
          maxLength:
            typeof params.maxLength === 'number' && Number.isFinite(params.maxLength)
              ? Math.trunc(params.maxLength)
              : undefined,
        },
        defaults,
      );

      return {
        content: [{ type: 'text' as const, text: output }],
        details: { output },
      };
    },
  };
}

export function createProcessMessageCommand(
  defaults: ResolvedProcessMessagePluginConfig,
  historyConfig: ResolvedHistoryShapePluginConfig,
) {
  return {
    name: DEFAULT_COMMAND_NAME,
    description: 'Normalize a message or inspect persisted-history rewrite behavior.',
    acceptsArgs: true,
    handler: async (ctx: { args?: string }) => {
      const input = ctx.args?.trim() ?? '';
      if (!input) {
        return { text: formatProcessMessageUsage(defaults) };
      }
      if (input === 'history-status') {
        return { text: formatHistoryShapeStatus(historyConfig) };
      }
      return { text: processMessage(input, {}, defaults) };
    },
  };
}

export function createPreviewHistoryMessageTool(
  historyConfig: ResolvedHistoryShapePluginConfig,
): AnyAgentTool {
  return {
    name: PREVIEW_HISTORY_TOOL_NAME,
    label: 'Preview History Message',
    description:
      'Preview how this plugin rewrites a message before it is persisted to transcript history.',
    parameters: {
      type: 'object',
      properties: {
        role: {
          type: 'string',
          description: 'Message role, for example user, assistant, or toolResult.',
        },
        content: {
          type: 'string',
          description: 'Message text content used for preview.',
        },
        sessionKey: {
          type: 'string',
          description: 'Optional session key included in preview metadata.',
        },
        agentId: {
          type: 'string',
          description: 'Optional agent id included in preview metadata.',
        },
        toolName: {
          type: 'string',
          description: 'Optional tool name used when previewing a toolResult message.',
        },
        toolCallId: {
          type: 'string',
          description: 'Optional tool call id used when previewing a toolResult message.',
        },
        isSynthetic: {
          type: 'boolean',
          description: 'Whether the tool result should be treated as synthetic.',
        },
        includeDetails: {
          type: 'boolean',
          description: 'Whether to attach a sample details payload to toolResult previews.',
        },
      },
      required: [ 'role', 'content' ],
    },
    async execute(_toolCallId, params: Record<string, unknown>) {
      const role = typeof params.role === 'string' ? params.role.trim() : '';
      const content = typeof params.content === 'string' ? params.content : '';

      if (!role) {
        throw new Error('role required');
      }
      if (!content) {
        throw new Error('content required');
      }

      const message: HistoryMessage = {
        role,
        content,
      };

      if (role === 'toolResult' && params.includeDetails !== false) {
        message.details = {
          preview: true,
          note: 'sample tool details',
        };
      }

      const preview = previewHistoryMessage(
        message,
        historyConfig,
        {
          sessionKey: typeof params.sessionKey === 'string' ? params.sessionKey : undefined,
          agentId: typeof params.agentId === 'string' ? params.agentId : undefined,
        },
        {
          toolName: typeof params.toolName === 'string' ? params.toolName : undefined,
          toolCallId: typeof params.toolCallId === 'string' ? params.toolCallId : undefined,
          isSynthetic: typeof params.isSynthetic === 'boolean' ? params.isSynthetic : undefined,
        },
      );

      const output = JSON.stringify(preview, null, 2);
      return {
        content: [{ type: 'text' as const, text: output }],
        details: preview,
      };
    },
  };
}

export const processMessagePlugin = {
  id: PROCESS_MESSAGE_PLUGIN_ID,
  name: 'OpenClaw Process Message Plugin',
  description:
    'Message processing helpers exposed as an OpenClaw tool, command, and history persistence hook set.',
  configSchema: processMessagePluginConfigSchema,
  register(api: OpenClawPluginApi) {
    const processConfig = resolveProcessMessagePluginConfig(api.pluginConfig);
    const historyConfig = resolveHistoryShapePluginConfig(api.pluginConfig);
    const assistantAggregationState = createAssistantAggregationState();
    const conversationRoundState = createConversationRoundState();

    api.registerTool(createProcessMessageTool(processConfig), {
      name: PROCESS_MESSAGE_TOOL_NAME,
      optional: true,
    });
    api.registerTool(createPreviewHistoryMessageTool(historyConfig), {
      name: PREVIEW_HISTORY_TOOL_NAME,
      optional: true,
    });
    api.registerCommand(createProcessMessageCommand(processConfig, historyConfig));

    api.on('before_prompt_build', event => {
      if (!historyConfig.aggregateConversationRounds || !historyConfig.injectLatestRoundIntoPrompt) {
        return;
      }

      const latestRound = extractLatestConversationRound(event.messages, historyConfig, {
        excludeTrailingUser: true,
        requireResponse: true,
      });
      if (!latestRound) {
        return;
      }

      return {
        prependContext: formatConversationRoundForPrompt(latestRound),
      };
    });

    api.on('tool_result_persist', ({ message, toolCallId, toolName, isSynthetic }) => {
      const assistantAggregation = historyConfig.aggregateToolResultsByAssistant
        ? appendToolResultToAssistantAggregation(assistantAggregationState, historyConfig, {
          toolCallId,
          toolName,
          message: message as unknown as HistoryMessage,
        })
        : undefined;

      return {
        message: rewriteToolResultForPersistence(
          message as unknown as HistoryMessage,
          historyConfig,
          {
            toolCallId,
            toolName,
            isSynthetic,
            assistantAggregation,
          },
        ) as unknown as typeof message,
      };
    });

    api.on('before_message_write', ({ message }, ctx) => {
      const historyMessage = message as unknown as HistoryMessage;
      const conversationRoundId = resolveConversationRoundIdForMessage(
        conversationRoundState,
        historyMessage,
        historyConfig,
        {
          agentId: ctx.agentId,
          sessionKey: ctx.sessionKey,
        },
      );
      const messageWithAssistantAggregation =
        historyMessage.role === 'assistant'
          ? annotateAssistantMessageForPersistence(
            historyMessage,
            historyConfig,
            assistantAggregationState,
            {
              agentId: ctx.agentId,
              conversationRoundId,
              sessionKey: ctx.sessionKey,
            },
          )
          : historyMessage;

      const rewritten = rewritePersistedMessage(
        messageWithAssistantAggregation,
        historyConfig,
        {
          agentId: ctx.agentId,
          conversationRoundId,
          sessionKey: ctx.sessionKey,
        },
      );

      if (!rewritten) {
        return { block: true };
      }

      return {
        message: rewritten as unknown as typeof message,
      };
    });

    api.logger.info?.(
      '[openclaw-process-message-plugin] registered process_message, preview_history_message, /process, prompt-round aggregation, and history rewrite hooks',
    );
  },
};

export default processMessagePlugin;
