import {
  startClaudePrompt,
  type ClaudePromptHandlers,
  type ClaudePromptResult,
  type RunningClaudePrompt,
  type ToolUseInfo,
} from './claude-cli-bridge.js';
import { type InteractionRequestedRuntimeEvent } from './claude-sdk-bridge.js';
import type { TodoItem, ToolUseMeta, ToolResultMeta, ThinkingMeta, InjectMeta } from './types.js';
import { createLogger } from './debug.js';

const log = createLogger('orchestrator');

export type HistoryRole = 'user' | 'assistant' | 'tool_use' | 'tool_result' | 'thinking';

export type OrchestratorHistoryEntry = {
  role: HistoryRole;
  text: string;
  senderName?: string;
  metadata?: ToolUseMeta | ToolResultMeta | ThinkingMeta | InjectMeta;
};

export type OrchestratorUsage = {
  inputTokens?: number;
  outputTokens?: number;
  cacheReadTokens?: number;
  cacheCreationTokens?: number;
};

export type OrchestratorCost = {
  costUsd?: number;
  durationMs?: number;
  numTurns?: number;
};

export type OrchestratorMessageData = {
  messageId?: string;
  model?: string;
  usage?: { inputTokens?: number; outputTokens?: number };
};

export type OrchestratorContentBlockData = {
  index: number;
  blockType: string;
  toolCallId?: string;
  name?: string;
};

export type OrchestratorEvent =
  | { kind: 'textDelta'; fullText: string; delta: string }
  | { kind: 'thinkingDelta'; fullText: string; delta: string }
  | { kind: 'toolStart'; tool: ToolUseInfo }
  | { kind: 'toolUpdate'; toolCallId: string; partialJson: string }
  | { kind: 'toolEnd'; tool: ToolUseInfo }
  | { kind: 'commandOutput'; toolCallId: string; phase: 'delta' | 'end'; output: string; meta?: { exitCode?: number | null; durationMs?: number; cwd?: string } }
  | { kind: 'lifecycle'; phase: 'start' | 'end' | 'error'; data?: Record<string, unknown> }
  | { kind: 'usage'; usage: OrchestratorUsage }
  | { kind: 'messageStart'; data: OrchestratorMessageData }
  | { kind: 'messageStop' }
  | { kind: 'contentBlockStart'; data: OrchestratorContentBlockData }
  | { kind: 'contentBlockStop'; data: { index: number; blockType: string } }
  | { kind: 'cost'; data: OrchestratorCost }
  | { kind: 'task'; data: { type: 'task_started' | 'task_progress' | 'task_notification' | 'task_updated'; taskId: string; toolUseId?: string; status?: 'pending' | 'running' | 'completed' | 'failed' | 'stopped' | 'killed'; description?: string; summary?: string; outputFile?: string; usage?: { totalTokens: number; toolUses: number; durationMs: number }; taskType?: string; workflowName?: string; prompt?: string; lastToolName?: string; patch?: Record<string, unknown> } }
  | { kind: 'todoUpdate'; todos: TodoItem[]; toolCallId?: string }
  | { kind: 'toolProgress'; data: { toolCallId: string; toolName: string; parentToolUseId: string | null; elapsedSeconds: number; taskId?: string } }
  | { kind: 'toolSummary'; data: { summary: string; precedingToolUseIds: string[] } }
  | { kind: 'system'; data: Record<string, unknown> }
  | { kind: 'memoryRecall'; data: { mode: string; memories: unknown[] } }
  | { kind: 'notification'; data: { key: string; text: string; priority: string; color?: string; timeoutMs?: number } }
  | { kind: 'promptSuggestion'; data: { suggestion: string } };

export type OrchestratorFinal = {
  ok: boolean;
  text: string;
  stopReason?: string;
  error?: string;
  toolUses: ToolUseInfo[];
  aborted: boolean;
  sdkSessionId?: string;
};

export type ChatRunnerFactory = (
  params: {
    cwd: string;
    message: string;
    systemPrompt?: string;
    model?: string;
    permissionMode?: string;
    env?: Record<string, string>;
    /**
     * Extra directories beyond `cwd` to expose to Claude — maps to the SDK's
     * `additionalDirectories` option and the CLI's `--add-dir` flag.
     */
    additionalDirectories?: string[];
    /** SDK session ID to resume a previous conversation. */
    resumeSessionId?: string;
    /** Whether this is a new session (no prior SDK conversation exists). */
    isNewSession?: boolean;
    /** Run ID for correlation with gateway events. */
    runId?: string;
    /** Session key for correlation with gateway session. */
    sessionKey?: string;
    /** Callback when an interaction is requested (tool use needs approval). */
    onInteractionRequested?: (event: InteractionRequestedRuntimeEvent) => void;
  },
  handlers?: ClaudePromptHandlers,
) => RunningClaudePrompt;

export type BuildContextOptions = {
  history: OrchestratorHistoryEntry[];
  contextTurns?: number;
  maxContextChars?: number;
  /** Lead-in paragraph prepended to the joined history. */
  intro?: string;
  /** Label function for history entries, e.g. ({ role, senderName }) => senderName ?? '用户'. */
  label?: (entry: OrchestratorHistoryEntry) => string;
};

const DEFAULT_CONTEXT_TURNS = 8;
const DEFAULT_MAX_CONTEXT_CHARS = 12000;
const DEFAULT_INTRO = [
  '以下是当前会话最近的上下文，请在回答时延续这段对话语境。',
  '如果用户提到"上一次""刚才""前文"，请优先参考这些上下文。',
].join('\n');

function formatHistoryEntry(m: OrchestratorHistoryEntry): string {
  const meta = m.metadata;

  // tool_use
  if (meta && 'toolName' in meta && 'input' in meta) {
    const json = JSON.stringify({ name: meta.toolName, input: meta.input });
    return `\n<tool>${json}</tool>\n`;
  }

  // tool_result
  if (meta && 'output' in meta) {
    const result = JSON.stringify({ name: meta.toolName, success: !meta.isError, output: meta.output, exitCode: meta.exitCode, durationMs: meta.durationMs });
    return `\n<tool_result>${result}</tool_result>\n`;
  }

  // thinking
  if (meta && 'text' in meta && !('toolName' in meta)) {
    return `\n<thinking>${meta.text}</thinking>\n`;
  }

  // fallback
  return m.text;
}

/**
 * Build a system-prompt conversation-context snippet from a history array.
 * Shared by the OpenClaw gateway — only the role labels and
 * intro copy differ.
 */
export function buildConversationContext(opts: BuildContextOptions): string {
  const turns = Math.max(0, opts.contextTurns ?? DEFAULT_CONTEXT_TURNS);
  const maxChars = opts.maxContextChars ?? DEFAULT_MAX_CONTEXT_CHARS;
  const intro = opts.intro ?? DEFAULT_INTRO;
  const labelFn = opts.label ?? (entry => (entry.role === 'user' ? '用户' : '助手'));

  const relevant = opts.history
    .filter(m => m.role === 'user' || m.role === 'assistant' || m.role === 'tool_use' || m.role === 'tool_result' || m.role === 'thinking')
    .slice(-turns * 2);
  if (relevant.length === 0) return '';

  // 每条消息添加角色标签，并用双换行分隔，确保历史消息清晰分开
  const joined = relevant.map(m => {
    const label = labelFn(m);
    const prefix = `${label}:`;
    const body = formatHistoryEntry(m);
    const text = body.startsWith('\n') ? body.slice(1) : body;
    return `${prefix} ${text}`;
  }).join('\n\n');
  const trimmed = joined.length > maxChars ? joined.slice(joined.length - maxChars) : joined;

  return [ intro, '', trimmed ].join('\n');
}

export type OrchestratorInput = {
  cwd: string;
  message: string;
  /** Optional Claude model id, e.g. `claude-sonnet-4-5`. */
  model?: string;
  /** Optional Claude permission mode: `default`, `acceptEdits`, `bypassPermissions`, or `plan`. */
  permissionMode?: string;
  /** Per-request environment variable overrides (e.g. ANTHROPIC_MODEL). */
  env?: Record<string, string>;
  /**
   * Extra directories beyond `cwd` to expose to Claude — maps to the SDK's
   * `additionalDirectories` option and the CLI's `--add-dir` flag.
   */
  additionalDirectories?: string[];
  /** Optional pre-built system prompt. If omitted, it is built from `history` + `contextOptions`. */
  systemPrompt?: string;
  /** Used to build a system prompt when `systemPrompt` is not provided. */
  history?: OrchestratorHistoryEntry[];
  contextOptions?: Omit<BuildContextOptions, 'history'>;
  /** Prepended *before* the conversation-context paragraph. */
  systemPromptPrefix?: string;
  /**
   * SDK session ID returned by a previous `startChatRun` for the same session.
   * When present, the active bridge is asked to resume the upstream conversation
   * (SDK: `options.resume`; CLI: `--resume <id>`) so model-side context is
   * preserved without re-sending the whole history as a system prompt.
   */
  resumeSessionId?: string;
  /**
   * Whether this is a new session (no prior SDK conversation exists).
   * When true and `resumeSessionId` is set, the SDK bridge uses `options.sessionId`
   * to create a new session with the specified ID instead of `options.resume`.
   */
  isNewSession?: boolean;

  // ---- HITL Suspend/Resume Support (Phase 1) ----
  /** Run ID for correlation with gateway events. */
  runId?: string;
  /** Session key for correlation with gateway session. */
  sessionKey?: string;
  /** Callback when an interaction is requested (tool use needs approval). */
  onInteractionRequested?: (event: InteractionRequestedRuntimeEvent) => void;
};

export type OrchestratorListener = (event: OrchestratorEvent) => void;

export type RunningOrchestration = {
  /** Subscribe to normalized events. Call once. */
  subscribe(listener: OrchestratorListener): void;
  abort(): void;
  completed: Promise<OrchestratorFinal>;
};

export type StartChatRunOptions = {
  /** Swap the Claude runner (used in tests). Defaults to the module-level default. */
  runner?: ChatRunnerFactory;
};

let defaultRunner: ChatRunnerFactory = startClaudePrompt;

/** Override the Claude runner used by `startChatRun` when no `options.runner` is given. */
export function setDefaultChatRunner(runner: ChatRunnerFactory): void {
  defaultRunner = runner;
}

/** Restore the built-in runner (intended for tests). */
export function resetDefaultChatRunner(): void {
  defaultRunner = startClaudePrompt;
}

/**
 * Start a single Claude chat run and expose it as a normalized event stream.
 * The orchestrator is protocol-agnostic: callers (server) translate events
 * into their own protocol frames.
 */
export function startChatRun(
  input: OrchestratorInput,
  options: StartChatRunOptions = {},
): RunningOrchestration {
  const runner = options.runner ?? defaultRunner;
  const systemPrompt = resolveSystemPrompt(input);

  let listener: OrchestratorListener | null = null;
  let aborted = false;
  const preSubscribeBuffer: OrchestratorEvent[] = [];

  const emit = (event: OrchestratorEvent) => {
    if (!listener) {
      preSubscribeBuffer.push(event);
      return;
    }
    try {
      listener(event);
    } catch (err) {
      log.error('listener-threw', { kind: event.kind, error: (err as Error).message });
    }
  };

  const handlers: ClaudePromptHandlers = {
    onTextDelta(fullText, delta) {
      emit({ kind: 'textDelta', fullText, delta });
    },
    onThinkingDelta(fullText, delta) {
      emit({ kind: 'thinkingDelta', fullText, delta });
    },
    onToolStart(tool) {
      emit({ kind: 'toolStart', tool });
    },
    onToolUpdate(toolCallId, partialJson) {
      emit({ kind: 'toolUpdate', toolCallId, partialJson });
    },
    onToolEnd(tool) {
      emit({ kind: 'toolEnd', tool });
    },
    onCommandOutput(toolCallId, phase, output, meta) {
      emit({ kind: 'commandOutput', toolCallId, phase, output, meta });
    },
    onLifecycle(phase, data) {
      emit({ kind: 'lifecycle', phase, data });
    },
    onUsage(usage) {
      emit({ kind: 'usage', usage });
    },
    onMessageStart(data) {
      emit({ kind: 'messageStart', data });
    },
    onMessageStop() {
      emit({ kind: 'messageStop' });
    },
    onContentBlockStart(data) {
      emit({ kind: 'contentBlockStart', data });
    },
    onContentBlockStop(data) {
      emit({ kind: 'contentBlockStop', data });
    },
    onCost(data) {
      emit({ kind: 'cost', data });
    },
    onTaskEvent(event) {
      emit({ kind: 'task', data: event });
    },
    onTodoUpdate(todos, toolCallId) {
      emit({ kind: 'todoUpdate', todos, toolCallId });
    },
    onToolProgress(data) {
      emit({ kind: 'toolProgress', data });
    },
    onToolSummary(data) {
      emit({ kind: 'toolSummary', data });
    },
    onSystemEvent(data) {
      emit({ kind: 'system', data });
    },
    onMemoryRecall(data) {
      emit({ kind: 'memoryRecall', data });
    },
    onNotification(data) {
      emit({ kind: 'notification', data });
    },
    onPromptSuggestion(data) {
      emit({ kind: 'promptSuggestion', data });
    },
  };

  log.debug('startChatRun: invoking runner', {
    runId: input.runId,
    sessionKey: input.sessionKey,
    permissionMode: input.permissionMode,
    model: input.model,
    hasOnInteractionRequested: Boolean(input.onInteractionRequested),
  });

  const running = runner(
    {
      cwd: input.cwd,
      message: input.message,
      systemPrompt,
      model: input.model,
      permissionMode: input.permissionMode,
      env: input.env,
      additionalDirectories: input.additionalDirectories,
      resumeSessionId: input.resumeSessionId,
      isNewSession: input.isNewSession,
      runId: input.runId,
      sessionKey: input.sessionKey,
      onInteractionRequested: input.onInteractionRequested,
    },
    handlers,
  );

  const completed: Promise<OrchestratorFinal> = running.completed.then((result: ClaudePromptResult) => ({
    ok: result.ok,
    text: result.text,
    stopReason: result.stopReason,
    error: result.error,
    toolUses: result.toolUses,
    aborted: aborted || result.stopReason === 'cancelled' || (result.error ?? '').includes('signal'),
    sdkSessionId: result.sdkSessionId,
  }));

  return {
    subscribe(l) {
      if (listener) throw new Error('RunningOrchestration.subscribe may only be called once');
      listener = l;
      while (preSubscribeBuffer.length > 0) {
        const event = preSubscribeBuffer.shift()!;
        try {
          l(event);
        } catch (err) {
          log.error('listener-threw', { kind: event.kind, error: (err as Error).message });
        }
      }
    },
    abort() {
      aborted = true;
      running.abort();
    },
    completed,
  };
}

function resolveSystemPrompt(input: OrchestratorInput): string | undefined {
  if (input.systemPrompt !== undefined) return input.systemPrompt || undefined;
  const parts: string[] = [];
  if (input.systemPromptPrefix?.trim()) parts.push(input.systemPromptPrefix.trim());
  if (input.history && input.history.length > 0) {
    const ctx = buildConversationContext({ history: input.history, ...(input.contextOptions ?? {}) });
    if (ctx) parts.push(ctx);
  }
  const joined = parts.join('\n\n');
  return joined.trim() ? joined : undefined;
}
