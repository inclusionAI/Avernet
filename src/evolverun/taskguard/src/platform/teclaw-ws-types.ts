/**
 * TeClaw WebSocket Frame Protocol Types — /ws/v1/chat Channel 2.
 *
 * Defines the frame types, payloads, and type guards for the TeClaw
 * WebSocket protocol (version 3) used by TeClawProvider for Agent Loop
 * execution, chat/inject, abort, and approval resolution.
 *
 * Protocol overview:
 * - req:  client → server  (method call with id)
 * - res:  server → client  (response to a req, same id)
 * - event: server → client (unsolicited: chat delta/final/error, tick, approval)
 *
 * @module platform/teclaw-ws-types
 */

// ── Base Frame Types ──

/** Base shape for all WebSocket frames. */
export interface WsFrame {
  /** Frame type discriminator. */
  type: "req" | "res" | "event";
}

/** Client → Server request frame. */
export interface WsReqFrame extends WsFrame {
  type: "req";
  /** Unique request ID for correlating with res frames. */
  id: string;
  /** Method name (e.g., "chat.send", "chat.inject"). */
  method: string;
  /** Method-specific parameters. Per WS API v2 spec, field is "params" not "payload". */
  params: Record<string, unknown>;
}

/** Error shape for failed response frames. Per WS API v2 spec. */
export interface WsErrorShape {
  /** Error code (e.g., "INVALID_REQUEST", "METHOD_NOT_SUPPORTED"). */
  code: string;
  /** Human-readable error description. */
  message: string;
  /** Additional details. */
  details?: unknown;
  /** Whether the request can be retried. */
  retryable?: boolean;
  /** Suggested retry wait time in milliseconds. */
  retryAfterMs?: number;
}

/** Server → Client response frame. Per WS API v2 spec. */
export interface WsResFrame extends WsFrame {
  type: "res";
  /** Matches the req frame's id. */
  id: string;
  /** Whether the request succeeded. */
  ok: boolean;
  /** Response payload (present when ok=true). */
  payload?: Record<string, unknown>;
  /** Error details (present when ok=false). */
  error?: WsErrorShape;
}

/** State version counters carried by event frames per WS API v2 spec. */
export interface WsStateVersion {
  /** Monotonically increasing presence state version. */
  presence?: number;
  /** Monotonically increasing health state version. */
  health?: number;
}

/** Server → Client event frame (unsolicited). Per WS API v2 spec. */
export interface WsEventFrame extends WsFrame {
  type: "event";
  /** Event name (e.g., "chat", "tick", "approval.requested"). */
  event: string;
  /** Event-specific data. */
  payload: Record<string, unknown>;
  /** Monotonically increasing sequence number. */
  seq?: number;
  /** State version counters for presence/health tracking. */
  stateVersion?: WsStateVersion;
}

// ── Frame Type Guards ──

export function isWsReqFrame(frame: WsFrame): frame is WsReqFrame {
  return frame.type === "req" && typeof (frame as WsReqFrame).method === "string";
}

export function isWsResFrame(frame: WsFrame): frame is WsResFrame {
  return frame.type === "res";
}

export function isWsEventFrame(frame: WsFrame): frame is WsEventFrame {
  return frame.type === "event" && typeof (frame as WsEventFrame).event === "string";
}

// ── Connect Handshake ──

/** Client identification within the connect handshake. Per WS API v2 spec. */
export interface ConnectClientInfo {
  /** Unique client instance identifier (e.g., "clawmind-instance-001"). */
  id: string;
  /** Client version string (e.g., "0.1.0"). */
  version: string;
  /** Platform identifier (e.g., "openclaw", "clawmind"). */
  platform: string;
  /** Client mode (e.g., "agent-loop", "chat"). */
  mode: string;
}

/** Connect payload sent as the first req after WS connection. */
export interface ConnectPayload {
  /** Minimum protocol version the client supports. Must be ≤ 3. */
  minProtocol: number;
  /** Maximum protocol version the client supports. Must be ≥ 3. */
  maxProtocol: number;
  /** Client identification. Per WS API v2 spec: id, version, platform, mode. */
  client: ConnectClientInfo;
  /** Client role (e.g., "agent-loop", "workflow-engine"). */
  role?: string;
  /** Authentication. */
  auth: { token: string };
}

/** Server response to a successful connect. Per WS API v2 spec. */
export interface HelloOkPayload {
  /** Discriminator: always "hello-ok" for a successful connect response. */
  type: "hello-ok";
  /** Negotiated protocol version. */
  protocol: number;
  /** Server identification. */
  server: { name: string; version: string };
  /** Available features. */
  features: {
    /** Methods the server supports. */
    methods: string[];
    /** Events the server may emit. */
    events: string[];
  };
  /** Server policy. */
  policy: {
    /** Maximum payload size in bytes. */
    maxPayload: number;
    /** Tick interval in milliseconds. */
    tickIntervalMs: number;
  };
  /** Authentication context returned by the server. */
  auth?: {
    /** Authenticated identity. */
    identity?: string;
    /** Token expiry (epoch ms). */
    expiresAt?: number;
  };
  /** Optional initial state snapshot. */
  snapshot?: Record<string, unknown>;
}

/** Validation result for ConnectPayload. */
export interface ConnectPayloadValidation {
  valid: boolean;
  error?: string;
}

/** Validate a ConnectPayload before sending. */
export function validateConnectPayload(payload: ConnectPayload): ConnectPayloadValidation {
  if (!payload.auth?.token) {
    return { valid: false, error: "auth.token is required" };
  }
  if (payload.minProtocol > 3 || payload.maxProtocol < 3) {
    return { valid: false, error: "protocol version 3 must be within [minProtocol, maxProtocol] range" };
  }
  if (!payload.client?.id) {
    return { valid: false, error: "client.id is required" };
  }
  if (!payload.client?.platform) {
    return { valid: false, error: "client.platform is required" };
  }
  return { valid: true };
}

// ── chat.send Method ──

/** Payload for chat.send req frame. */
export interface ChatSendPayload {
  /** Session key for the conversation. */
  sessionKey: string;
  /** The message to send to the agent. */
  message: string;
  /** Optional maximum agent loop turns. */
  maxTurns?: number;
  /** Optional maximum tokens per LLM call. */
  maxTokens?: number;
  /** Optional allowlist of tool names. */
  allowedTools?: string[];
  /** Optional system prompt override. */
  systemPrompt?: string;
}

// ── chat.inject Method ──

/** Payload for chat.inject req frame. */
export interface ChatInjectWsPayload {
  /** Session key for the conversation. */
  sessionKey: string;
  /** The message to inject. */
  message: string;
  /** Human-readable label for the injected message. */
  label: string;
}

// ── chat.abort Method ──

/** Payload for chat.abort req frame. */
export interface ChatAbortPayload {
  /** Session key for the conversation. */
  sessionKey: string;
  /** Run ID to abort. */
  runId: string;
}

// ── exec.approval.resolve Method ──

/** Payload for exec.approval.resolve req frame. */
export interface ApprovalResolvePayload {
  /** The approval ID to resolve. */
  approvalId: string;
  /** The resolution action (e.g., "approve", "reject"). */
  action: string;
  /** Optional comment. */
  comment?: string;
}

// ── Chat Event States ──

/** Chat event states from the server. */
export type ChatEventState = "delta" | "final" | "error" | "aborted" | "thinking" | "status";

/**
 * Payload for a "chat" event frame.
 *
 * Field names match TeClaw's OpenAPI WS `convert_app_event()` wire format
 * (handler.rs).  The server emits these JSON structures:
 *
 * | state    | field containing text                              |
 * |----------|---------------------------------------------------|
 * | delta    | `text` — streaming token                          |
 * | final    | `message.content[0].text` — complete response     |
 * | error    | `errorMessage` — error description                |
 * | thinking | `text` — reasoning/thinking status                |
 * | status   | `text` — generic status message                   |
 */
export interface ChatEventPayload {
  /** Chat state: delta, final, error, aborted, thinking, or status. */
  state: ChatEventState;
  /** True only when this final frame completes the originating Agent Loop turn. */
  completed?: boolean;
  /**
   * Streaming text for delta/thinking/status states (TeClaw wire field).
   * NOT present on final events — use `message` instead.
   */
  text?: string;
  /**
   * Structured response for final state (TeClaw wire format).
   * Contains the full assistant message with content blocks.
   */
  message?: {
    role: string;
    content: Array<{ type: string; text?: string }>;
    timestamp?: number;
  };
  /** Error message for error state (TeClaw wire field: "errorMessage"). */
  errorMessage?: string;
  /** Session key injected by TeClaw server for event routing in multi-session scenarios. */
  sessionKey?: string;
  /** Run ID of the current agent loop. */
  runId?: string;
  /** Sequence number injected by TeClaw server. */
  seq?: number;
  /** Server timestamp injected by TeClaw server. */
  ts?: number;
  /**
   * @deprecated Use `text` for delta/thinking/status, `message.content[0].text` for final.
   * Kept for backward compat with old TeClaw versions that may send `content` directly.
   */
  content?: string;
  /**
   * @deprecated Use `errorMessage` for error state.
   * Kept for backward compat with old TeClaw versions.
   */
  error?: string;
  /** Payloads from the agent loop (for final state). */
  payloads?: Array<{ text?: string; isError?: boolean; isReasoning?: boolean; turn?: number }>;
  /** Messaging tool sent texts (for final state). */
  messagingToolSentTexts?: string[];
  /** Metadata from the agent loop (for final state). */
  meta?: Record<string, unknown>;
}

/** Payload for a "tick" event frame. */
export interface TickEventPayload {
  /** Server timestamp. */
  ts?: number;
}

/** Payload for an "approval.requested" event frame. */
export interface ApprovalRequestedEventPayload {
  /** Unique approval ID. */
  approvalId: string;
  /** Description of the approval request. */
  description?: string;
  /** Available actions. */
  actions?: Array<{ label: string; action: string }>;
  /** Timeout in seconds. */
  timeout?: number;
}

/** Payload for an "approval.resolved" event frame. */
export interface ApprovalResolvedEventPayload {
  /** The approval ID that was resolved. */
  approvalId: string;
  /** The resolution action taken. */
  action: string;
}

// ── Chat Event Type Guards ──

export function isChatFinalEvent(frame: WsEventFrame): frame is WsEventFrame & { payload: ChatEventPayload } {
  return frame.event === "chat" && (frame.payload as Record<string, unknown>).state === "final";
}

export function isChatDeltaEvent(frame: WsEventFrame): frame is WsEventFrame & { payload: ChatEventPayload } {
  return frame.event === "chat" && (frame.payload as Record<string, unknown>).state === "delta";
}

export function isChatErrorEvent(frame: WsEventFrame): frame is WsEventFrame & { payload: ChatEventPayload } {
  return frame.event === "chat" && (frame.payload as Record<string, unknown>).state === "error";
}

export function isChatAbortedEvent(frame: WsEventFrame): frame is WsEventFrame & { payload: ChatEventPayload } {
  return frame.event === "chat" && (frame.payload as Record<string, unknown>).state === "aborted";
}

export function isApprovalRequestedEvent(frame: WsEventFrame): frame is WsEventFrame & { payload: ApprovalRequestedEventPayload } {
  return frame.event === "approval.requested";
}

export function isTickEvent(frame: WsEventFrame): frame is WsEventFrame & { payload: TickEventPayload } {
  return frame.event === "tick";
}

// ── WS Provider Config ──

/** Configuration for TeClawProvider WebSocket connection. */
export interface TeClawWsProviderConfig {
  /** WebSocket URL (e.g., "ws://127.0.0.1:13000/ws/v1/chat" or "wss://example.com/ws/v1/chat"). */
  wsUrl: string;
  /** MCP Token for authentication. */
  token: string;
  /** Additional HTTP headers for the WS handshake (e.g., x-andc-target-service). */
  headers?: Record<string, string>;
  /** Client identification. Per WS API v2 spec. Defaults to { id: "clawmind", version: "0.1.0", platform: "openclaw", mode: "agent-loop" }. */
  client?: ConnectClientInfo;
  /** Connect handshake timeout in ms. Default: 10000. */
  connectTimeoutMs?: number;
  /** Session key for chat.send/chat.inject. Defaults to a generated key. */
  sessionKey?: string;
  /** HTTP base URL for REST API calls (e.g., POST /api/v1/sessions).
   *  When empty, derived from wsUrl (ws:// → http://, wss:// → https://, strip path). */
  httpBaseUrl?: string;
}
