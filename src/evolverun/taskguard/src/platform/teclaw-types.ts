/**
 * TeClaw type definitions — Channel 2 types.
 *
 * Contains types used by both the legacy HTTP API and the new WebSocket
 * protocol (/ws/v1/chat). HTTP-specific types are kept for backward
 * compatibility but are deprecated — prefer the WebSocket types from
 * teclaw-ws-types.ts.
 *
 * Still-active types:
 * - TeClawChatInjectRequest  — request body for HTTP chat/inject (used by mcp-chat-inject)
 * - TeClawChatInjectResponse — response from chat/inject (used by TeClawProvider.chatInjectWS)
 * - TeClawCancelJobResponse  — cancel response shape
 *
 * Deprecated (HTTP-only, kept for backward compat):
 * - TeClawProviderConfig / validateTeClawProviderConfig — replaced by TeClawWsProviderConfig
 * - TeClawThreadNewResponse / TeClawChatSendResponse — replaced by WS res frames
 * - TeClawEventSourceType / TeClawEventMessage / TeClawLoopCompleteEvent — replaced by WS event frames
 *
 * @module platform/teclaw-types
 */

// ── Re-export WS Config (preferred) ──

export type { TeClawWsProviderConfig } from "./teclaw-ws-types.js";

// ── TeClawProvider Configuration (DEPRECATED: HTTP-only) ──

/**
 * Configuration for TeClawProvider (Channel 2 HTTP API).
 *
 * @deprecated Use TeClawWsProviderConfig from teclaw-ws-types.ts instead.
 * The HTTP API is being replaced by the WebSocket protocol (/ws/v1/chat).
 */
export interface TeClawProviderConfig {
  /** @deprecated Use TeClawWsProviderConfig.wsUrl */
  baseUrl: string;
  /** @deprecated Use TeClawWsProviderConfig.token */
  apiKey: string;
  /** @deprecated Use TeClawWsProviderConfig (WS chat.inject replaces HTTP) */
  chatInjectUrl?: string;
}

/** @deprecated Use TeClawWsProviderConfig from teclaw-ws-types.ts instead. */
export interface TeClawProviderConfigValidation {
  valid: boolean;
  chatInjectUrl: string;
  error?: string;
}

/** @deprecated Use TeClawWsProviderConfig directly — validation is done in TeClawProvider constructor. */
export function validateTeClawProviderConfig(config: TeClawProviderConfig): TeClawProviderConfigValidation {
  if (!config.baseUrl) {
    return { valid: false, chatInjectUrl: "", error: "baseUrl is required" };
  }
  const chatInjectUrl = config.chatInjectUrl ?? `${config.baseUrl.replace(/\/+$/, "")}/api/chat/inject`;
  return { valid: true, chatInjectUrl };
}

// ── Agent Loop API Types (DEPRECATED: HTTP-only) ──

/** @deprecated Agent loop params are now passed directly to TeClawProvider.runAgentLoop(). */
export interface TeClawAgentLoopParams {
  prompt: string;
  systemPrompt?: string;
  maxTurns?: number;
  maxTokens?: number;
  allowedTools?: string[];
  workflowContext?: {
    flowId?: string;
    nodeId?: string;
    workflowId?: string;
    nodeOutputs?: Record<string, string>;
    params?: Record<string, unknown>;
  };
}

/** @deprecated Thread creation is handled internally by the WS protocol via sessionKey. */
export interface TeClawThreadNewResponse {
  threadId: string;
}

/** @deprecated Replaced by WS res frame for chat.send (status: accepted/queued). */
export interface TeClawChatSendResponse {
  jobId?: string;
  status: "accepted" | "queued";
}

// ── SSE Event Types (DEPRECATED: replaced by WS event frames) ──

/**
 * @deprecated Replaced by WS event frames (chat delta/final/error/aborted).
 * See ChatEventPayload in teclaw-ws-types.ts.
 */
export enum TeClawEventSourceType {
  TextDelta = "text_delta",
  ToolCall = "tool_use",
  ToolResult = "tool_result",
  LoopComplete = "loop_complete",
  AgentMessage = "agent_message",
  Error = "error",
}

/** @deprecated Replaced by WS event frames. See WsEventFrame in teclaw-ws-types.ts. */
export interface TeClawEventMessage {
  source: TeClawEventSourceType;
  data: Record<string, unknown>;
}

/** @deprecated Replaced by ChatEventPayload (state: "final") in teclaw-ws-types.ts. */
export interface TeClawLoopCompleteEvent {
  output?: string;
  error?: string;
  payloads?: Array<{ text?: string; isError?: boolean; isReasoning?: boolean; turn?: number }>;
  messagingToolSentTexts?: string[];
  meta?: {
    model?: string;
    stopReason?: string;
    totalTurns?: number;
    totalTokens?: { input?: number; output?: number };
  };
}

// ── Event Type Guards (DEPRECATED) ──

/** @deprecated Use isChatFinalEvent from teclaw-ws-types.ts instead. */
export function isLoopCompleteEvent(event: TeClawEventMessage): event is TeClawEventMessage & { data: TeClawLoopCompleteEvent } {
  return event.source === TeClawEventSourceType.LoopComplete;
}

/** @deprecated Use isChatDeltaEvent from teclaw-ws-types.ts instead. */
export function isTextDeltaEvent(event: TeClawEventMessage): event is TeClawEventMessage & { data: { text: string; turn: number } } {
  return event.source === TeClawEventSourceType.TextDelta;
}

/** @deprecated No direct WS equivalent (tool events are internal to the agent). */
export function isToolCallEvent(event: TeClawEventMessage): event is TeClawEventMessage & { data: { toolName: string; toolCallId: string; args: Record<string, unknown> } } {
  return event.source === TeClawEventSourceType.ToolCall;
}

/** @deprecated No direct WS equivalent (tool events are internal to the agent). */
export function isToolResultEvent(event: TeClawEventMessage): event is TeClawEventMessage & { data: { toolCallId: string; result: unknown } } {
  return event.source === TeClawEventSourceType.ToolResult;
}

// ── Chat Inject API Types (STILL ACTIVE) ──

/** Request body for POST /api/chat/inject (HTTP chatInject endpoint). */
export interface TeClawChatInjectRequest {
  /** Message type: progress, info, error, or approval. */
  messageType: "progress" | "info" | "error" | "approval";
  /** Flow ID for tracking. */
  flowId?: string;
  /** Node ID that produced this message. */
  nodeId?: string;
  /** Workflow ID for context. */
  workflowId?: string;
  /** Human-readable message text. */
  message: string;
  /** Idempotency key to prevent duplicate delivery. */
  idempotencyKey?: string;
  /** Actions for approval-type messages (confirm/reject buttons). */
  actions?: Array<{ label: string; action: string; style: string }>;
  /** Additional metadata. */
  metadata?: Record<string, unknown>;
  /** ISO 8601 timestamp. */
  timestamp?: string;
}

/** Structured context appended to a WebSocket chat.inject request. */
export type TeClawChatInjectContext = Omit<
  TeClawChatInjectRequest,
  "message"
>;

/** Response from chat/inject (both HTTP and WebSocket chat.inject). */
export interface TeClawChatInjectResponse {
  /** "delivered" for progress/error/info; "pending" for approval. */
  status: "delivered" | "pending";
  /** Delivery channel. */
  channel?: "dingtalk" | "web" | "repl";
  /** Approval ID (only for approval type). */
  approvalId?: string;
  /** Timeout in seconds (only for approval type). */
  timeout?: number;
}

// ── Cancel API (STILL ACTIVE shape) ──

/** Response shape for job cancellation. */
export interface TeClawCancelJobResponse {
  status: "cancelled" | "not_found";
}
