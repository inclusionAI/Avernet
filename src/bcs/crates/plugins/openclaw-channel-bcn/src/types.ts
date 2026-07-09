/**
 * BCS protocol types — ported from bcs-common/src/protocol.rs.
 * Plus plugin config types for the OpenClaw channel plugin.
 */

// ---------------------------------------------------------------------------
// Frame layer
// ---------------------------------------------------------------------------

export interface ErrorShape {
  code: string;
  message: string;
  details?: unknown;
  retryable: boolean;
  retry_after_ms?: number;
}

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
  error?: ErrorShape;
}

export interface EventFrame {
  type: 'event';
  event: string;
  payload: Record<string, unknown>;
  seq: number;
}

export type BcsFrame = RequestFrame | ResponseFrame | EventFrame;

// ---------------------------------------------------------------------------
// Bot connection & heartbeat (Bot → BCS)
// ---------------------------------------------------------------------------

export interface BotCapabilities {
  summary: string;
  domains: string[];
  skills: string[];
  scopes: string[];
}

/** Parameters for bot.connect - optional token for reconnection. */
export interface BotConnectParams {
  token?: string;
  /** Optional preconfigured bot_id. */
  bot_id?: string;
}

/** Response from bot.connect. */
export interface BotConnectResponse {
  is_new: boolean;
  bot_uuid: string;
  token: string;
}

/** Legacy alias for backwards compatibility. */
export interface BotRegisterParams {
  protocol_version: string;
  bot_id: string;
  bot_name: string;
  engine_type: string;
  capabilities: BotCapabilities;
}

/** Legacy alias for backwards compatibility. */
export interface BotRegisterResponse {
  session_token: string;
}

/** Session info for persistence (saved to .bcs/session.json). */
export interface SessionInfo {
  bot_uuid: string | null; // null for new bots awaiting onboarding
  token: string;
  bcs_url: string;
}

export type BotStatus = 'idle' | 'busy' | 'error';

export interface BotStatusParams {
  status: BotStatus;
  dynamic_summary?: string;
  load?: number;
}

// ---------------------------------------------------------------------------
// Message down-send (BCS → Bot)
// ---------------------------------------------------------------------------

export interface ContentBlock {
  type: 'text' | 'image';
  text?: string;
  url?: string;
}

export interface MessageContent {
  role: string;
  content: ContentBlock[];
  timestamp: number;
}

export type ChannelSource = 'webui' | 'dingtalk' | 'api';

export interface ChannelInfo {
  source: ChannelSource;
  user_id?: string;
  thread_id?: string;
}

export type SessionMode = 'agent' | 'fusion' | 'composite';

/** Response directive injected into GroupContext by BCS structured routing.
 *
 * Tells the receiving bot why it got the message and what action to take.
 * New bots should read this instead of `you_are_mentioned`.
 */
export interface ResponseDirective {
  /** What the bot should do: respond to the message or observe silently. */
  action: 'respond' | 'observe';
  /** Response mode (only meaningful when action=respond). */
  mode?: 'required' | 'optional';
  /** Routing reason from the requesting bot. */
  reason?: string;
  /** How this routing was triggered. */
  request_source: 'structured_metadata' | 'legacy_mention' | 'default_policy' | 'sender_routes';
  /** Which selector matched this bot (only for structured routing). */
  matched_by?: RouteSelectorWire;
}

/** Group context injected by BCS when routing messages to bots.
 *
 * This context helps bots understand:
 * - Which group they're in
 * - Who sent the message
 * - Whether they should respond (via response_directive or legacy you_are_mentioned)
 *
 * Priority: response_directive > you_are_mentioned > default policy.
 */
export interface GroupContext {
  session_id: string;
  participants: string[];
  originator: string;
  from: string;
  you_are_mentioned: boolean;
  is_sender: boolean;
  mentions: string[];
  message: string;
  /** Structured routing directive — takes priority over you_are_mentioned. */
  response_directive?: ResponseDirective;
  /** Group routing mode: "structured", "mention", or "hybrid". Absent for old BCS servers. */
  routing_mode?: string;
  /** Group type: "default" for normal groups, "task" for task groups. Absent for old BCS servers. */
  group_type?: string;
  /** Bot owner ID — the owner of the bot that sent this message. Absent for old BCS servers. */
  from_bot_owner?: string;
  /** Bot ID — the ID of the bot that sent this message. Absent for old BCS servers. */
  from_bot_id?: string;
  /** Recipient bot's role in this group: "manager" | "worker" | "driver" | "consultant" | "observer". */
  recipient_role?: string;
  /** Recipient bot's UUID (the bot this frame is addressed to). */
  recipient?: string;
}

/** Legacy alias for backwards compatibility. */
export interface SessionContext {
  mode: SessionMode;
  participants: string[];
  driver_bot_id?: string;
}

export interface ChatSendParams {
  session_key: string;
  bcs_group_id: string;
  bcs_session_id?: string;
  message: MessageContent;
  channel: ChannelInfo;
  session_context: GroupContext;
  timeout_ms?: number;
  idempotency_key?: string;
}

/** Parameters for chat.inject - incoming message that bot should observe silently.
 *
 * BCS sends chat.inject to bots that should observe but NOT respond.
 * The bot receives the message for context awareness only.
 */
export interface ChatInjectParams {
  session_key: string;
  bcs_group_id: string;
  bcs_session_id?: string;
  message: MessageContent;
  channel: ChannelInfo;
  session_context: GroupContext;
}

export interface ChatSendResponse {
  run_id: string;
}

/** Parameters for session.delete request (BCS → Bot). */
export interface SessionDeleteParams {
  /** BCS group ID being deleted. */
  bcs_group_id: string;
}

export interface ChatHistoryParams {
  session_key: string;
  limit?: number;
}

export interface ChatHistoryMessage {
  role: 'user' | 'assistant' | 'system';
  content: string | unknown[];
  timestamp: number;
}

export interface ChatHistoryResponse {
  session_key: string;
  session_id?: string;
  messages: ChatHistoryMessage[];
}

// ---------------------------------------------------------------------------
// Streaming events (Bot → BCS)
// ---------------------------------------------------------------------------

export type AgentStream = 'lifecycle' | 'assistant' | 'tool' | 'error';

export interface AgentEventPayload {
  run_id: string;
  bcs_group_id: string;
  stream: AgentStream;
  ts: number;
  data: unknown;
}

export type ChatEventState = 'delta' | 'final' | 'aborted' | 'error';

export interface UsageInfo {
  input: number;
  output: number;
}

/** Route selector for structured routing — corresponds to Rust RouteSelectorWire. */
export interface RouteSelectorWire {
  type: 'bot' | 'name' | 'role' | 'capability' | 'participants' | 'originator' | 'driver';
  value?: string;
}

/** Structured routing metadata attached to final chat events. */
export interface ChatEventRouting {
  responders: RouteSelectorWire[];
  mode?: 'required' | 'optional';
  reason: string;
  include_self?: boolean;
  dedupe_key?: string;
}

/** Pending route intent cached per run, populated by bcs_route tool. */
export interface PendingRouteIntent {
  responders: RouteSelectorWire[];
  mode: 'required' | 'optional';
  reason: string;
  includeSelf: boolean;
  dedupeKey?: string;
}

export interface ChatEventPayload {
  run_id: string;
  bcs_group_id: string;
  state: ChatEventState;
  message?: MessageContent;
  usage?: UsageInfo;
  stop_reason?: string;
  /** Structured routing metadata (only on state=final). */
  routing?: ChatEventRouting;
}

// ---------------------------------------------------------------------------
// Plugin config types
// ---------------------------------------------------------------------------

export interface BcsChannelConfig {
  enabled?: boolean;
  bcsUrl?: string;
  botId?: string;
  botName?: string;
  capabilities?: Partial<BotCapabilities>;
  heartbeatIntervalMs?: number;
  reconnectIntervalMs?: number;
  connectionTimeoutMs?: number;
  dmPolicy?: string;
  allowFrom?: string[];
  sessionCleanup?: BcsSessionCleanupConfig;
  accounts?: Record<string, BcsAccountRaw>;
}

export interface BcsSessionCleanupConfig {
  pruneAfter?: string | number;
  intervalMinutes?: number;
}

export interface BcsAccountRaw {
  enabled?: boolean;
  bcsUrl?: string;
  botId?: string;
  botName?: string;
  capabilities?: Partial<BotCapabilities>;
  heartbeatIntervalMs?: number;
  reconnectIntervalMs?: number;
  connectionTimeoutMs?: number;
  dmPolicy?: string;
  allowFrom?: string[];
}

export interface ResolvedBcsAccount {
  accountId: string;
  enabled: boolean;
  bcsUrl: string;
  botId: string;
  connectBotId?: string;
  botName: string;
  capabilities: BotCapabilities;
  heartbeatIntervalMs: number;
  reconnectIntervalMs: number;
  connectionTimeoutMs: number;
  dmPolicy?: string;
  allowFrom?: string[];
}

// ---------------------------------------------------------------------------
// Task group types (task.dispatch / task.complete)
// ---------------------------------------------------------------------------

/** Parameters for the bcs_assign_task tool (sent as task.dispatch to BCS). */
export interface TaskDispatchParams {
  group_id: string;
  target_bot: string;
  message: string;
  response_mode?: 'after-last-tool-call' | 'full';
}

/** Response payload from task.dispatch. */
export interface TaskDispatchResponse {
  task_id: string;
  status: 'dispatched';
}

/** Parameters for the bcs_send_task_message tool (sent as task.message to BCS). */
export interface TaskMessageParams {
  group_id: string;
  message: string;
}

/** Response payload from task.message. */
export interface TaskMessageResponse {
  status: 'sent';
}

/** Parameters for the bcs_task_complete tool (sent as task.complete to BCS). */
export interface TaskCompleteParams {
  group_id: string;
  summary: string;
}

/** Info cached per session for task group tool activation. */
export interface TaskGroupInfo {
  groupId: string;
  groupType: string;
  originator: string;
  participants: string[];
  /** Recipient bot's role in this group (e.g. "manager"). Used to gate task tools. */
  recipientRole?: string;
}
