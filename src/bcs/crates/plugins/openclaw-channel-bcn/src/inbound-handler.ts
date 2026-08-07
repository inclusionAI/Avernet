/**
 * Inbound message handler — ported from Python bridge.py.
 *
 * Handles chat.send requests from BCS:
 * 1. Extract text from message content blocks
 * 2. Resolve run_id, ACK immediately
 * 3. Dispatch to OpenClaw agent via SDK
 * 4. Stream response back to BCS as event frames
 */

import { randomUUID } from 'node:crypto';
import { rm } from 'node:fs/promises';
import type { BcsWsClient } from './bcs-ws-client.js';
import { resolveGatewayPort, scopesForGatewayMethod } from './gateway-security.js';
import { getBcsRuntime } from './runtime.js';
import type {
  RequestFrame,
  ChatSendParams,
  ChatInjectParams,
  ChatHistoryParams,
  ChatHistoryMessage,
  ContentBlock,
  Attachment,
  ChatEventPayload,
  ChatEventRouting,
  PendingRouteIntent,
  RouteSelectorWire,
  AgentEventPayload,
  GroupContext,
  ResolvedBcsAccount,
  SessionDeleteParams,
  TaskDispatchParams,
  TaskDispatchResponse,
  TaskMessageResponse,
  TaskGroupInfo,
} from './types.js';

const CHANNEL_ID = 'bcs';
const OPENCLAW_GATEWAY_MIN_PROTOCOL = 3;
const OPENCLAW_GATEWAY_MAX_PROTOCOL = 4;
const NO_REPLY_TEXT = 'NO_REPLY';
const MAX_IMAGE_ATTACHMENTS = 5;
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
const IMAGE_DOWNLOAD_TIMEOUT_MS = 30_000;
const IMAGE_READ_IDLE_TIMEOUT_MS = 10_000;
const RUN_TERMINAL_TIMEOUT_MS = 15 * 60 * 1000;

/** Extract sender name from [from:botName] prefix. Returns stripped text and sender name. */
function extractFromPrefix(raw: string): { senderName: string; text: string } {
  if (raw.startsWith('[from:')) {
    const end = raw.indexOf(']');
    if (end !== -1) {
      return { senderName: raw.slice(6, end), text: raw.slice(end + 1) };
    }
  }
  return { senderName: '', text: raw };
}

/** Active streams: run_id -> AbortController */
const activeStreams = new Map<string, AbortController>();

type RunContext = {
  groupId: string;
  client: BcsWsClient;
  sessionKey?: string;
  agentRunId?: string;
  finalSent?: boolean;
  sawToolEvent: boolean;
  preparedImages?: PreparedImage[];
  terminalTimer?: ReturnType<typeof setTimeout>;
};

/** Run context for agent event routing: run_id -> context used for BCS event emission. */
const runContexts = new Map<string, RunContext>();

/** Actual OpenClaw agent run ID -> BCS-visible run ID. */
const bcsRunIdByAgentRunId = new Map<string, string>();

/** In-flight prepared-image cleanup keyed by BCS-visible run ID. */
const cleanupPromiseByRunId = new Map<string, Promise<void>>();

interface VisibleReplyState {
  text: string;
  flushedOffset: number;
  segmentOffset: number;
  deltaCount: number;
  sawAssistantText: boolean;
}

/** Visible assistant reply accumulated from OpenClaw agent events. */
const visibleReplyByRunId = new Map<string, VisibleReplyState>();

/** Pending routing intents: run_id -> PendingRouteIntent (populated by bcs_route tool). */
const pendingRouteByRunId = new Map<string, PendingRouteIntent>();

/** Pending routing intents by session key — fallback when run_id is not tracked (e.g. OpenClaw-queued runs). */
const pendingRouteBySessionKey = new Map<string, PendingRouteIntent>();

/** Active run ID per session key: allows bcs_route tool to find the current run_id. */
const activeRunIdForSession = new Map<string, string>();

/** Resolve the current active run_id for a given session key (used by bcs_route tool factory). */
export function resolveActiveRunId(sessionKey: string): string | undefined {
  return activeRunIdForSession.get(sessionKey);
}

/** Session key -> BCS group ID mapping for outbound routing */
const sessionKeyToGroupId = new Map<string, string>();

/** Session key -> latest BCS session ID mapping for route validation. */
const sessionKeyToBcsSessionId = new Map<string, string>();

/** Session key -> normalized BCS group/session scope for route.resolve. */
const sessionKeyToRouteScope = new Map<string, { groupId: string; sessionId?: string }>();

/** BCS group ID -> session key mapping for history queries */
const groupIdToSessionKey = new Map<string, string>();

/** Session key -> routing_mode cache (from GroupContext.routing_mode). */
const sessionRoutingMode = new Map<string, string>();

/** Session key -> BcsWsClient reference (for task group tool handlers). */
const sessionKeyToClient = new Map<string, BcsWsClient>();

/** Session key -> task group info (for tool activation checks). */
const sessionTaskGroupInfo = new Map<string, TaskGroupInfo>();

/** Get the cached routing_mode for a session (used by tool factory to hide bcs_route). */
export function getSessionRoutingMode(sessionKey: string): string | undefined {
  return sessionRoutingMode.get(sessionKey);
}

/** Get task group info for a session (used by tool factory for activation). */
export function getSessionTaskGroupInfo(sessionKey: string): TaskGroupInfo | undefined {
  return sessionTaskGroupInfo.get(sessionKey);
}

function parseSessionScopedGroupId(value?: string): { groupId: string; sessionId: string } | undefined {
  const trimmed = value?.trim();
  if (!trimmed) return undefined;

  const separator = trimmed.indexOf(':');
  if (separator <= 0) return undefined;

  return {
    groupId: trimmed.slice(0, separator),
    sessionId: trimmed,
  };
}

function resolveBcsRouteScope(
  wireGroupId: string,
  sessionContext?: GroupContext,
  explicitBcsSessionId?: string,
): { groupId: string; sessionId?: string } {
  const explicitSession = parseSessionScopedGroupId(explicitBcsSessionId);
  if (explicitSession) return explicitSession;

  const wireSession = parseSessionScopedGroupId(wireGroupId);
  if (wireSession) return wireSession;

  const contextSession = parseSessionScopedGroupId(sessionContext?.session_id);
  if (contextSession && contextSession.groupId === wireGroupId) return contextSession;

  return { groupId: wireGroupId };
}

/** Remember BCS client and manager-worker context for task-scoped tools. */
export function rememberTaskToolSession(
  sessionKey: string,
  client: BcsWsClient,
  groupId: string,
  sessionContext?: GroupContext,
  explicitBcsSessionId?: string,
): void {
  sessionKeyToClient.set(sessionKey, client);
  if (groupId) {
    sessionKeyToGroupId.set(sessionKey, groupId);
    groupIdToSessionKey.set(groupId, sessionKey);
    const routeScope = resolveBcsRouteScope(groupId, sessionContext, explicitBcsSessionId);
    sessionKeyToRouteScope.set(sessionKey, routeScope);
    groupIdToSessionKey.set(routeScope.groupId, sessionKey);
  }
  if (sessionContext?.session_id) {
    sessionKeyToBcsSessionId.set(sessionKey, sessionContext.session_id);
  }
  if (sessionContext?.group_type) {
    sessionTaskGroupInfo.set(sessionKey, {
      groupId,
      groupType: sessionContext.group_type,
      originator: sessionContext.originator,
      participants: sessionContext.participants ?? [],
      recipientRole: sessionContext.recipient_role,
    });
  }
}

/** Look up the BCS group ID for a given session key (used by outbound.sendText). */
export function resolveGroupIdFromSessionKey(sessionKey: string): string | undefined {
  return sessionKeyToGroupId.get(sessionKey);
}

/** Look up the session key for a given BCS group ID (used by chat.history). */
export function resolveSessionKeyFromGroupId(groupId: string): string | undefined {
  return groupIdToSessionKey.get(groupId);
}

export function combineDeliveredReplyParts(deliveredParts: string[]): string | undefined {
  const combinedText = deliveredParts.join('\n\n').trim();
  return combinedText ? combinedText : undefined;
}

/** Per-client sequence counter for event frames. */
const seqCounters = new WeakMap<BcsWsClient, { value: number }>();

function nextSeq(client: BcsWsClient): number {
  let counter = seqCounters.get(client);
  if (!counter) {
    counter = { value: 0 };
    seqCounters.set(client, counter);
  }
  return ++counter.value;
}

/** Extract text from BCS message content blocks. */
function extractText(content: ContentBlock[]): string {
  const parts: string[] = [];
  for (const block of content) {
    if (block.type === 'text' && block.text) {
      parts.push(block.text);
    }
  }
  return parts.join('\n');
}

const SUPPORTED_IMAGE_MIME_TYPES = new Set([
  'image/jpeg',
  'image/png',
  'image/gif',
  'image/webp',
]);

function extractImageAttachments(attachments: Attachment[] | undefined): Attachment[] {
  return (attachments ?? []).filter(attachment => attachment.type === 'image');
}

function sanitizeAttachmentDisplay(value: string): string {
  // COSEC: prevent untrusted filenames from breaking structured prompt notes.
  return value
    .replace(/[\p{Cc}\[\]]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 200) || 'image';
}

function sanitizeAttachmentLogValue(value: string): string {
  // COSEC: prevent control-character injection from untrusted attachment metadata in logs.
  return value.replace(/[\p{Cc}]+/gu, ' ').trim().slice(0, 128) || 'unknown';
}

function attachmentFallbackText(attachments: Attachment[]): string {
  return attachments.length === 1
    ? `[Image: ${sanitizeAttachmentDisplay(attachments[0].file_name)}]`
    : `[Images: ${attachments.map(attachment => sanitizeAttachmentDisplay(attachment.file_name)).join(', ')}]`;
}

function attachmentObservationText(attachments: Attachment[]): string {
  return attachments.map(attachment => {
    const metadata = [
      `name=${sanitizeAttachmentDisplay(attachment.file_name)}`,
      attachment.mime_type ? `type=${sanitizeAttachmentDisplay(attachment.mime_type)}` : undefined,
      typeof attachment.size === 'number' ? `size=${attachment.size} bytes` : undefined,
    ].filter(Boolean).join(', ');
    return `[Image attachment: ${metadata}; image content is not available in this silent observation]`;
  }).join('\n');
}

function buildInjectText(messageText: string, attachments: Attachment[]): string {
  return [ messageText, attachmentObservationText(attachments) ].filter(Boolean).join('\n');
}

function normalizeMimeType(value?: string): string | undefined {
  const normalized = value?.split(';')[0]?.trim().toLowerCase();
  return normalized || undefined;
}

function sniffSupportedImageMime(buffer: Buffer): string | undefined {
  // COSEC: accept image content only when its magic bytes match a supported format.
  if (
    buffer.length >= 8
    && buffer.subarray(0, 8).equals(Buffer.from([ 0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a ]))
  ) {
    return 'image/png';
  }
  if (buffer.length >= 3 && buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[2] === 0xff) {
    return 'image/jpeg';
  }
  if (buffer.length >= 6) {
    const signature = buffer.toString('ascii', 0, 6);
    if (signature === 'GIF87a' || signature === 'GIF89a') return 'image/gif';
  }
  if (
    buffer.length >= 12
    && buffer.toString('ascii', 0, 4) === 'RIFF'
    && buffer.toString('ascii', 8, 12) === 'WEBP'
  ) {
    return 'image/webp';
  }
  return undefined;
}

type PreparedImage = {
  path: string;
  contentType: string;
};

type InboundImageErrorCode =
  | 'TOO_MANY_IMAGES'
  | 'IMAGE_EXPIRED'
  | 'INVALID_IMAGE_URL'
  | 'IMAGE_TOO_LARGE'
  | 'IMAGE_DOWNLOAD_TIMEOUT'
  | 'IMAGE_DOWNLOAD_FAILED'
  | 'UNSUPPORTED_IMAGE_TYPE'
  | 'IMAGE_STORE_FAILED'
  | 'IMAGE_ABORTED';

class InboundImageError extends Error {
  constructor(
    readonly code: InboundImageErrorCode,
    readonly userMessage: string,
    readonly attachmentId?: string,
  ) {
    super(code);
    this.name = 'InboundImageError';
  }
}

async function cleanupPreparedImages(images: PreparedImage[]): Promise<void> {
  await Promise.all(images.map(async image => {
    await rm(image.path, { force: true }).catch(() => undefined);
  }));
}

function bindAgentRun(
  runId: string,
  agentRunId: string,
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void },
): boolean {
  const context = runContexts.get(runId);
  if (!context) return false;

  const existingRunId = bcsRunIdByAgentRunId.get(agentRunId);
  if (existingRunId && existingRunId !== runId) {
    log?.warn?.(`[BCS] Refusing to remap agent run_id=${agentRunId} from BCS run_id=${existingRunId} to run_id=${runId}`);
    return false;
  }
  if (context.agentRunId && context.agentRunId !== agentRunId) {
    log?.warn?.(`[BCS] Refusing to replace agent run_id=${context.agentRunId} for BCS run_id=${runId} with run_id=${agentRunId}`);
    return false;
  }

  context.agentRunId = agentRunId;
  bcsRunIdByAgentRunId.set(agentRunId, runId);
  if (context.sessionKey) {
    activeRunIdForSession.set(context.sessionKey, runId);
  }
  if (agentRunId !== runId) {
    log?.info?.(`[BCS] Bound OpenClaw agent run_id=${agentRunId} to BCS run_id=${runId}`);
  }
  return true;
}

function cleanupRunContext(
  runId: string,
  log?: { info: (...args: unknown[]) => void },
): Promise<void> {
  const existingCleanup = cleanupPromiseByRunId.get(runId);
  if (existingCleanup) return existingCleanup;

  const context = runContexts.get(runId);
  if (!context) return Promise.resolve();

  if (context.terminalTimer) {
    clearTimeout(context.terminalTimer);
  }
  activeStreams.delete(runId);
  runContexts.delete(runId);
  visibleReplyByRunId.delete(runId);
  pendingRouteByRunId.delete(runId);

  if (context.agentRunId && bcsRunIdByAgentRunId.get(context.agentRunId) === runId) {
    bcsRunIdByAgentRunId.delete(context.agentRunId);
  }
  if (context.sessionKey && activeRunIdForSession.get(context.sessionKey) === runId) {
    activeRunIdForSession.delete(context.sessionKey);
    pendingRouteBySessionKey.delete(context.sessionKey);
  }

  const preparedImages = context.preparedImages ?? [];
  context.preparedImages = [];
  if (preparedImages.length === 0) return Promise.resolve();

  const cleanupPromise = cleanupPreparedImages(preparedImages)
    .then(() => {
      log?.info?.(`[BCS] Cleaned ${preparedImages.length} prepared image(s) for run_id=${runId}`);
    })
    .finally(() => {
      if (cleanupPromiseByRunId.get(runId) === cleanupPromise) {
        cleanupPromiseByRunId.delete(runId);
      }
    });
  cleanupPromiseByRunId.set(runId, cleanupPromise);
  return cleanupPromise;
}

function armRunTerminalTimeout(
  runId: string,
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void },
): void {
  const context = runContexts.get(runId);
  if (!context) return;

  const timer = setTimeout(() => {
    if (!runContexts.has(runId)) return;
    sendRunErrorOnce(
      runId,
      'Agent response timed out before completion.',
      log,
      `terminal lifecycle timeout after ${RUN_TERMINAL_TIMEOUT_MS}ms`,
    );
    void cleanupRunContext(runId, log);
  }, RUN_TERMINAL_TIMEOUT_MS);
  timer.unref?.();
  context.terminalTimer = timer;
}

function validateImageUrl(attachment: Attachment): void {
  let parsed: URL;
  try {
    parsed = new URL(attachment.url);
  } catch {
    throw new InboundImageError(
      'INVALID_IMAGE_URL',
      'The attached image has an invalid download URL.',
      attachment.attachment_id,
    );
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') {
    throw new InboundImageError(
      'INVALID_IMAGE_URL',
      'The attached image has an invalid download URL.',
      attachment.attachment_id,
    );
  }
}

async function prepareImageAttachments(params: {
  attachments: Attachment[];
  abortSignal: AbortSignal;
  runtime: ReturnType<typeof getBcsRuntime>;
}): Promise<PreparedImage[]> {
  if (params.attachments.length === 0) return [];

  if (params.attachments.length > MAX_IMAGE_ATTACHMENTS) {
    throw new InboundImageError(
      'TOO_MANY_IMAGES',
      `A message can contain at most ${MAX_IMAGE_ATTACHMENTS} images.`,
    );
  }

  const media = params.runtime.channel?.media;
  if (!media?.fetchRemoteMedia || !media?.saveMediaBuffer) {
    throw new InboundImageError(
      'IMAGE_STORE_FAILED',
      'Image processing is unavailable in the current OpenClaw runtime.',
    );
  }

  const prepared: PreparedImage[] = [];
  try {
    for (const attachment of params.attachments) {
      if (typeof attachment.expires_at === 'number' && attachment.expires_at <= Date.now()) {
        throw new InboundImageError(
          'IMAGE_EXPIRED',
          'The attached image download link has expired.',
          attachment.attachment_id,
        );
      }
      if (typeof attachment.size === 'number' && attachment.size > MAX_IMAGE_BYTES) {
        throw new InboundImageError(
          'IMAGE_TOO_LARGE',
          'The attached image exceeds the 20 MB limit.',
          attachment.attachment_id,
        );
      }
      validateImageUrl(attachment);

      const timeoutSignal = AbortSignal.timeout(IMAGE_DOWNLOAD_TIMEOUT_MS);
      const downloadSignal = AbortSignal.any([ params.abortSignal, timeoutSignal ]);
      let fetched: { buffer: Buffer; contentType?: string; fileName?: string };
      try {
        // OpenClaw's guarded media fetch applies SSRF checks to the initial URL and redirects.
        fetched = await media.fetchRemoteMedia({
          url: attachment.url,
          filePathHint: attachment.file_name,
          maxBytes: MAX_IMAGE_BYTES,
          maxRedirects: 3,
          readIdleTimeoutMs: IMAGE_READ_IDLE_TIMEOUT_MS,
          requestInit: { signal: downloadSignal },
        });
      } catch (err) {
        if (params.abortSignal.aborted) {
          throw new InboundImageError(
            'IMAGE_ABORTED',
            'Image processing was aborted.',
            attachment.attachment_id,
          );
        }
        if (timeoutSignal.aborted) {
          throw new InboundImageError(
            'IMAGE_DOWNLOAD_TIMEOUT',
            'The attached image download timed out.',
            attachment.attachment_id,
          );
        }
        if ((err as { code?: string })?.code === 'max_bytes') {
          throw new InboundImageError(
            'IMAGE_TOO_LARGE',
            'The attached image exceeds the 20 MB limit.',
            attachment.attachment_id,
          );
        }
        throw new InboundImageError(
          'IMAGE_DOWNLOAD_FAILED',
          'The attached image could not be downloaded. The link may have expired.',
          attachment.attachment_id,
        );
      }

      const contentType = Buffer.isBuffer(fetched.buffer)
        ? sniffSupportedImageMime(fetched.buffer)
        : undefined;
      if (!contentType) {
        throw new InboundImageError(
          'UNSUPPORTED_IMAGE_TYPE',
          'Unsupported image format. Supported formats are JPEG, PNG, GIF, and WebP.',
          attachment.attachment_id,
        );
      }

      let saved: { path: string; size: number; contentType?: string };
      try {
        saved = await media.saveMediaBuffer(
          fetched.buffer,
          contentType,
          'inbound',
          MAX_IMAGE_BYTES,
          fetched.fileName ?? attachment.file_name,
        );
      } catch {
        throw new InboundImageError(
          'IMAGE_STORE_FAILED',
          'The attached image could not be prepared for analysis.',
          attachment.attachment_id,
        );
      }

      const savedContentType = normalizeMimeType(saved.contentType) ?? contentType;
      if (!SUPPORTED_IMAGE_MIME_TYPES.has(savedContentType)) {
        await rm(saved.path, { force: true }).catch(() => undefined);
        throw new InboundImageError(
          'UNSUPPORTED_IMAGE_TYPE',
          'Unsupported image format. Supported formats are JPEG, PNG, GIF, and WebP.',
          attachment.attachment_id,
        );
      }
      prepared.push({
        path: saved.path,
        contentType: savedContentType,
      });
    }
    return prepared;
  } catch (err) {
    await cleanupPreparedImages(prepared);
    throw err;
  }
}

function buildChatEventPayload(
  runId: string,
  bcsGroupId: string,
  state: ChatEventPayload['state'],
  text?: string,
  routeIntent?: PendingRouteIntent,
): ChatEventPayload {
  return {
    run_id: runId,
    bcs_group_id: bcsGroupId,
    state,
    ...(text !== undefined ? {
      message: {
        role: 'assistant',
        content: [{ type: 'text', text }],
        timestamp: Date.now(),
      },
    } : {}),
    ...(routeIntent ? {
      routing: {
        responders: routeIntent.responders,
        mode: routeIntent.mode,
        reason: routeIntent.reason,
        include_self: routeIntent.includeSelf,
        dedupe_key: routeIntent.dedupeKey,
      } satisfies ChatEventRouting,
    } : {}),
  };
}

function ensureVisibleReplyState(runId: string): VisibleReplyState {
  let state = visibleReplyByRunId.get(runId);
  if (!state) {
    state = {
      text: '',
      flushedOffset: 0,
      segmentOffset: 0,
      deltaCount: 0,
      sawAssistantText: false,
    };
    visibleReplyByRunId.set(runId, state);
  }
  return state;
}

function stringField(record: unknown, key: string): string | undefined {
  if (!record || typeof record !== 'object') return undefined;
  const value = (record as Record<string, unknown>)[key];
  return typeof value === 'string' ? value : undefined;
}

function textFromUnknownContent(value: unknown): string | undefined {
  if (typeof value === 'string') return value;
  if (!Array.isArray(value)) return undefined;

  const parts = value
    .map(item => {
      if (!item || typeof item !== 'object') return undefined;
      const record = item as Record<string, unknown>;
      return typeof record.text === 'string' ? record.text : undefined;
    })
    .filter((text): text is string => typeof text === 'string');

  return parts.length > 0 ? parts.join('\n') : undefined;
}

function assistantSnapshotText(data: unknown): string | undefined {
  if (typeof data === 'string') return data;
  if (!data || typeof data !== 'object') return undefined;

  const record = data as Record<string, unknown>;
  const direct = stringField(record, 'text') ?? stringField(record, 'body');
  if (direct !== undefined) return direct;

  const content = textFromUnknownContent(record.content);
  if (content !== undefined) return content;

  const message = record.message;
  if (message && typeof message === 'object') {
    return textFromUnknownContent((message as Record<string, unknown>).content);
  }

  return undefined;
}

function assistantDeltaText(data: unknown): string | undefined {
  if (!data || typeof data !== 'object') return undefined;
  return stringField(data as Record<string, unknown>, 'delta');
}

function assistantReplacesCurrentSegment(data: unknown): boolean {
  if (!data || typeof data !== 'object') return false;
  return (data as Record<string, unknown>).replace === true;
}

function recordAssistantAgentText(runId: string, data: unknown): void {
  const state = ensureVisibleReplyState(runId);
  if (assistantReplacesCurrentSegment(data)) {
    const snapshot = assistantSnapshotText(data);
    if (snapshot === undefined) return;

    state.text = state.text.slice(0, state.segmentOffset) + snapshot;
    state.sawAssistantText = Boolean(state.text.trim());
    return;
  }

  const delta = assistantDeltaText(data);
  if (delta !== undefined && delta.length > 0) {
    state.text += delta;
    if (delta.trim()) {
      state.sawAssistantText = true;
    }
  }
}

function markVisibleReplySegmentBoundary(runId: string): void {
  const state = visibleReplyByRunId.get(runId);
  if (state) {
    state.segmentOffset = state.text.length;
  }
}

function sendVisibleReplyDelta(
  runId: string,
  log?: { info: (...args: unknown[]) => void },
): void {
  const state = visibleReplyByRunId.get(runId);
  const context = runContexts.get(runId);
  if (!state || !context) return;

  const deltaText = state.text.slice(state.flushedOffset);
  if (!deltaText.trim()) return;

  context.client.sendEvent(
    'chat.event',
    buildChatEventPayload(runId, context.groupId, 'delta', deltaText) as unknown as Record<string, unknown>,
    nextSeq(context.client),
  );
  state.flushedOffset = state.text.length;
  state.deltaCount += 1;
  log?.info?.(`[BCS] Sent chat.event delta from agent event (part ${state.deltaCount}) for run_id=${runId}, len=${deltaText.length}`);
}

function finishVisibleReply(runId: string, log?: { info: (...args: unknown[]) => void }): string | undefined {
  const state = visibleReplyByRunId.get(runId);
  if (!state?.sawAssistantText || !state.text.trim()) return undefined;

  sendVisibleReplyDelta(runId, log);
  return state.text.trim();
}

function consumeRouteIntent(runId: string, sessionKey?: string): PendingRouteIntent | undefined {
  const routeIntent = pendingRouteByRunId.get(runId);
  if (routeIntent) {
    pendingRouteByRunId.delete(runId);
    return routeIntent;
  }

  if (!sessionKey) return undefined;
  const sessionRouteIntent = pendingRouteBySessionKey.get(sessionKey);
  if (sessionRouteIntent) {
    pendingRouteBySessionKey.delete(sessionKey);
  }
  return sessionRouteIntent;
}

function sendFinalVisibleReplyOnce(
  runId: string,
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void },
  options?: {
    source?: string;
    deliveredText?: string;
    allowDeliveredTextFallback?: boolean;
    finalDeliveredPartsCount?: number;
    noReplyDetail?: string;
  },
): boolean {
  const context = runContexts.get(runId);
  if (!context || context.finalSent) return false;

  const visibleText = finishVisibleReply(runId, log);
  const deliveredText = options?.allowDeliveredTextFallback
    ? options.deliveredText?.trim() || undefined
    : undefined;
  const assistantText = visibleText ?? deliveredText;
  const toolOnlyEmpty = assistantText === undefined && context.sawToolEvent;
  const combinedText = assistantText ?? (toolOnlyEmpty ? undefined : NO_REPLY_TEXT);

  if (toolOnlyEmpty) {
    log?.info?.(`[BCS] Tool activity completed without assistant text for run_id=${runId}, sending message-less final${options?.noReplyDetail ? ` ${options.noReplyDetail}` : ''}`);
  } else if (!visibleText && !deliveredText) {
    log?.warn?.(`[BCS] No assistant agent text for run_id=${runId}, sending ${NO_REPLY_TEXT} final${options?.noReplyDetail ? ` ${options.noReplyDetail}` : ''}`);
  } else if (!visibleText && deliveredText) {
    log?.info?.(`[BCS] Using dispatcher final for non-agent run_id=${runId}`);
  } else if (options?.deliveredText && options.deliveredText !== visibleText) {
    log?.warn?.(`[BCS] Agent-event final differs from dispatcher deliver buffer for run_id=${runId}; using agent-event text`);
  } else if ((options?.finalDeliveredPartsCount ?? 0) > 1) {
    log?.info?.(`[BCS] Combined ${options?.finalDeliveredPartsCount} final deliver parts for diagnostics for run_id=${runId}`);
  }

  const routeIntent = consumeRouteIntent(runId, context.sessionKey);
  const chatPayload = buildChatEventPayload(
    runId,
    context.groupId,
    'final',
    combinedText,
    routeIntent,
  );
  context.client.sendEvent('chat.event', chatPayload as unknown as Record<string, unknown>, nextSeq(context.client));
  context.finalSent = true;

  const source = options?.source ? ` (source=${options.source})` : '';
  if (routeIntent) {
    log?.info?.(`[BCS] Sent chat.event final with routing intent for run_id=${runId}${source}`);
  } else {
    log?.info?.(`[BCS] Sent chat.event final for run_id=${runId}${source}`);
  }

  return true;
}

function sendRunErrorOnce(
  runId: string,
  userMessage: string,
  log?: { warn: (...args: unknown[]) => void },
  detail?: string,
): boolean {
  const context = runContexts.get(runId);
  if (!context || context.finalSent) return false;

  const errorPayload = buildChatEventPayload(runId, context.groupId, 'error', userMessage);
  context.client.sendEvent(
    'chat.event',
    errorPayload as unknown as Record<string, unknown>,
    nextSeq(context.client),
  );
  context.finalSent = true;
  log?.warn?.(`[BCS] Sent chat.event error for run_id=${runId}${detail ? ` (${detail})` : ''}`);
  return true;
}

/** Extract GroupContext from params for logging/context. */
function extractGroupContext(sessionContext: GroupContext): Record<string, unknown> {
  return {
    session_id: sessionContext.session_id,
    originator: sessionContext.originator,
    from: sessionContext.from,
    you_are_mentioned: sessionContext.you_are_mentioned,
    is_sender: sessionContext.is_sender,
    mentions: sessionContext.mentions,
    participants: sessionContext.participants,
    response_directive: sessionContext.response_directive,
  };
}

export function resolveChatRunId(requestId: unknown, idempotencyKey: unknown): string {
  const upstreamRunId = typeof idempotencyKey === 'string' ? idempotencyKey.trim() : '';
  if (upstreamRunId) return upstreamRunId;
  const frameRunId = typeof requestId === 'string' ? requestId.trim() : '';
  return frameRunId || randomUUID();
}

/** Handle chat.send request from BCS. */
export async function handleChatSend(
  request: RequestFrame,
  client: BcsWsClient,
  account: ResolvedBcsAccount,
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void; error: (...args: unknown[]) => void },
): Promise<void> {
  const params = request.params as unknown as ChatSendParams;
  const bcsGroupId = params.bcs_group_id;
  const channel = params.channel;
  const sessionContext = params.session_context;

  // Extract text from message content
  const imageAttachments = extractImageAttachments(params.attachments);
  const messageText = extractText(params.message?.content ?? []);
  const text = messageText || (imageAttachments.length > 0 ? attachmentFallbackText(imageAttachments) : '');
  if (!text) {
    client.sendResponse(request.id, false, undefined, {
      code: 'INVALID_REQUEST',
      message: 'Empty message',
      retryable: false,
    });
    return;
  }

  // Preserve the BCS correlation id when available. Older callers that do not
  // provide one still receive a stable request-id or generated run-id fallback.
  const runId = resolveChatRunId(request.id, params.idempotency_key);
  client.sendResponse(request.id, true, { run_id: runId });

  const { senderName, text: strippedText } = extractFromPrefix(text);
  const displayName = senderName || channel?.user_id || sessionContext?.from || 'bcs-bot';

  const preview = strippedText.length > 100 ? `${strippedText.slice(0, 100)}...` : strippedText;
  const ctxInfo = sessionContext ? extractGroupContext(sessionContext) : {};
  log?.info?.(`chat.send from ${displayName} in ${bcsGroupId || 'onboarding'}: ${preview}`, ctxInfo);

  // Track active stream for abort support
  const abortController = new AbortController();
  activeStreams.set(runId, abortController);
  let preparedImages: PreparedImage[] = [];

  // Track run context for agent event routing
  runContexts.set(runId, { groupId: bcsGroupId, client, sawToolEvent: false });
  ensureVisibleReplyState(runId);
  armRunTerminalTimeout(runId, log);

  try {
    const rt = getBcsRuntime();
    const currentCfg = await rt.config.loadConfig();
    preparedImages = await prepareImageAttachments({
      attachments: imageAttachments,
      abortSignal: abortController.signal,
      runtime: rt,
    });
    const preparedImageContext = runContexts.get(runId);
    if (preparedImageContext) {
      preparedImageContext.preparedImages = preparedImages;
    }

    // Resolve agent route to find the correct agent for this session
    const route = rt.channel.routing.resolveAgentRoute({
      cfg: currentCfg,
      channel: CHANNEL_ID,
      accountId: account.accountId,
      peer: {
        kind: 'group',
        id: bcsGroupId || `onboarding-${account.botId}`,
      },
    });

    log?.info?.(`[DEBUG] Resolved agent route: agentId=${route.agentId}, sessionKey=${route.sessionKey}`);
    const runContext = runContexts.get(runId);
    if (runContext) {
      runContext.sessionKey = route.sessionKey;
    }

    // Record bidirectional mapping: sessionKey <-> bcsGroupId
    if (bcsGroupId) {
      sessionKeyToGroupId.set(route.sessionKey, bcsGroupId);
      groupIdToSessionKey.set(bcsGroupId, route.sessionKey);
    }

    // Track client reference and manager-worker context for task group tools
    rememberTaskToolSession(route.sessionKey, client, bcsGroupId, sessionContext, params.bcs_session_id);

    // Cache routing_mode so tool factory can hide bcs_route when mode=mention
    if (sessionContext?.routing_mode) {
      sessionRoutingMode.set(route.sessionKey, sessionContext.routing_mode);
    }

    // Build inbound context using SDK's finalizeInboundContext
    // Use bcsGroupId for To/OriginatingTo so agent's outbound messages route correctly
    // BCS v2+ prepends GroupContext header to message content, so pass text as-is
    const msgCtx = rt.channel.reply.finalizeInboundContext({
      Body: text,
      RawBody: text,
      CommandBody: text,
      From: `bcs:${displayName}`,
      To: bcsGroupId || `bcs:${account.botId}`,
      SessionKey: route.sessionKey,
      AccountId: account.accountId,
      OriginatingChannel: CHANNEL_ID,
      OriginatingTo: bcsGroupId || `bcs:${account.botId}`,
      ChatType: 'group',
      SenderName: sessionContext.from_bot_owner ?? undefined,
      SenderId: sessionContext.from_bot_id ?? undefined,
      Provider: CHANNEL_ID,
      Surface: CHANNEL_ID,
      ConversationLabel: bcsGroupId ? `BCS Group ${bcsGroupId}` : 'BCS Onboarding',
      Timestamp: Date.now(),
      CommandAuthorized: true,
      MediaPath: preparedImages[0]?.path,
      MediaType: preparedImages[0]?.contentType,
      MediaPaths: preparedImages.length > 0
        ? preparedImages.map(image => image.path)
        : undefined,
      MediaTypes: preparedImages.length > 0
        ? preparedImages.map(image => image.contentType)
        : undefined,
    });

    // Resolve store path for session storage using the resolved agentId
    const storePath = rt.channel.session.resolveStorePath(currentCfg.session?.store, {
      agentId: route.agentId,
    });

    log?.info?.(`[DEBUG] Recording inbound session: storePath=${storePath}, sessionKey=${route.sessionKey}, agentId=${route.agentId}`);

    // Record inbound session to save user message (makes it visible in UI)
    // Use the actual BCS group ID as the route "to" so replies go back to the correct group
    try {
      await rt.channel.session.recordInboundSession({
        storePath,
        sessionKey: route.sessionKey,
        ctx: msgCtx,
        createIfMissing: true,
        updateLastRoute: {
          sessionKey: route.sessionKey,
          channel: CHANNEL_ID,
          to: bcsGroupId || `bcs:${account.botId}`,
          accountId: account.accountId,
        },
        onRecordError: (err: any) => {
          log?.warn?.('Failed to record inbound session:', err);
        },
      });
      log?.info?.('[DEBUG] Successfully recorded inbound session');
    } catch (err) {
      log?.error?.('[DEBUG] Error recording inbound session:', err);
    }

    // DEBUG: Send test message to verify plugin is loaded
    log?.info?.('[BCN PLUGIN LOADED] test message from inbound-handler');

    // Keep SDK deliver parts only for diagnostics; chat delta/final are built
    // from assistant agent events so BCS sees the same visible reply stream.
    const finalDeliveredParts: string[] = [];
    const blockDeliveredParts: string[] = [];
    const replyOptions = {
      runId,
      abortSignal: abortController.signal,
      disableBlockStreaming: false,
      sourceReplyDeliveryMode: 'automatic' as const,
      onAgentRunStart: (agentRunId: string) => {
        bindAgentRun(runId, agentRunId, log);
      },
    };

    // Dispatch via SDK's buffered block dispatcher
    await rt.channel.reply.dispatchReplyWithBufferedBlockDispatcher({
      ctx: msgCtx,
      cfg: currentCfg,
      dispatcherOptions: {
        deliver: async (
          payload: { text?: string; body?: string },
          info?: { kind?: string },
        ) => {
          if (abortController.signal.aborted) return;
          const replyText = payload?.text ?? payload?.body ?? '';
          const kind = info?.kind ?? 'final';

          if (kind === 'block') {
            if (!replyText.trim()) return;
            blockDeliveredParts.push(replyText);
            log?.info?.(`[BCS] buffered block deliver for run_id=${runId}, len=${replyText.length}`);
            return;
          }

          finalDeliveredParts.push(replyText);
          log?.info?.(`[BCS] deliver called (kind=${kind}, final part ${finalDeliveredParts.length}) for run_id=${runId}, len=${replyText.length}`);
        },
        onReplyStart: () => {
          log?.info?.(`Agent reply started for run_id=${runId}`);
        },
      },
      // Pass our runId so SDK uses it for agent events — enables runContexts lookup
      replyOptions,
    });

    const settledContext = runContexts.get(runId);
    if (!settledContext) {
      await cleanupRunContext(runId, log);
      log?.info?.(`[BCS] Dispatcher settled after terminal lifecycle for run_id=${runId}`);
      return;
    }
    if (abortController.signal.aborted) {
      await cleanupRunContext(runId, log);
      return;
    }

    const combinedFinalText = combineDeliveredReplyParts(finalDeliveredParts);
    if (!settledContext.agentRunId && combinedFinalText) {
      sendFinalVisibleReplyOnce(runId, log, {
        source: 'dispatcher_non_agent',
        deliveredText: combinedFinalText,
        allowDeliveredTextFallback: true,
        finalDeliveredPartsCount: finalDeliveredParts.length,
      });
      await cleanupRunContext(runId, log);
      return;
    }

    log?.info?.(
      settledContext.agentRunId
        ? `[BCS] Dispatcher settled for run_id=${runId}; waiting for terminal lifecycle from agent run_id=${settledContext.agentRunId}`
        : `[BCS] Dispatcher settled without an agent start for run_id=${runId}; retaining context for a queued agent run (block deliver parts=${blockDeliveredParts.length}, final deliver parts=${finalDeliveredParts.length})`,
    );
  } catch (err: any) {
    const imageError = err instanceof InboundImageError ? err : undefined;
    if (imageError) {
      log?.error?.(
        `[BCS] Image preparation failed for run_id=${runId}, attachment_id=${sanitizeAttachmentLogValue(imageError.attachmentId ?? 'unknown')}, code=${imageError.code}`,
      );
    } else {
      log?.error?.(`Error processing chat.send for run_id=${runId}: ${err?.message ?? err}`);
      if (err?.stack) {
        log?.error?.(`[DEBUG] Stack trace for run_id=${runId}:\n${err.stack}`);
      }
      if (err?.cause) {
        log?.error?.(`[DEBUG] Error cause for run_id=${runId}:`, err.cause);
      }
    }

    if (!runContexts.has(runId) || runContexts.get(runId)?.finalSent) {
      log?.warn?.(`[BCS] Dispatcher failed after final was already sent for run_id=${runId}; suppressing duplicate error event`);
    } else {
      sendRunErrorOnce(
        runId,
        imageError?.userMessage ?? 'An error occurred while processing your message.',
        log,
        'dispatcher failure',
      );
    }
    await cleanupRunContext(runId, log);
  }
}

/** Handle session.delete request from BCS - delete local session via gateway. */
export async function handleSessionDelete(
  request: RequestFrame,
  client: BcsWsClient,
  _account: ResolvedBcsAccount,
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void; error: (...args: unknown[]) => void },
  dataDir?: string,
): Promise<void> {
  const params = request.params as unknown as SessionDeleteParams;
  const bcsGroupId = params.bcs_group_id;
  log?.info?.(`[session.delete] Deleting session for group ${bcsGroupId}`);

  // 1. Find sessionKey from memory mapping
  const sessionKey = groupIdToSessionKey.get(bcsGroupId);

  if (!sessionKey) {
    log?.warn?.(`[session.delete] No sessionKey mapping found for group ${bcsGroupId}`);
    client.sendResponse(request.id, true, {}); // ACK anyway
    return;
  }

  try {
    const rt = getBcsRuntime();
    const currentCfg = await rt.config.loadConfig();

    // 2. Call gateway sessions.delete
    await callGatewaySessionDelete({
      cfg: currentCfg,
      sessionKey,
      dataDir,
      log,
    });

    // 3. Clean up memory mappings
    sessionKeyToGroupId.delete(sessionKey);
    groupIdToSessionKey.delete(bcsGroupId);
    sessionRoutingMode.delete(sessionKey);
    sessionKeyToClient.delete(sessionKey);
    sessionTaskGroupInfo.delete(sessionKey);

    log?.info?.(`[session.delete] Session deleted: ${sessionKey}`);
    client.sendResponse(request.id, true, {});
  } catch (err) {
    log?.error?.(`[session.delete] Failed: ${err instanceof Error ? err.message : String(err)}`);
    client.sendResponse(request.id, false, undefined, {
      code: 'DELETE_FAILED',
      message: err instanceof Error ? err.message : String(err),
      retryable: false,
    });
  }
}

/** Call OpenClaw gateway sessions.delete via WebSocket. */
async function callGatewaySessionDelete(params: {
  cfg: Record<string, unknown>;
  sessionKey: string;
  dataDir?: string;
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void };
}): Promise<void> {
  const { cfg, sessionKey, dataDir, log } = params;

  const port = resolveGatewayPort(cfg);

  const token = (cfg as { gateway?: { auth?: { token?: string } } }).gateway?.auth?.token;
  if (!token) {
    log?.warn?.('sessions.delete: no gateway token found');
    throw new Error('No gateway token found');
  }

  const os = await import('node:os');
  const path = await import('node:path');
  const crypto = await import('node:crypto');
  const keyFile = path.join(dataDir || os.homedir(), '.openclaw', 'bcs_device_key.pem');
  const { privateKey, publicKeyB64, deviceId } = await loadOrCreateDeviceKeypair(keyFile);

  const WebSocket = (await import('ws')).default;
  const ws = new WebSocket(`ws://127.0.0.1:${port}`);

  await new Promise<void>((resolve, reject) => {
    const connectId = `connect-${randomUUID()}`;
    const reqId = `delete-${randomUUID()}`;
    let connected = false;

    const timeout = setTimeout(() => {
      ws.close();
      reject(new Error('gateway sessions.delete timeout'));
    }, 8000);

    ws.on('message', data => {
      try {
        const frame = JSON.parse(data.toString());

        // Step 1: receive connect.challenge, get nonce
        if (frame.type === 'event' && frame.event === 'connect.challenge') {
          const nonce = frame.payload?.nonce ?? '';
          const signedAtMs = Date.now();
          const scopes = scopesForGatewayMethod('sessions.delete');

          // Build v3 device auth payload and sign it
          const payloadStr = [
            'v3', deviceId, 'gateway-client', 'backend', 'operator',
            scopes.join(','), String(signedAtMs), token, nonce, 'node', '',
          ].join('|');
          const signature = crypto.sign(null, Buffer.from(payloadStr, 'utf8'), privateKey).toString('base64url');

          ws.send(JSON.stringify({
            type: 'req',
            id: connectId,
            method: 'connect',
            params: {
              minProtocol: OPENCLAW_GATEWAY_MIN_PROTOCOL,
              maxProtocol: OPENCLAW_GATEWAY_MAX_PROTOCOL,
              client: { id: 'gateway-client', version: '1.0.0', platform: 'node', mode: 'backend' },
              auth: { token },
              scopes,
              role: 'operator',
              device: { id: deviceId, publicKey: publicKeyB64, signature, signedAt: signedAtMs, nonce },
            },
          }));
          return;
        }

        // Step 2: connect response
        if (!connected && frame.id === connectId) {
          if (!frame.ok) {
            clearTimeout(timeout);
            ws.close();
            reject(new Error(`gateway connect failed: ${JSON.stringify(frame.error)}`));
            return;
          }
          connected = true;
          // Call sessions.delete using the public gateway protocol shape.
          ws.send(JSON.stringify({
            type: 'req',
            id: reqId,
            method: 'sessions.delete',
            params: { key: sessionKey },
          }));
          return;
        }

        // Step 3: delete response
        if (frame.id === reqId) {
          clearTimeout(timeout);
          ws.close();
          if (frame.ok) {
            log?.info?.(`[sessions.delete] Deleted session: ${sessionKey}`);
            resolve();
          } else {
            reject(new Error(`gateway sessions.delete failed: ${JSON.stringify(frame.error)}`));
          }
        }
      } catch {
        // ignore parse errors
      }
    });

    ws.on('error', err => {
      clearTimeout(timeout);
      reject(new Error(`gateway connection error: ${err.message}`));
    });

    ws.on('close', () => {
      clearTimeout(timeout);
    });
  });
}

/** Handle chat.inject request from BCS - observe silently without responding.
 *
 * BCS sends chat.inject to bots that should observe but NOT respond.
 * The bot receives the message for context awareness only.
 */
export async function handleChatInject(
  request: RequestFrame,
  client: BcsWsClient,
  account: ResolvedBcsAccount,
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void; error: (...args: unknown[]) => void },
  dataDir?: string,
): Promise<void> {
  const params = request.params as unknown as ChatInjectParams;
  const bcsGroupId = params.bcs_group_id;
  const channel = params.channel;
  const sessionContext = params.session_context;

  // Extract text from message content
  const imageAttachments = extractImageAttachments(params.attachments);
  const messageText = extractText(params.message?.content ?? []);
  const text = buildInjectText(messageText, imageAttachments);
  if (!text) {
    client.sendResponse(request.id, false, undefined, {
      code: 'INVALID_REQUEST',
      message: 'Empty message',
      retryable: false,
    });
    return;
  }

  const { senderName, text: strippedText } = extractFromPrefix(text);
  const displayName = senderName || channel?.user_id || sessionContext?.from || 'bcs-bot';
  const preview = strippedText.length > 100 ? `${strippedText.slice(0, 100)}...` : strippedText;
  const ctxInfo = sessionContext ? extractGroupContext(sessionContext) : {};
  log?.info?.(`chat.inject from ${displayName} in ${bcsGroupId} (observe only): ${preview}`, ctxInfo);

  // ACK immediately - no response needed for inject
  client.sendResponse(request.id, true, {});

  // Record into OpenClaw session and inject message via gateway
  try {
    const rt = getBcsRuntime();
    const currentCfg = await rt.config.loadConfig();

    const route = rt.channel.routing.resolveAgentRoute({
      cfg: currentCfg,
      channel: CHANNEL_ID,
      accountId: account.accountId,
      peer: {
        kind: 'group',
        id: bcsGroupId || `onboarding-${account.botId}`,
      },
    });

    // Record bidirectional mapping: sessionKey <-> bcsGroupId
    if (bcsGroupId) {
      sessionKeyToGroupId.set(route.sessionKey, bcsGroupId);
      groupIdToSessionKey.set(bcsGroupId, route.sessionKey);
    }

    // Track client reference and manager-worker context for task group tools
    rememberTaskToolSession(route.sessionKey, client, bcsGroupId, sessionContext, params.bcs_session_id);

    // Cache routing_mode so tool factory can hide bcs_route when mode=mention
    if (sessionContext?.routing_mode) {
      sessionRoutingMode.set(route.sessionKey, sessionContext.routing_mode);
    }

    // Use bcsGroupId for To/OriginatingTo so agent's outbound messages route correctly
    // BCS v2+ prepends GroupContext header to message content, so pass text as-is
    const msgCtx = rt.channel.reply.finalizeInboundContext({
      Body: text,
      RawBody: text,
      CommandBody: text,
      From: `bcs:${displayName}`,
      To: bcsGroupId || `bcs:${account.botId}`,
      SessionKey: route.sessionKey,
      AccountId: account.accountId,
      OriginatingChannel: CHANNEL_ID,
      OriginatingTo: bcsGroupId || `bcs:${account.botId}`,
      ChatType: 'group',
      SenderName: sessionContext.from_bot_owner ?? undefined,
      SenderId: sessionContext.from_bot_id ?? undefined,
      Provider: CHANNEL_ID,
      Surface: CHANNEL_ID,
      ConversationLabel: bcsGroupId ? `BCS Group ${bcsGroupId}` : 'BCS Onboarding',
      Timestamp: Date.now(),
      CommandAuthorized: false,
    });

    const storePath = rt.channel.session.resolveStorePath(currentCfg.session?.store, {
      agentId: route.agentId,
    });

    // Ensure session exists in sessions.json before calling gateway chat.inject
    // Use the actual BCS group ID as the route "to" so replies go back to the correct group
    await rt.channel.session.recordInboundSession({
      storePath,
      sessionKey: route.sessionKey,
      ctx: msgCtx,
      createIfMissing: true,
      updateLastRoute: {
        sessionKey: route.sessionKey,
        channel: CHANNEL_ID,
        to: bcsGroupId || `bcs:${account.botId}`,
        accountId: account.accountId,
      },
      onRecordError: (err: any) => {
        log?.warn?.('chat.inject: failed to record session:', err);
      },
    });

    // Ensure transcript (.jsonl) file exists - gateway chat.inject requires it
    const fs = await import('node:fs');
    const path = await import('node:path');
    const sessionsDir = path.dirname(storePath);
    const sessionsData = JSON.parse(fs.readFileSync(storePath, 'utf8'));
    const sessionEntry = sessionsData[route.sessionKey];
    const sessionId = sessionEntry?.sessionId;
    if (sessionId) {
      const transcriptPath = path.join(sessionsDir, `${sessionId}.jsonl`);
      if (!fs.existsSync(transcriptPath)) {
        const header = JSON.stringify({ type: 'session', version: 3, id: sessionId, timestamp: new Date().toISOString() });
        fs.writeFileSync(transcriptPath, header + '\n');
        log?.info?.(`[chat.inject] created transcript file: ${transcriptPath}`);
      }
    }

    // Call gateway chat.inject to write message to transcript (visible in UI)
    await callGatewayChatInject({
      cfg: currentCfg,
      sessionKey: route.sessionKey,
      message: text,
      dataDir,
      log,
    });
  } catch (err) {
    log?.warn?.(`chat.inject: error: ${err instanceof Error ? err.message : String(err)}`);
  }
}

/** Load or create Ed25519 device keypair, returns { privateKey, publicKeyB64, deviceId } */
async function loadOrCreateDeviceKeypair(keyFile: string): Promise<{
  privateKey: import('node:crypto').KeyObject;
  publicKeyB64: string;
  deviceId: string;
}> {
  const fs = await import('node:fs');
  const crypto = await import('node:crypto');
  const path = await import('node:path');

  const dir = path.dirname(keyFile);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  let privateKey: import('node:crypto').KeyObject;

  if (fs.existsSync(keyFile)) {
    const pem = fs.readFileSync(keyFile, 'utf8');
    privateKey = crypto.createPrivateKey(pem);
  } else {
    const { privateKey: pk } = crypto.generateKeyPairSync('ed25519');
    privateKey = pk;
    const pem = pk.export({ type: 'pkcs8', format: 'pem' }) as string;
    fs.writeFileSync(keyFile, pem, { mode: 0o600 });
  }

  const publicKey = crypto.createPublicKey(privateKey);
  const pubBytes = publicKey.export({ type: 'spki', format: 'der' }).slice(-32); // raw 32 bytes
  const publicKeyB64 = pubBytes.toString('base64url');
  const deviceId = crypto.createHash('sha256').update(pubBytes).digest('hex');

  return { privateKey, publicKeyB64, deviceId };
}

/** Call OpenClaw gateway chat.inject via WebSocket to write message to transcript. */
async function callGatewayChatInject(params: {
  cfg: Record<string, unknown>;
  sessionKey: string;
  message: string;
  dataDir?: string;
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void };
}): Promise<void> {
  const { cfg, sessionKey, message, dataDir, log } = params;

  const port = resolveGatewayPort(cfg);

  const token = (cfg as { gateway?: { auth?: { token?: string } } }).gateway?.auth?.token;
  if (!token) {
    log?.warn?.('chat.inject: no gateway token found, skipping transcript write');
    return;
  }

  const os = await import('node:os');
  const path = await import('node:path');
  const crypto = await import('node:crypto');
  const keyFile = path.join(dataDir || os.homedir(), '.openclaw', 'bcs_device_key.pem');
  const { privateKey, publicKeyB64, deviceId } = await loadOrCreateDeviceKeypair(keyFile);

  const WebSocket = (await import('ws')).default;
  const ws = new WebSocket(`ws://127.0.0.1:${port}`);

  await new Promise<void>((resolve, reject) => {
    const connectId = `connect-${randomUUID()}`;
    const reqId = `inject-${randomUUID()}`;
    let connected = false;

    const timeout = setTimeout(() => {
      ws.close();
      reject(new Error('gateway chat.inject timeout'));
    }, 8000);

    ws.on('message', data => {
      try {
        const frame = JSON.parse(data.toString());

        // Step 1: receive connect.challenge, get nonce
        if (frame.type === 'event' && frame.event === 'connect.challenge') {
          const nonce = frame.payload?.nonce ?? '';
          const signedAtMs = Date.now();
          const scopes = scopesForGatewayMethod('chat.inject');

          // Build v3 device auth payload and sign it
          const payloadStr = [
            'v3', deviceId, 'gateway-client', 'backend', 'operator',
            scopes.join(','), String(signedAtMs), token, nonce, 'node', '',
          ].join('|');
          const signature = crypto.sign(null, Buffer.from(payloadStr, 'utf8'), privateKey).toString('base64url');

          ws.send(JSON.stringify({
            type: 'req',
            id: connectId,
            method: 'connect',
            params: {
              minProtocol: OPENCLAW_GATEWAY_MIN_PROTOCOL,
              maxProtocol: OPENCLAW_GATEWAY_MAX_PROTOCOL,
              client: { id: 'gateway-client', version: '1.0.0', platform: 'node', mode: 'backend' },
              auth: { token },
              scopes,
              role: 'operator',
              device: { id: deviceId, publicKey: publicKeyB64, signature, signedAt: signedAtMs, nonce },
            },
          }));
          return;
        }

        // Step 2: connect response
        if (!connected && frame.id === connectId) {
          if (!frame.ok) {
            clearTimeout(timeout);
            ws.close();
            reject(new Error(`gateway connect failed: ${JSON.stringify(frame.error)}`));
            return;
          }
          connected = true;
          ws.send(JSON.stringify({ type: 'req', id: reqId, method: 'chat.inject', params: { sessionKey, message } }));
          return;
        }

        // Step 3: inject response
        if (frame.id === reqId) {
          clearTimeout(timeout);
          ws.close();
          if (frame.ok) {
            resolve();
          } else {
            reject(new Error(`gateway chat.inject failed: ${JSON.stringify(frame.error)}`));
          }
        }
      } catch {
        // ignore parse errors
      }
    });

    ws.once('error', err => {
      clearTimeout(timeout);
      reject(err);
    });
  });
}

/** Handle chat.history request from BCS - fetch session history from OpenClaw gateway. */
export async function handleChatHistory(
  request: RequestFrame,
  client: BcsWsClient,
  account: ResolvedBcsAccount,
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void; error: (...args: unknown[]) => void },
  dataDir?: string,
): Promise<void> {
  const params = request.params as unknown as ChatHistoryParams;
  const bcsGroupId = params.session_key;

  if (!bcsGroupId) {
    client.sendResponse(request.id, false, undefined, {
      code: 'INVALID_REQUEST',
      message: 'Missing session_key',
      retryable: false,
    });
    return;
  }

  try {
    const rt = getBcsRuntime();
    const currentCfg = await rt.config.loadConfig();

    // Look up the OpenClaw sessionKey from the reverse mapping
    let sessionKey = resolveSessionKeyFromGroupId(bcsGroupId);

    // If not found in mapping, resolve via routing (for new sessions)
    if (!sessionKey) {
      const route = rt.channel.routing.resolveAgentRoute({
        cfg: currentCfg,
        channel: CHANNEL_ID,
        accountId: account.accountId,
        peer: {
          kind: 'group',
          id: bcsGroupId,
        },
      });
      sessionKey = route.sessionKey;
      log?.warn?.(`[chat.history] No mapping found for ${bcsGroupId}, resolved to ${sessionKey}`);
    } else {
      log?.info?.(`[chat.history] Found mapping: ${bcsGroupId} -> ${sessionKey}`);
    }

    const messages = await callGatewayChatHistory({
      cfg: currentCfg,
      sessionKey: sessionKey!,
      limit: params.limit,
      dataDir,
      log,
    });

    client.sendResponse(request.id, true, {
      session_key: bcsGroupId,
      messages,
    });
  } catch (err) {
    log?.error?.(`chat.history error: ${err instanceof Error ? err.message : String(err)}`);
    client.sendResponse(request.id, false, undefined, {
      code: 'INTERNAL_ERROR',
      message: err instanceof Error ? err.message : String(err),
      retryable: true,
    });
  }
}

/** Call OpenClaw gateway chat.history via WebSocket to fetch session transcript. */
async function callGatewayChatHistory(params: {
  cfg: Record<string, unknown>;
  sessionKey: string;
  limit?: number;
  dataDir?: string;
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void };
}): Promise<ChatHistoryMessage[]> {
  const { cfg, sessionKey, limit, dataDir, log } = params;

  const port = resolveGatewayPort(cfg);

  const token = (cfg as { gateway?: { auth?: { token?: string } } }).gateway?.auth?.token;
  if (!token) {
    log?.warn?.('chat.history: no gateway token found');
    return [];
  }

  const os = await import('node:os');
  const path = await import('node:path');
  const crypto = await import('node:crypto');
  const keyFile = path.join(dataDir || os.homedir(), '.openclaw', 'bcs_device_key.pem');
  const { privateKey, publicKeyB64, deviceId } = await loadOrCreateDeviceKeypair(keyFile);

  const WebSocket = (await import('ws')).default;
  const ws = new WebSocket(`ws://127.0.0.1:${port}`);

  return new Promise<ChatHistoryMessage[]>((resolve, reject) => {
    const connectId = `connect-${randomUUID()}`;
    const reqId = `history-${randomUUID()}`;
    let connected = false;

    const timeout = setTimeout(() => {
      ws.close();
      reject(new Error('gateway chat.history timeout'));
    }, 8000);

    ws.on('message', data => {
      try {
        const frame = JSON.parse(data.toString());

        if (frame.type === 'event' && frame.event === 'connect.challenge') {
          const nonce = frame.payload?.nonce ?? '';
          const signedAtMs = Date.now();
          const scopes = scopesForGatewayMethod('chat.history');
          const payloadStr = [
            'v3', deviceId, 'gateway-client', 'backend', 'operator',
            scopes.join(','), String(signedAtMs), token, nonce, 'node', '',
          ].join('|');
          const signature = crypto.sign(null, Buffer.from(payloadStr, 'utf8'), privateKey).toString('base64url');

          ws.send(JSON.stringify({
            type: 'req',
            id: connectId,
            method: 'connect',
            params: {
              minProtocol: OPENCLAW_GATEWAY_MIN_PROTOCOL,
              maxProtocol: OPENCLAW_GATEWAY_MAX_PROTOCOL,
              client: { id: 'gateway-client', version: '1.0.0', platform: 'node', mode: 'backend' },
              auth: { token },
              scopes,
              role: 'operator',
              device: { id: deviceId, publicKey: publicKeyB64, signature, signedAt: signedAtMs, nonce },
            },
          }));
          return;
        }

        if (!connected && frame.id === connectId) {
          if (!frame.ok) {
            clearTimeout(timeout);
            ws.close();
            reject(new Error(`gateway connect failed: ${JSON.stringify(frame.error)}`));
            return;
          }
          connected = true;
          ws.send(JSON.stringify({
            type: 'req',
            id: reqId,
            method: 'chat.history',
            params: { sessionKey, limit: limit ?? 200 },
          }));
          return;
        }

        if (frame.id === reqId) {
          clearTimeout(timeout);
          ws.close();
          if (frame.ok) {
            resolve((frame.payload?.messages as ChatHistoryMessage[]) ?? []);
          } else {
            reject(new Error(`gateway chat.history failed: ${JSON.stringify(frame.error)}`));
          }
        }
      } catch {
        // ignore parse errors
      }
    });

    ws.once('error', err => {
      clearTimeout(timeout);
      reject(err);
    });
  });
}

/** Clear all active streams (for shutdown). */
export function abortAllStreams(): void {
  for (const controller of activeStreams.values()) {
    controller.abort();
  }
  for (const runId of [ ...runContexts.keys() ]) {
    void cleanupRunContext(runId);
  }
  activeStreams.clear();
  runContexts.clear();
  bcsRunIdByAgentRunId.clear();
  visibleReplyByRunId.clear();
  pendingRouteByRunId.clear();
  pendingRouteBySessionKey.clear();
  activeRunIdForSession.clear();
  sessionKeyToGroupId.clear();
  sessionKeyToBcsSessionId.clear();
  sessionKeyToRouteScope.clear();
  groupIdToSessionKey.clear();
  sessionRoutingMode.clear();
  sessionKeyToClient.clear();
  sessionTaskGroupInfo.clear();
}

// ---------------------------------------------------------------------------
// bcs_route tool handler (Task 9)
// ---------------------------------------------------------------------------

/** bcs_route tool parameter schema for LLM tool registration. */
export const BCS_ROUTE_TOOL_SCHEMA = {
  name: 'bcs_route',
  description:
    'Specify which bot(s) in this BCS group should respond next. ' +
    'Use this instead of writing @Bot in your reply text. ' +
    'The routing intent is captured locally and attached to your final reply automatically. ' +
    'If called multiple times, targets accumulate (union of all calls).',
  parameters: {
    type: 'object' as const,
    properties: {
      to: {
        type: 'array' as const,
        items: {
          type: 'object' as const,
          properties: {
            type: {
              type: 'string' as const,
              enum: [ 'name', 'bot' ],
              description: "'name' targets by display name (e.g. 'DBA'), 'bot' targets by bot_uuid.",
            },
            value: {
              type: 'string' as const,
              description: 'Bot display name or bot_uuid.',
            },
          },
          required: [ 'type', 'value' ],
        },
        description: 'Target bot(s). Multiple selectors are OR/union.',
      },
      reason: {
        type: 'string' as const,
        description: 'Why this routing is needed.',
      },
    },
    required: [ 'to', 'reason' ],
  },
};

interface BcsRouteCandidate {
  bot_uuid: string;
  bot_name?: string;
  role?: string;
}

interface BcsRouteResolvedTarget {
  type: 'bot';
  value: string;
  bot_name?: string;
  role?: string;
}

interface BcsRouteToolResult {
  ok: boolean;
  captured?: boolean;
  display_to_user?: boolean;
  error?: string;
  message?: string;
  candidates?: BcsRouteCandidate[];
  resolved?: BcsRouteResolvedTarget[];
}

interface BcsRouteParams {
  to: RouteSelectorWire[];
  reason: string;
}

const MAX_ROUTE_RESPONDERS = 20;
const MAX_ROUTE_REASON_LENGTH = 500;

function isBcsRouteToolResult(value: BcsRouteToolResult | BcsRouteParams): value is BcsRouteToolResult {
  return 'ok' in value;
}

function parseBcsRouteParams(params: Record<string, unknown>): BcsRouteParams | BcsRouteToolResult {
  const to = params.to as Array<{ type?: unknown; value?: unknown }> | undefined;
  const reason = params.reason as string | undefined;

  if (!to || !Array.isArray(to) || to.length === 0) {
    return { ok: false, error: 'INVALID_PARAMS', message: "'to' must be a non-empty array of selectors" };
  }
  if (!reason || typeof reason !== 'string' || reason.trim() === '') {
    return { ok: false, error: 'INVALID_PARAMS', message: "'reason' is required and must be non-empty" };
  }

  const selectors: RouteSelectorWire[] = [];
  for (const selector of to) {
    const type = selector?.type;
    const value = typeof selector?.value === 'string' ? selector.value.trim() : undefined;
    if (type !== 'name' && type !== 'bot') {
      return { ok: false, error: 'INVALID_PARAMS', message: "selector.type must be 'name' or 'bot'" };
    }
    if (!value) {
      return { ok: false, error: 'INVALID_PARAMS', message: 'selector.value must be a non-empty string' };
    }
    selectors.push({ type, value });
  }

  return { to: selectors, reason: reason.trim() };
}

function normalizeRouteCandidates(value: unknown): BcsRouteCandidate[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const candidates: BcsRouteCandidate[] = [];
  for (const candidate of value) {
    if (!candidate || typeof candidate !== 'object') continue;
    const record = candidate as Record<string, unknown>;
    if (typeof record.bot_uuid !== 'string' || !record.bot_uuid) continue;
    candidates.push({
      bot_uuid: record.bot_uuid,
      bot_name: typeof record.bot_name === 'string' ? record.bot_name : undefined,
      role: typeof record.role === 'string' ? record.role : undefined,
    });
  }
  return candidates.length > 0 ? candidates : undefined;
}

function normalizeResolvedTargets(value: unknown): BcsRouteResolvedTarget[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const resolved: BcsRouteResolvedTarget[] = [];
  for (const target of value) {
    if (!target || typeof target !== 'object') continue;
    const record = target as Record<string, unknown>;
    if (record.type !== 'bot' || typeof record.value !== 'string' || !record.value) continue;
    resolved.push({
      type: 'bot',
      value: record.value,
      bot_name: typeof record.bot_name === 'string' ? record.bot_name : undefined,
      role: typeof record.role === 'string' ? record.role : undefined,
    });
  }
  return resolved.length > 0 ? resolved : undefined;
}

async function resolveBcsRouteSelectors(
  sessionKey: string,
  selectors: RouteSelectorWire[],
  log?: { warn: (...args: unknown[]) => void },
): Promise<BcsRouteToolResult> {
  const client = sessionKeyToClient.get(sessionKey);
  if (!client) {
    return {
      ok: false,
      error: 'BCS_UNAVAILABLE',
      message: 'BCS route validation is unavailable for this session',
      display_to_user: false,
    };
  }

  const rawGroupId = sessionTaskGroupInfo.get(sessionKey)?.groupId ?? sessionKeyToGroupId.get(sessionKey);
  const routeScope = sessionKeyToRouteScope.get(sessionKey)
    ?? (rawGroupId ? resolveBcsRouteScope(rawGroupId) : undefined);
  const groupId = routeScope?.groupId;
  if (!groupId) {
    return {
      ok: false,
      error: 'NO_GROUP_CONTEXT',
      message: 'BCS group context is unavailable for this session',
      display_to_user: false,
    };
  }

  const requestParams: Record<string, unknown> = {
    group_id: groupId,
    selectors,
  };
  const sessionId = routeScope?.sessionId ?? parseSessionScopedGroupId(sessionKeyToBcsSessionId.get(sessionKey))?.sessionId;
  if (sessionId) {
    requestParams.session_id = sessionId;
  }

  try {
    const response = await client.sendRequest('route.resolve', requestParams, 10_000);
    if (!response.ok) {
      return {
        ok: false,
        error: response.error?.code ?? 'ROUTE_RESOLVE_FAILED',
        message: response.error?.message ?? 'BCS failed to resolve route targets',
        display_to_user: false,
      };
    }

    const payload = response.payload ?? {};
    if (payload.ok === false) {
      return {
        ok: false,
        error: typeof payload.error === 'string' ? payload.error : 'ROUTE_TARGET_INVALID',
        message: typeof payload.message === 'string' ? payload.message : 'BCS could not resolve route targets',
        display_to_user: false,
        candidates: normalizeRouteCandidates(payload.candidates),
      };
    }

    const resolved = normalizeResolvedTargets(payload.resolved);
    if (payload.ok !== true || !resolved) {
      return {
        ok: false,
        error: 'INVALID_ROUTE_RESOLVE_RESPONSE',
        message: 'BCS returned an invalid route.resolve response',
        display_to_user: false,
      };
    }

    return { ok: true, display_to_user: false, resolved };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    log?.warn?.(`[BCS] route.resolve failed for sessionKey=${sessionKey}: ${message}`);
    return {
      ok: false,
      error: 'ROUTE_RESOLVE_FAILED',
      message,
      display_to_user: false,
    };
  }
}

function buildRouteIntent(routeParams: BcsRouteParams, responders: RouteSelectorWire[]): PendingRouteIntent {
  return {
    responders: responders.slice(0, MAX_ROUTE_RESPONDERS),
    mode: 'required',
    reason: routeParams.reason.slice(0, MAX_ROUTE_REASON_LENGTH),
    includeSelf: false,
    dedupeKey: undefined,
  };
}

function routeSelectorKey(selector: RouteSelectorWire): string {
  return `${selector.type}:${selector.value ?? ''}`;
}

function mergeRouteIntent(existing: PendingRouteIntent, intent: PendingRouteIntent): void {
  const existingKeys = new Set(existing.responders.map(routeSelectorKey));
  existing.responders = existing.responders.slice(0, MAX_ROUTE_RESPONDERS);
  for (const responder of intent.responders) {
    if (existing.responders.length >= MAX_ROUTE_RESPONDERS) break;
    const key = routeSelectorKey(responder);
    if (existingKeys.has(key)) continue;
    existing.responders.push(responder);
    existingKeys.add(key);
  }

  existing.reason = `${existing.reason}; ${intent.reason}`.slice(0, MAX_ROUTE_REASON_LENGTH);
}

/**
 * Handle a bcs_route tool call from the LLM.
 *
 * Validates selectors against BCS, stores the canonical routing intent in
 * pendingRouteByRunId, and returns BCS candidates when the target is unknown.
 */
export async function handleBcsRouteTool(
  runId: string,
  sessionKey: string,
  params: Record<string, unknown>,
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void },
): Promise<BcsRouteToolResult> {
  const routeParams = parseBcsRouteParams(params);
  if (isBcsRouteToolResult(routeParams)) return routeParams;

  const routeResult = await resolveBcsRouteSelectors(sessionKey, routeParams.to, log);
  if (!routeResult.ok) return routeResult;

  const intent = buildRouteIntent(routeParams, routeResult.resolved ?? []);

  // Accumulate: merge with any previous intent for this run
  const existing = pendingRouteByRunId.get(runId);
  if (existing) {
    mergeRouteIntent(existing, intent);
    log?.info?.(`[BCS] bcs_route merged for run_id=${runId}: ${JSON.stringify(existing)}`);
  } else {
    pendingRouteByRunId.set(runId, intent);
    log?.info?.(`[BCS] bcs_route captured for run_id=${runId}: ${JSON.stringify(intent)}`);
  }

  return { ok: true, captured: true, display_to_user: false, resolved: routeResult.resolved };
}

/**
 * Handle a bcs_route tool call by session key (fallback when run_id is not tracked).
 *
 * Same validation as handleBcsRouteTool, but stores intent by sessionKey.
 */
export async function handleBcsRouteToolBySession(
  sessionKey: string,
  params: Record<string, unknown>,
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void },
): Promise<BcsRouteToolResult> {
  const routeParams = parseBcsRouteParams(params);
  if (isBcsRouteToolResult(routeParams)) return routeParams;

  const routeResult = await resolveBcsRouteSelectors(sessionKey, routeParams.to, log);
  if (!routeResult.ok) return routeResult;

  const intent = buildRouteIntent(routeParams, routeResult.resolved ?? []);

  const existing = pendingRouteBySessionKey.get(sessionKey);
  if (existing) {
    mergeRouteIntent(existing, intent);
    log?.info?.(`[BCS] bcs_route merged (session-level) for sessionKey=${sessionKey}: ${JSON.stringify(existing)}`);
  } else {
    pendingRouteBySessionKey.set(sessionKey, intent);
    log?.info?.(`[BCS] bcs_route captured (session-level) for sessionKey=${sessionKey}: ${JSON.stringify(intent)}`);
  }

  return { ok: true, captured: true, display_to_user: false, resolved: routeResult.resolved };
}
type SdkAgentEventPayload = {
  runId: string;
  seq: number;
  stream: 'lifecycle' | 'tool' | 'assistant' | 'error' | string;
  ts: number;
  data: Record<string, unknown>;
  sessionKey?: string;
};

const TERMINAL_LIFECYCLE_VALUES = new Set([
  'end',
  'done',
  'complete',
  'completed',
  'final',
  'finished',
  'success',
  'succeeded',
]);

const ERROR_LIFECYCLE_VALUES = new Set([
  'abort',
  'aborted',
  'cancel',
  'cancelled',
  'canceled',
  'error',
  'fail',
  'failed',
]);

function terminalLifecycleOutcome(evt: SdkAgentEventPayload): 'final' | 'error' | undefined {
  if (evt.stream !== 'lifecycle') return undefined;

  const value =
    stringField(evt.data, 'phase') ??
    stringField(evt.data, 'status') ??
    stringField(evt.data, 'state') ??
    stringField(evt.data, 'type');

  if (!value) return undefined;
  const normalized = value.toLowerCase();
  if (TERMINAL_LIFECYCLE_VALUES.has(normalized)) return 'final';
  if (ERROR_LIFECYCLE_VALUES.has(normalized)) return 'error';
  return undefined;
}

/** Unsubscribe function for agent events */
let agentEventUnsubscribe: (() => boolean) | null = null;

/**
 * Subscribe to OpenClaw agent events and forward to BCS.
 * Should be called once during plugin initialization.
 */
export function initAgentEventsSubscription(log?: {
  info: (...args: unknown[]) => void;
  warn: (...args: unknown[]) => void;
  error: (...args: unknown[]) => void;
}): void {
  const rt = getBcsRuntime();

  // Subscribe to agent events from OpenClaw
  agentEventUnsubscribe = rt.events.onAgentEvent((evt: SdkAgentEventPayload) => {
    // log?.debug?.(`[BCS] onAgentEvent RAW: runId=${evt.runId}, stream=${evt.stream}, seq=${evt.seq}`);

    // onAgentRunStart binds queued OpenClaw run IDs to the BCS-visible run ID.
    // Direct runs normally keep the requested ID and can be bound lazily here.
    const resolvedRunId = runContexts.has(evt.runId)
      ? evt.runId
      : bcsRunIdByAgentRunId.get(evt.runId);
    if (resolvedRunId && evt.runId === resolvedRunId) {
      bindAgentRun(resolvedRunId, evt.runId, log);
    }
    const context = resolvedRunId ? runContexts.get(resolvedRunId) : undefined;

    if (!resolvedRunId || !context) {
      // This event is not for a BCS-managed run, skip it
      log?.warn?.(`[BCS] No run context for runId=${evt.runId}, skipping`);
      return true; // Indicate event was handled (skipped)
    }

    if (context.sessionKey && evt.sessionKey && context.sessionKey !== evt.sessionKey) {
      log?.warn?.(`[BCS] Agent event session mismatch for runId=${evt.runId}: expected=${context.sessionKey}, received=${evt.sessionKey}`);
      return true;
    }

    const { groupId, client } = context;
    const terminalOutcome = terminalLifecycleOutcome(evt);
    if (evt.stream === 'tool') {
      context.sawToolEvent = true;
    }

    if (!context.finalSent) {
      if (evt.stream === 'assistant') {
        recordAssistantAgentText(resolvedRunId, evt.data);
      } else {
        sendVisibleReplyDelta(resolvedRunId, log);
        markVisibleReplySegmentBoundary(resolvedRunId);
      }

      if (terminalOutcome === 'final') {
        sendFinalVisibleReplyOnce(resolvedRunId, log, { source: 'agent_lifecycle' });
      } else if (terminalOutcome === 'error') {
        sendRunErrorOnce(
          resolvedRunId,
          'Agent run failed before completing a reply.',
          log,
          `agent lifecycle ${stringField(evt.data, 'phase') ?? 'error'}`,
        );
      }
    }

    // Build the agent event payload for BCS — always use the original runId
    // so that all events for one user message share the same run_id
    const agentPayload: AgentEventPayload = {
      run_id: resolvedRunId,
      bcs_group_id: groupId,
      stream: evt.stream as any,
      ts: evt.ts,
      data: evt.data,
    };

    // Forward to BCS
    client.sendEvent('agent', agentPayload as unknown as Record<string, unknown>, nextSeq(client));

    log?.info?.(
      `[BCS] Forwarded agent event: runId=${evt.runId}, stream=${evt.stream}, groupId=${groupId}`,
    );
    if (terminalOutcome) {
      void cleanupRunContext(resolvedRunId, log);
    }
    return true; // Indicate event was handled
  }) as (() => boolean) | null;

  log?.info?.('[BCS] Agent events subscription initialized');
}

/**
 * Unsubscribe from OpenClaw agent events.
 * Should be called during plugin shutdown.
 */
export function cleanupAgentEventsSubscription(): void {
  if (agentEventUnsubscribe) {
    agentEventUnsubscribe();
    agentEventUnsubscribe = null;
  }
}

// ---------------------------------------------------------------------------
// bcs_assign_task tool (Task Group)
// ---------------------------------------------------------------------------

export const BCS_ASSIGN_TASK_TOOL_SCHEMA = {
  name: 'bcs_assign_task',
  description:
    'Dispatch a task to a sub bot in this task group. Returns immediately — ' +
    "the sub bot's response will arrive as a follow-up message. " +
    'You can dispatch multiple sub bots in parallel.',
  parameters: {
    type: 'object' as const,
    properties: {
      target_bot: {
        type: 'string' as const,
        description: "Target bot name (e.g. 'DBA') or ID (e.g. 'bot_abc123'). Use one or the other, NOT the combined 'name(id)' format.",
      },
      message: {
        type: 'string' as const,
        description: 'The task description / instruction to send to the sub bot.',
      },
      response_mode: {
        type: 'string' as const,
        enum: [ 'after-last-tool-call', 'full' ],
        description:
          "Controls how the sub bot's task result is returned to the manager. " +
          "Use 'after-last-tool-call' for the final answer after tool calls (default), or 'full' for the full response text.",
      },
    },
    required: [ 'target_bot', 'message' ],
  },
};

export async function handleAssignTask(
  sessionKey: string,
  params: Record<string, unknown>,
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void; error: (...args: unknown[]) => void },
): Promise<{ ok: boolean; task_id?: string; status?: string; error?: string }> {
  const client = sessionKeyToClient.get(sessionKey);
  if (!client?.connected) {
    return { ok: false, error: 'BCS WebSocket not connected' };
  }

  const taskInfo = sessionTaskGroupInfo.get(sessionKey);
  const groupId = taskInfo?.groupId ?? sessionKeyToGroupId.get(sessionKey);
  if (!groupId) {
    return { ok: false, error: 'No group context for this session' };
  }

  const targetBot = params.target_bot as string;
  const message = params.message as string;
  const responseMode = params.response_mode as TaskDispatchParams['response_mode'] | undefined;

  if (!targetBot || !message) {
    return { ok: false, error: "'target_bot' and 'message' are required" };
  }

  log?.info?.(`[bcs_assign_task] Dispatching to ${targetBot} in group ${groupId}`);

  try {
    const response = await client.sendRequest(
      'task.dispatch',
      {
        group_id: groupId,
        target_bot: targetBot,
        message,
        ...(responseMode ? { response_mode: responseMode } : {}),
      },
      30_000,
    );

    if (!response.ok) {
      const errMsg = response.error?.message ?? 'task.dispatch failed';
      log?.warn?.(`[bcs_assign_task] Failed: ${errMsg}`);
      return { ok: false, error: errMsg };
    }

    const payload = response.payload as unknown as TaskDispatchResponse;
    log?.info?.(`[bcs_assign_task] Dispatched: task_id=${payload.task_id}`);

    return {
      ok: true,
      task_id: payload.task_id,
      status: payload.status ?? 'dispatched',
    };
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    log?.error?.(`[bcs_assign_task] Error: ${errMsg}`);
    return { ok: false, error: errMsg };
  }
}

// ---------------------------------------------------------------------------
// bcs_send_task_message tool (Task Group)
// ---------------------------------------------------------------------------

export const BCS_TASK_MESSAGE_TOOL_SCHEMA = {
  name: 'bcs_send_task_message',
  description:
    'Send a task-scoped message from this worker bot to the manager bot. ' +
    'Use this for progress updates, blockers, intermediate findings, or supplemental information.',
  parameters: {
    type: 'object' as const,
    properties: {
      message: {
        type: 'string' as const,
        description: 'The task-scoped message to send to the manager bot.',
      },
    },
    required: [ 'message' ],
  },
};

export async function handleTaskMessage(
  sessionKey: string,
  params: Record<string, unknown>,
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void; error: (...args: unknown[]) => void },
): Promise<{ ok: boolean; status?: string; error?: string }> {
  const client = sessionKeyToClient.get(sessionKey);
  if (!client?.connected) {
    return { ok: false, error: 'BCS WebSocket not connected' };
  }

  const taskInfo = sessionTaskGroupInfo.get(sessionKey);
  const groupId = taskInfo?.groupId ?? sessionKeyToGroupId.get(sessionKey);
  if (!groupId) {
    return { ok: false, error: 'No group context for this session' };
  }

  const message = params.message as string;
  if (!message || typeof message !== 'string' || message.trim() === '') {
    return { ok: false, error: "'message' is required and must be non-empty" };
  }

  log?.info?.(`[bcs_send_task_message] Sending task message in group ${groupId}`);

  try {
    const response = await client.sendRequest(
      'task.message',
      {
        group_id: groupId,
        message: message.trim(),
      },
      30_000,
    );

    if (!response.ok) {
      const errMsg = response.error?.message ?? 'task.message failed';
      log?.warn?.(`[bcs_send_task_message] Failed: ${errMsg}`);
      return { ok: false, error: errMsg };
    }

    const payload = response.payload as unknown as TaskMessageResponse;
    return {
      ok: true,
      status: payload.status ?? 'sent',
    };
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    log?.error?.(`[bcs_send_task_message] Error: ${errMsg}`);
    return { ok: false, error: errMsg };
  }
}

// ---------------------------------------------------------------------------
// bcs_task_complete tool (Task Group)
// ---------------------------------------------------------------------------

export const BCS_TASK_COMPLETE_TOOL_SCHEMA = {
  name: 'bcs_task_complete',
  description:
    "Signal that the task group's work is FULLY done. " +
    'IMPORTANT: Only call this AFTER you have received replies from ALL sub bots ' +
    'and completed your final analysis. Never call before receiving sub bot replies. ' +
    'Provide a comprehensive summary of all results.',
  parameters: {
    type: 'object' as const,
    properties: {
      summary: {
        type: 'string' as const,
        description: "Final summary of the task group's work and results.",
      },
    },
    required: [ 'summary' ],
  },
};

export async function handleTaskComplete(
  sessionKey: string,
  params: Record<string, unknown>,
  log?: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void; error: (...args: unknown[]) => void },
): Promise<{ ok: boolean; error?: string }> {
  const client = sessionKeyToClient.get(sessionKey);
  if (!client?.connected) {
    return { ok: false, error: 'BCS WebSocket not connected' };
  }

  const taskInfo = sessionTaskGroupInfo.get(sessionKey);
  const groupId = taskInfo?.groupId ?? sessionKeyToGroupId.get(sessionKey);
  if (!groupId) {
    return { ok: false, error: 'No group context for this session' };
  }

  const summary = params.summary as string;
  if (!summary || typeof summary !== 'string' || summary.trim() === '') {
    return { ok: false, error: "'summary' is required and must be non-empty" };
  }

  log?.info?.(`[bcs_task_complete] Completing task group ${groupId}`);

  try {
    const response = await client.sendRequest(
      'task.complete',
      {
        group_id: groupId,
        summary: summary.trim(),
      },
      30_000,
    );

    if (!response.ok) {
      const errMsg = response.error?.message ?? 'task.complete failed';
      log?.warn?.(`[bcs_task_complete] Failed: ${errMsg}`);
      return { ok: false, error: errMsg };
    }

    log?.info?.(`[bcs_task_complete] Task group ${groupId} completed`);
    return { ok: true };
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    log?.error?.(`[bcs_task_complete] Error: ${errMsg}`);
    return { ok: false, error: errMsg };
  }
}
