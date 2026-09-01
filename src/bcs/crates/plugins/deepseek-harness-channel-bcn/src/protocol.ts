export const BCN_PROTOCOL_VERSION = 2;
export const MAX_FRAME_BYTES = 2 * 1024 * 1024;

export interface RequestFrame {
  type: 'req';
  id: string;
  method: string;
  params: Record<string, unknown>;
}

export interface ResponseFrame {
  type: 'res';
  id: string;
  ok: boolean;
  payload?: Record<string, unknown>;
  error?: {
    code: string;
    message: string;
    retryable: boolean;
    details?: unknown;
    retry_after_ms?: number;
  };
}

export interface EventFrame {
  type: 'event';
  event: string;
  payload: Record<string, unknown>;
  seq?: number;
}

export type BcnFrame = RequestFrame | ResponseFrame | EventFrame;

export interface BotSession {
  version: 1;
  endpoint: string;
  botUuid: string;
  botToken: string;
  botName: string;
}

export interface RegisterBotResponse {
  bot_name: string;
  bot_uuid: string;
  bot_token: string;
}

export interface BotConnectResponse {
  is_new: boolean;
  token: string;
  bot_uuid: string;
  protocol_version: number;
  min_supported_version?: number;
  env?: Record<string, string>;
}

export interface MessageContent {
  role: string;
  content: Array<Record<string, unknown>>;
  timestamp: number;
}

export interface ChatSendParams {
  session_key: string;
  bcs_group_id: string;
  bcs_session_id?: string;
  message: MessageContent;
  channel: Record<string, unknown>;
  session_context: Record<string, unknown>;
  timeout_ms?: number;
  idempotency_key?: string;
  tags?: string[];
  attachments?: Array<Record<string, unknown>>;
}

export interface ChatInjectParams {
  session_key: string;
  bcs_group_id: string;
  bcs_session_id?: string;
  message: MessageContent;
  channel: Record<string, unknown>;
  session_context: Record<string, unknown>;
  tags?: string[];
  attachments?: Array<Record<string, unknown>>;
}

export interface ChatAbortParams {
  session_key: string;
  run_id?: string;
}

export interface RouteSelector {
  type: 'name' | 'bot';
  value: string;
}

export interface ChatEventRouting {
  responders: Array<{ type: string; value?: string }>;
  mode: 'required' | 'optional';
  reason: string;
  include_self: boolean;
  dedupe_key?: string;
}

export interface PendingRouteIntent {
  responders: Array<{ type: string; value?: string }>;
  mode: 'required' | 'optional';
  reason: string;
  includeSelf: boolean;
  dedupeKey?: string;
}

export function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

export function asNonEmptyString(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim();
  return trimmed || undefined;
}

export function parseFrame(value: unknown): BcnFrame | undefined {
  const frame = asRecord(value);
  const id = asNonEmptyString(frame?.id);
  if (!frame || !asNonEmptyString(frame.type)) return undefined;

  if (frame.type === 'res') {
    if (!id || typeof frame.ok !== 'boolean') return undefined;
    const payload = frame.payload === undefined ? undefined : asRecord(frame.payload);
    if (frame.payload !== undefined && !payload) return undefined;
    const error = frame.error === undefined ? undefined : parseResponseError(frame.error);
    if (frame.error !== undefined && !error) return undefined;
    if (!frame.ok && !error) return undefined;
    return {
      type: 'res',
      id,
      ok: frame.ok,
      ...(payload ? { payload } : {}),
      ...(error ? { error } : {}),
    };
  }

  if (frame.type === 'req') {
    const method = asNonEmptyString(frame.method);
    const params = asRecord(frame.params);
    if (!id || !method || !params) return undefined;
    return { type: 'req', id, method, params };
  }

  if (frame.type === 'event') {
    const event = asNonEmptyString(frame.event);
    const payload = asRecord(frame.payload);
    const seq = frame.seq === undefined ? undefined : Number(frame.seq);
    if (!event || !payload || (seq !== undefined && (!Number.isSafeInteger(seq) || seq < 0))) return undefined;
    return { type: 'event', event, payload, ...(seq !== undefined ? { seq } : {}) };
  }

  return undefined;
}

function parseResponseError(value: unknown): NonNullable<ResponseFrame['error']> | undefined {
  const error = asRecord(value);
  const code = asNonEmptyString(error?.code);
  const message = asNonEmptyString(error?.message);
  if (!error || !code || !message || typeof error.retryable !== 'boolean') return undefined;
  const retryAfterMs = error.retry_after_ms;
  if (retryAfterMs !== undefined && (!Number.isSafeInteger(retryAfterMs) || Number(retryAfterMs) < 0)) {
    return undefined;
  }
  return {
    code,
    message,
    retryable: error.retryable,
    ...(Object.hasOwn(error, 'details') ? { details: error.details } : {}),
    ...(retryAfterMs !== undefined ? { retry_after_ms: Number(retryAfterMs) } : {}),
  };
}

export function parseBotSession(value: string): BotSession {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error('BCN Bot Session credential is not valid JSON');
  }
  const record = asRecord(parsed);
  const endpoint = asNonEmptyString(record?.endpoint);
  const botUuid = asNonEmptyString(record?.botUuid);
  const botToken = asNonEmptyString(record?.botToken);
  const botName = asNonEmptyString(record?.botName);
  if (record?.version !== 1 || !endpoint || !botUuid || !botToken || !botName) {
    throw new Error('BCN Bot Session credential has an unsupported or incomplete shape');
  }
  return { version: 1, endpoint, botUuid, botToken, botName };
}

export function parseChatSendParams(value: unknown): ChatSendParams {
  return parseChatParams(value, true) as ChatSendParams;
}

export function parseChatInjectParams(value: unknown): ChatInjectParams {
  return parseChatParams(value, false) as ChatInjectParams;
}

function parseChatParams(value: unknown, allowSendFields: boolean): ChatSendParams | ChatInjectParams {
  const record = asRecord(value);
  const sessionKey = asNonEmptyString(record?.session_key);
  const groupId = asNonEmptyString(record?.bcs_group_id);
  const message = asRecord(record?.message);
  const channel = asRecord(record?.channel);
  const sessionContext = asRecord(record?.session_context);
  if (!record || !sessionKey || !groupId || !message || !channel || !sessionContext) {
    throw new Error('chat params require session_key, bcs_group_id, message, channel, and session_context');
  }
  if (!Array.isArray(message.content) || !Number.isFinite(message.timestamp) || typeof message.role !== 'string') {
    throw new Error('chat message has an invalid shape');
  }
  const content = message.content.map((block) => {
    const item = asRecord(block);
    if (!item || !asNonEmptyString(item.type)) throw new Error('chat message contains an invalid content block');
    return item;
  });
  const bcsSessionId = asNonEmptyString(record.bcs_session_id);
  const attachments = Array.isArray(record.attachments)
    ? record.attachments.flatMap((item): Record<string, unknown>[] => {
      const attachment = asRecord(item);
      return attachment ? [attachment] : [];
    })
    : undefined;
  const base: ChatInjectParams = {
    session_key: sessionKey,
    bcs_group_id: groupId,
    ...(bcsSessionId ? { bcs_session_id: bcsSessionId } : {}),
    message: { role: message.role, content, timestamp: Number(message.timestamp) },
    channel,
    session_context: sessionContext,
    ...(Array.isArray(record.tags) ? { tags: record.tags.filter((item): item is string => typeof item === 'string') } : {}),
    ...(attachments ? { attachments } : {}),
  };
  if (!allowSendFields) return base;
  const idempotencyKey = asNonEmptyString(record.idempotency_key);
  return {
    ...base,
    ...(typeof record.timeout_ms === 'number' && Number.isFinite(record.timeout_ms)
      ? { timeout_ms: record.timeout_ms }
      : {}),
    ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
  };
}

export function parseChatAbortParams(value: unknown): ChatAbortParams {
  const record = asRecord(value);
  const sessionKey = asNonEmptyString(record?.session_key);
  if (!record || !sessionKey) throw new Error('chat.abort requires session_key');
  const runId = asNonEmptyString(record.run_id);
  return { session_key: sessionKey, ...(runId ? { run_id: runId } : {}) };
}

export function extractMessageText(message: MessageContent): string {
  const chunks: string[] = [];
  for (const block of message.content) {
    if (block.type === 'text' && typeof block.text === 'string') chunks.push(block.text);
  }
  const text = chunks.join('').trim();
  if (!text) throw new Error('BCN message contains no visible text');
  if (Buffer.byteLength(text, 'utf8') > 1024 * 1024) throw new Error('BCN message text exceeds 1 MiB');
  return text;
}
