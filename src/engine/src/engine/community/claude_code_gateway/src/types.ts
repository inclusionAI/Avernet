// ---- Wire protocol frames (OpenClaw-compatible v3) ----

export type GatewayRequestFrame = {
  type: 'req';
  id: string;
  method: string;
  params?: Record<string, unknown>;
};

export type GatewayResponseFrame = {
  type: 'res';
  id: string;
  ok: boolean;
  payload?: unknown;
  error?: {
    code: string;
    message: string;
    details?: unknown;
    retryable?: boolean;
    retryAfterMs?: number;
  };
};

export type GatewayEventFrame = {
  type: 'event';
  event: string;
  payload?: unknown;
  seq?: number;
  stateVersion?: { presence: number; health: number };
};

export type GatewayFrame = GatewayRequestFrame | GatewayResponseFrame | GatewayEventFrame;

// ---- Chat event (user-facing text stream) ----

export type GatewayChatEvent = {
  runId: string;
  sessionKey: string;
  seq: number;
  state: 'delta' | 'final' | 'aborted' | 'error';
  message?: {
    role?: string;
    content?: Array<{ type?: string; text?: string }>;
    timestamp?: number;
  };
  /** Incremental text appended since the previous delta event (absent on final/aborted/error). */
  delta?: string;
  errorMessage?: string;
  stopReason?: string;
  usage?: unknown;
};

// ---- Agent event (structured event stream) ----

/** Agent context for subagent attribution. Present on events originating from a subagent. */
export type AgentContext = {
  /** Primary key. Non-null = event belongs to subagent. Value = the Agent tool_use ID that spawned this subagent. */
  parentToolUseId?: string;
  /** Associated task event ID (from SDKToolProgressMessage.task_id). */
  taskId?: string;
  /** SDK agent_id (only available in permission/hook contexts). */
  agentId?: string;
  /** Agent type name (e.g. "general-purpose", "code-reviewer"). */
  agentType?: string;
  /** User-facing name (Agent tool description). */
  agentName?: string;
};

export type AgentEventStream =
  | 'lifecycle'
  | 'tool'
  | 'assistant'
  | 'thinking'
  | 'command_output'
  | 'interaction'
  | 'mode_transition' // compatibility stream for mode_switch
  | 'message'
  | 'content_block'
  | 'todo' // TodoWrite tool calls — persistent task panel, not chat stream
  | 'task' // Sub-agent task lifecycle (task_started / task_progress / task_notification)
  | 'system' // System status events (compaction, retry, rate limit, compact boundary, files persisted)
  | 'memory' // Memory recall events
  | (string & {});

export type AgentEventPayload = {
  runId: string;
  sessionKey?: string;
  seq: number;
  stream: AgentEventStream;
  ts: number;
  data: Record<string, unknown>;
  /** Incremental text for streaming events (e.g. thinking delta). Top-level alongside chat events' delta. */
  delta?: string;
};

// -- lifecycle stream data --

export type AgentLifecyclePhase = 'start' | 'end' | 'error';

/** Claude Agent 运行模式 */
export type AgentMode = 'plan' | 'execute' | 'auto';

export type AgentLifecycleData = {
  phase: AgentLifecyclePhase;
  stopReason?: string;
  error?: string;
  sessionId?: string;
  cwd?: string;
  tools?: string[];
  /** 当前 Agent 运行模式 (plan/execute/auto) */
  agentMode?: AgentMode;
};

// -- tool stream data (protocol-aligned field names) --

export type AgentToolPhase = 'start' | 'update' | 'progress' | 'result' | 'summary' | 'task';

export type AgentToolData = {
  type: AgentToolPhase;
  toolCallId: string;
  toolName: string;
  input?: Record<string, unknown>;
  partialInput?: string;
  partialOutput?: unknown;
  output?: unknown;
  error?: string;
  /** 是否需要用户交互 (用于 AskUserQuestion 等工具) */
  requiresInteraction?: boolean;
  /** 交互详情 (当 requiresInteraction 为 true 时) */
  interaction?: {
    interactionId: string;
    kind: 'ask_user' | 'exec' | 'mode_switch';
    questions?: InteractionQuestion[];
    options?: Array<{ value: string; label: string; recommended?: boolean }>;
    inputSchema?: Record<string, unknown>;
    uiHints?: Record<string, unknown>;
  };
  /** Task 状态 (当 type 为 'task' 时) */
  task?: {
    taskId: string;
    status: 'in_progress' | 'completed' | 'failed';
    summary: string;
    outputFile?: string;
  };
  /** Subagent context — present when this tool call originates from a subagent. */
  agentContext?: AgentContext;
  /** Tool progress data (type='progress'). */
  progress?: { elapsedSeconds: number };
  /** Tool summary data (type='summary'). Maps to SDK preceding_tool_use_ids. */
  precedingToolUseIds?: string[];
  summary?: string;
  /** Aggregated subagent tools (toolName='Task' result only). */
  subagentTools?: Array<{
    toolId: string;
    toolName: string;
    toolInput: unknown;
    toolResult?: { content: string; isError: boolean } | null;
    timestamp: number;
  }>;
};

// -- thinking stream data --

export type AgentThinkingData = {
  text: string;
  delta: string;
};

// -- command_output stream data --

export type AgentCommandOutputData = {
  toolCallId: string;
  phase: 'delta' | 'end';
  output?: string;
  exitCode?: number | null;
  durationMs?: number;
  cwd?: string;
};

// -- todo stream data (TodoWrite tool calls) --

export type TodoItem = {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  activeForm: string;
};

export type AgentTodoData = {
  /** Full replacement todo list — clients should replace their entire list, not append. */
  todos: TodoItem[];
  toolCallId?: string;
};

// -- task stream data (sub-agent lifecycle) --

export type AgentTaskData = {
  type: 'task_started' | 'task_progress' | 'task_notification' | 'task_updated';
  taskId: string;
  toolUseId?: string;
  /** task_started: task type (e.g. 'agent', 'workflow'). */
  taskType?: string;
  /** task_started: workflow name. */
  workflowName?: string;
  /** task_started: the prompt sent to the subagent. */
  prompt?: string;
  /** task_progress: name of the last tool executed by the subagent. */
  lastToolName?: string;
  status?: 'pending' | 'running' | 'completed' | 'failed' | 'stopped' | 'killed';
  description?: string;
  summary?: string;
  outputFile?: string;
  usage?: { totalTokens: number; toolUses: number; durationMs: number };
  /** Patch fields for task_updated events. */
  patch?: {
    status?: 'pending' | 'running' | 'completed' | 'failed' | 'killed';
    description?: string;
    endTime?: number;
    totalPausedMs?: number;
    error?: string;
    isBackgrounded?: boolean;
  };
};

// -- system stream data --

export type SystemStatusChange = {
  type: 'status_change';
  status: 'compacting' | 'requesting' | null;
  compactResult?: 'success' | 'failed' | null;
  compactError?: string | null;
};

export type SystemApiRetry = {
  type: 'api_retry';
  attempt: number;
  maxRetries: number;
  retryDelayMs: number;
  errorStatus?: number | null;
  error: string;
};

export type SystemRateLimit = {
  type: 'rate_limit';
  status: 'allowed' | 'allowed_warning' | 'rejected';
  rateLimitType?: 'five_hour' | 'seven_day' | 'seven_day_opus' | 'seven_day_sonnet' | 'overage';
  utilization?: number;
  resetsAt?: number;
  overageStatus?: 'allowed' | 'allowed_warning' | 'rejected';
  overageResetsAt?: number;
};

export type SystemCompactBoundary = {
  type: 'compact_boundary';
  trigger: 'manual' | 'auto';
  preTokens: number;
  postTokens?: number;
  durationMs?: number;
  compactedTurns: number;
};

export type SystemFilesPersisted = {
  type: 'files_persisted';
  files: Array<{ filename: string; fileId: string }>;
  failed: Array<{ filename: string; error: string }>;
  processedAt: string;
};

export type AgentSystemData =
  | SystemStatusChange
  | SystemApiRetry
  | SystemRateLimit
  | SystemCompactBoundary
  | SystemFilesPersisted;

// -- memory stream data --

export type AgentMemoryData = {
  type: 'recall';
  mode: 'select' | 'synthesize';
  memories: Array<{
    path: string;
    scope: 'personal' | 'team';
    content?: string | null;
  }>;
};

// -- message stream data --

export type AgentMessagePhase = 'start' | 'stop';

export type AgentMessageData = {
  phase: AgentMessagePhase;
  messageId?: string;
  model?: string;
  usage?: {
    inputTokens?: number;
    outputTokens?: number;
  };
};

// -- content_block stream data --

export type AgentContentBlockPhase = 'start' | 'stop';

export type AgentContentBlockData = {
  phase: AgentContentBlockPhase;
  index: number;
  blockType: 'text' | 'thinking' | 'tool_use';
  toolCallId?: string;
  name?: string;
};

// ---- Enhanced assistant data ----

export type AgentAssistantData = {
  usage?: {
    inputTokens?: number;
    outputTokens?: number;
    cacheReadTokens?: number;
    cacheCreationTokens?: number;
  };
  costUsd?: number;
  durationMs?: number;
  numTurns?: number;
  model?: string;
};

// ---- Unified Interaction Protocol (HITL) ----

/** Interaction kind - unified classification for all HITL scenarios.
 * `mode_switch` is a first-class interaction kind; current compatibility streams
 * may still mirror it as `agent.stream='mode_transition'`. */
export type InteractionKind = 'ask_user' | 'exec' | 'mode_switch';

/** Subject of the interaction - what object this interaction is about */
export type InteractionSubject = {
  type?: 'tool' | 'command' | 'file' | 'mode';
  toolName?: string;
  toolCallId?: string;
  command?: string;
  cwd?: string;
  filePath?: string;
  fromMode?: string;
  toMode?: string;
  /** Edit 工具：原内容（用于前端展示 diff 预览） */
  old_string?: string;
  /** Edit 工具：新内容（用于前端展示 diff 预览） */
  new_string?: string;
  /** 操作类型：create(新建) / overwrite(覆盖) / edit(编辑) / read(读取) */
  operation?: 'create' | 'overwrite' | 'edit' | 'read';
  /** 操作描述（用于前端展示给用户） */
  description?: string;
};

/** Named option for choices/mode switches */
export type InteractionOption = {
  value: string;
  label: string;
  description?: string;
  recommended?: boolean;
};

/** Question for ask_user kind */
export type InteractionQuestion = {
  question: string;
  header?: string;
  multiSelect?: boolean;
  options?: Array<{
    label: string;
    description?: string;
    preview?: string;
  }>;
};

/** UI hints - non-business semantic UI guidance */
export type InteractionUiHints = {
  variant?: 'question' | 'warning' | 'plan';
  severity?: 'info' | 'warning' | 'danger';
  collapsible?: boolean;
};

/** Input schema - helps frontend understand how to render input */
export type InteractionInputSchema = {
  type: 'none' | 'text' | 'choices' | 'form';
  multiSelect?: boolean;
};

/** Interaction phase - final state of resolved interaction.
 * 'allowed' is the canonical success phase for kind='exec' (frontend expects this).
 * 'answered' is the canonical success phase for kind='ask_user'. */
export type InteractionPhase = 'answered' | 'allowed' | 'denied' | 'cancelled' | 'expired';

/** v3: Unified interaction.requested event */
export type InteractionRequestedEvent = {
  interactionId: string;
  runId: string;
  sessionKey: string;
  kind: InteractionKind;
  title?: string;
  description?: string;
  prompt?: string;
  subject?: InteractionSubject;
  questions?: InteractionQuestion[];
  options?: InteractionOption[];
  /** kind='exec' convenience fields flattened to event root for frontend binding. */
  command?: string;
  cwd?: string;
  inputSchema?: InteractionInputSchema;
  uiHints?: InteractionUiHints;
  agentContext?: AgentContext;
  createdAtMs: number;
  expiresAtMs: number;
};

// -- Notification event (top-level, not agent stream) --

export type NotificationPriority = 'low' | 'medium' | 'high' | 'immediate';

export type NotificationEvent = {
  key: string;
  text: string;
  priority: NotificationPriority;
  color?: string;
  timeoutMs?: number;
  sessionKey?: string;
  runId?: string;
};

// -- Prompt suggestion event (top-level, not agent stream) --

export type PromptSuggestionEvent = {
  runId: string;
  sessionKey: string;
  suggestions: Array<{ text: string }>;
};

/** Compatibility stream for mode-switch cards.
 * New clients should prefer top-level `interaction.requested(kind='mode_switch')` when available. */
export type AgentModeTransitionPhase = 'requested' | 'resolved' | 'expired';

export type AgentModeTransitionData = {
  phase: AgentModeTransitionPhase;
  transitionId: string;
  kind?: 'exit_plan_mode';
  fromMode?: string;
  toMode?: string;
  summary?: string;
  decision?: 'proceed' | 'stay';
  createdAtMs?: number;
  resolvedAtMs?: number;
  expiresAtMs?: number;
};

/** v3: Unified interaction.resolved event */
export type InteractionResolvedEvent = {
  interactionId: string;
  runId: string;
  sessionKey: string;
  kind: InteractionKind;
  phase: InteractionPhase;
  decision: string;
  answer?: string;
  answers?: Record<string, string>;
  values?: Record<string, unknown>;
  selectedOptions?: string[];
  resolvedBy: string;
  resolvedAtMs: number;
};

/** v3: Unified interaction.resolve params */
export type InteractionResolveParams = {
  interactionId: string;
  /** Unified decision field. Wire format is string, but allowed values are constrained by kind.
   * Recommended: submit / cancel / approve / deny / proceed / stay
   */
  decision: string;
  /** Single text answer */
  answer?: string;
  /** Form or structured input */
  values?: Record<string, unknown>;
  /** Selected option values */
  selectedOptions?: string[];
  /** Optional metadata for extensibility */
  meta?: Record<string, unknown>;

  // backward compat - deprecated
  /** @deprecated use decision instead */
  action?: 'submit' | 'cancel';
  /** @deprecated use answer instead */
  message?: string;
};

/** v3: Unified interaction.resolve result */
export type InteractionResolveResult = {
  accepted: boolean;
  interactionId: string;
  decision: string;
  kind?: InteractionKind;
};

// ---- Legacy/Deprecated types (for backward compatibility) ----

/** @deprecated use InteractionKind instead */
export type LegacyInteractionKind = 'exec' | 'ask_user';

/** @deprecated use InteractionQuestion instead */
export type ApprovalQuestion = InteractionQuestion;

/** @deprecated use InteractionPhase instead. Note: 'requested' is no longer a valid phase. */
export type LegacyInteractionPhase = 'requested' | 'answered' | 'cancelled' | 'expired';

// ---- Snapshot types ----

export type Snapshot = {
  presence: unknown[];
  health: unknown;
  stateVersion: { presence: number; health: number };
};

// ---- Session types ----

// Structured history content blocks (for user/assistant messages)
export type HistoryContentBlock =
  | { type: 'text'; text: string }
  | { type: 'thinking'; text: string }
  | { type: 'tool_use'; toolCallId: string; toolName: string; input: Record<string, unknown> };

// Tool use metadata (stored in SessionHistoryMessage.metadata)
export type ToolUseMeta = {
  toolCallId: string;
  toolName: string;
  input: Record<string, unknown>;
  title?: string;
  description?: string;
  subject?: InteractionSubject;
};

// Tool result metadata (stored in SessionHistoryMessage.metadata)
export type ToolResultMeta = {
  toolCallId: string;
  toolName: string;
  output: string;
  exitCode?: number;
  durationMs?: number;
  isError?: boolean;
  isSynthetic?: boolean;
  title?: string;
  description?: string;
  subject?: InteractionSubject;
};

// Thinking metadata (stored in SessionHistoryMessage.metadata)
export type ThinkingMeta = {
  text: string;
};

export type SessionHistoryMessage = {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool_use' | 'tool_result' | 'thinking';
  text: string;
  timestamp: string;
  runId?: string;
  content?: HistoryContentBlock[] | null;
  metadata?: ToolUseMeta | ToolResultMeta | ThinkingMeta;
};

export type SessionBinding = {
  gatewaySessionKey: string;
  acpSessionId: string;
  cwd?: string;
  title?: string;
  createdAt: string;
  updatedAt: string;
  history?: SessionHistoryMessage[];
  /** Claude Agent SDK session ID for resuming conversations (SDK bridge only). */
  sdkSessionId?: string;
  /** Last requested chat model (set on each chat.send). */
  model?: string;
  /** Last requested permission mode (e.g. default, acceptEdits, bypassPermissions, plan). */
  permissionMode?: string;
  /**
   * Extra directories to expose to Claude besides `cwd` — maps to the SDK's
   * `additionalDirectories` option and the CLI's `--add-dir` flag. Set via
   * session.new / sessions.patch; chat.send falls back to this if the request
   * frame omits it.
   */
  additionalDirectories?: string[];
};
