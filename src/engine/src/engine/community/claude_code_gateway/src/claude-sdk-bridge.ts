// Claude Code SDK (Claude Agent SDK) bridge.
//
// This is an alternative to `claude-cli-bridge.ts` that uses the official
// `@anthropic-ai/claude-agent-sdk` Node SDK instead of spawning the `claude`
// binary directly. It exposes the same `ClaudePromptHandlers` callback shape
// and returns a `RunningSdkPrompt` with the same `{ completed, abort }`
// surface consumed by `server.ts`, so the two bridges are interchangeable.
//
// Wire selection via `CLAUDE_BRIDGE=sdk|cli` (default `sdk`).
//
// Import is deferred with a dynamic `import()` inside `startClaudePromptSdk`
// so projects that only use the CLI bridge do not need the SDK installed.

import crypto from 'node:crypto';

import type {
  ClaudeHealth,
  ClaudePromptHandlers,
  ClaudePromptResult,
  ToolUseInfo,
} from './claude-cli-bridge.js';
import { createLogger } from './debug.js';
import { EXEC_APPROVAL_TIMEOUT_MS } from './interaction/builders.js';
import { loadRelayModelProviderEnv } from './model-provider-settings.js';

// ---- HITL Suspend/Resume Types ----

export type PermissionResult =
  | { behavior: 'allow'; updatedInput?: Record<string, unknown>; updatedPermissions?: unknown[] }
  | { behavior: 'deny'; message: string };

export type PendingToolWait = {
  interactionId: string;
  runId: string;
  sessionKey: string;
  toolName: string;
  toolCallId?: string;
  originalInput: Record<string, unknown>;
  resolve: (result: PermissionResult) => void;
  reject: (error: Error) => void;
  createdAtMs: number;
  expiresAtMs: number;
  signal?: AbortSignal;
  resolved: boolean;
  /** SDK-provided permission suggestions; forwarded as updatedPermissions on allow-always. */
  suggestions?: unknown[];
};

type CommandOutputMeta = { exitCode?: number | null; durationMs?: number; cwd?: string };

export type InteractionRequestedRuntimeEvent = {
  interactionId: string;
  runId: string;
  sessionKey: string;
  toolName: string;
  toolCallId?: string;
  input: Record<string, unknown>;
  createdAtMs: number;
  expiresAtMs: number;
  /** SDK-provided permission suggestions; stored so allow-always can forward them. */
  suggestions?: unknown[];
  /** Subagent context — present when this interaction originates from a subagent. */
  agentContext?: import('./types.js').AgentContext;
};

// In-memory registry for pending tool waits (SDK side)
const pendingToolWaits = new Map<string, PendingToolWait>();

const log = createLogger('sdk');

/**
 * Extract a UUID from a gateway session key.
 * Gateway session keys have the format "session:<uuid>" or "session:<uuid>:user:<userId>".
 * The SDK requires a valid UUID for `options.sessionId`, so we must extract
 * the UUID portion from the sessionKey.
 * Returns the UUID portion, or the original string if it's already a bare UUID,
 * or null if the key doesn't match the expected format.
 */
export function extractUuidFromSessionKey(sessionKey: string): string | null {
  // Match patterns like "session:<uuid>"
  // or "session:<uuid>:user:alice"
  const match = sessionKey.match(/^session:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
  if (match) return match[1];
  // If the sessionKey itself is a bare UUID, return it directly
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(sessionKey)) {
    return sessionKey;
  }
  return null;
}

/**
 * Resolve a pending tool approval by interactionId.
 * Called by server.ts when interaction.resolve is received.
 */
export function resolveToolApproval(interactionId: string, result: PermissionResult): boolean {
  const wait = pendingToolWaits.get(interactionId);
  if (!wait) {
    log.warn('resolveToolApproval: wait not found', { interactionId, pendingCount: pendingToolWaits.size });
    return false;
  }
  if (wait.resolved) {
    log.warn('resolveToolApproval: wait already resolved', { interactionId, resolved: wait.resolved });
    return false;
  }
  const isAllow = result.behavior === 'allow';
  log.debug('resolveToolApproval: resolving', {
    interactionId,
    behavior: result.behavior,
    hasUpdatedInput: isAllow && Boolean(result.updatedInput),
    hasUpdatedPermissions: isAllow && Boolean(result.updatedPermissions),
  });
  wait.resolved = true;
  pendingToolWaits.delete(interactionId);
  wait.resolve(result);
  log.debug('resolveToolApproval: resolved successfully', { interactionId });
  return true;
}

/**
 * Reject a pending tool approval by interactionId.
 * Called when interaction expires, aborts, or encounters fatal error.
 */
export function rejectToolApproval(interactionId: string, error: Error): boolean {
  const wait = pendingToolWaits.get(interactionId);
  if (!wait) {
    log.warn('rejectToolApproval: wait not found', { interactionId, pendingCount: pendingToolWaits.size });
    return false;
  }
  if (wait.resolved) {
    log.warn('rejectToolApproval: wait already resolved', { interactionId, resolved: wait.resolved });
    return false;
  }
  log.debug('rejectToolApproval: rejecting', { interactionId, error: error.message });
  wait.resolved = true;
  pendingToolWaits.delete(interactionId);
  wait.reject(error);
  log.debug('rejectToolApproval: rejected successfully', { interactionId });
  return true;
}

/**
 * Get all pending tool waits for a session (for debugging/diagnostics).
 */
export function getPendingToolWaitsForSession(sessionKey: string): PendingToolWait[] {
  const result: PendingToolWait[] = [];
  for (const wait of pendingToolWaits.values()) {
    if (wait.sessionKey === sessionKey) {
      result.push(wait);
    }
  }
  return result;
}

/**
 * Clear all pending tool waits (for shutdown/cleanup).
 */
export function clearAllPendingToolWaits(): void {
  for (const wait of pendingToolWaits.values()) {
    if (!wait.resolved) {
      wait.reject(new Error('Server shutting down'));
    }
  }
  pendingToolWaits.clear();
}

// ---- Tool Gating Configuration ----

/** Tools that require user approval before execution */
const GATED_TOOLS = new Set([
  'AskUserQuestion',
  'ExitPlanMode',
  'Bash',
  'Edit',
  'Write',
  'Read',
]);

/**
 * Determine if a tool should be gated (require user approval).
 * @param toolName - The name of the tool
 * @return {boolean} true if the tool should be gated
 */
export function shouldGateTool(toolName: string): boolean {
  return GATED_TOOLS.has(toolName);
}

/**
 * Resolve the path to the Claude Code CLI executable.
 * Priority:
 *  1. CLAUDE_CODE_PATH env var (explicit override)
 *  2. `claude` found on PATH (common on macOS/Linux)
 * Returns undefined if nothing found (SDK will use its bundled binary).
 *
 * Logs at warn level on both the success and the unresolved branches so
 * deployed pre/prod logs (where CLAUDE_CODE_GATEWAY_DEBUG is off) still
 * carry enough breadcrumbs to diagnose `native binary not found at
 * $HOME/.local/bin/claude` failures on cloud Linux hosts.
 */
async function resolveClaudeCodePath(): Promise<string | undefined> {
  if (process.env.CLAUDE_CODE_PATH) {
    log.warn('resolveClaudeCodePath: using CLAUDE_CODE_PATH override', {
      path: process.env.CLAUDE_CODE_PATH,
    });
    return process.env.CLAUDE_CODE_PATH;
  }
  let whichError: string | undefined;
  try {
    const { execSync } = await import('node:child_process');
    const result = execSync('which claude 2>/dev/null', { encoding: 'utf-8', timeout: 3000 }).trim();
    if (result) {
      log.warn('resolveClaudeCodePath: found on PATH', { path: result });
      return result;
    }
  } catch (err) {
    whichError = err instanceof Error ? err.message : String(err);
  }
  log.warn(
    'resolveClaudeCodePath: no claude path resolved; SDK will fall back to its default lookup',
    await collectClaudePathDiagnostics(whichError ? { whichError } : {}),
  );
  return undefined;
}

/**
 * Capture the host state that affects how the SDK locates the Claude binary.
 * Emitted alongside the warning so the deployed log shows whether the failure
 * is a missing native install, a missing platform-specific SDK package, or
 * something more exotic (e.g. HOME pointing at /home/admin on a cloud host
 * that has no claude installed there).
 */
async function collectClaudePathDiagnostics(
  extra: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const [{ existsSync }, path, { createRequire }] = await Promise.all([
    import('node:fs'),
    import('node:path'),
    import('node:module'),
  ]);
  const home = process.env.HOME ?? '';
  const probedPaths: Record<string, boolean> = {};
  const probe = (p: string) => {
    if (!p) return;
    try {
      probedPaths[p] = existsSync(p);
    } catch {
      probedPaths[p] = false;
    }
  };
  if (home) probe(path.join(home, '.local/bin/claude'));
  // Always probe /home/admin too — that's the path hardcoded into the cloud
  // Linux deploy's HOME, and the one that surfaces in the SDK error message.
  probe('/home/admin/.local/bin/claude');
  probe('/usr/local/bin/claude');
  probe('/opt/homebrew/bin/claude');

  // Probe the SDK's vendored platform binary. If the optional dependency for
  // this host's platform/arch wasn't installed (typical when node_modules was
  // produced on darwin and copied to linux) the SDK's default lookup also
  // fails — same symptom, different root cause.
  const platformPkg = `@anthropic-ai/claude-agent-sdk-${process.platform}-${process.arch}`;
  let vendoredBinary: string | null = null;
  try {
    const req = createRequire(__filename);
    const pkgJsonPath = req.resolve(`${platformPkg}/package.json`);
    vendoredBinary = path.join(path.dirname(pkgJsonPath), 'claude');
    probe(vendoredBinary);
  } catch (err) {
    extra.platformPkgResolveError = err instanceof Error ? err.message : String(err);
  }
  return {
    HOME: home,
    USER: process.env.USER ?? '',
    PATH: process.env.PATH ?? '',
    cwd: process.cwd(),
    platform: `${process.platform}-${process.arch}`,
    platformPkg,
    vendoredBinary,
    probedPaths,
    ...extra,
  };
}

function isBinaryNotFoundError(message: string): boolean {
  return /native binary not found|executable not found|Failed to spawn Claude Code/i.test(message);
}

let _claudCodePath: string | undefined | null = null;

async function getClaudeCodePath(): Promise<string | undefined> {
  if (_claudCodePath === null) {
    _claudCodePath = await resolveClaudeCodePath();
  }
  return _claudCodePath;
}

export type RunningSdkPrompt = {
  completed: Promise<ClaudePromptResult>;
  abort: () => void;
};

export type StartClaudeSdkParams = {
  cwd: string;
  message: string;
  systemPrompt?: string;
  /** Optional Claude model id, e.g. `claude-sonnet-4-5`. */
  model?: string;
  /** Optional Claude permission mode: `default`, `acceptEdits`, `bypassPermissions`, or `plan`. */
  permissionMode?: string;
  /**
   * Extra directories beyond `cwd` to expose to Claude — forwarded to the SDK
   * as `options.additionalDirectories` (equivalent to the CLI's `--add-dir`).
   */
  additionalDirectories?: string[];
  /**
   * SDK session ID for resuming a previous conversation.
   * When set, the SDK uses `options.resume` to continue an existing session.
   */
  resumeSessionId?: string;
  /**
   * Whether this is a new session (no prior SDK conversation exists).
   * When true and `resumeSessionId` is set, the SDK uses `options.sessionId`
   * to create a new session with the specified ID instead of `options.resume`.
   * When false and `resumeSessionId` is set, the SDK uses `options.resume`
   * to resume the existing session.
   */
  isNewSession?: boolean;
  /** Extra options forwarded verbatim to the SDK `query({ options })` call. */
  sdkOptions?: Record<string, unknown>;
  /**
   * Per-request environment variable overrides merged into the SDK subprocess
   * env. Used to inject MCPORTER_USER_TOKEN for model-initiated MCP calls.
   */
  env?: Record<string, string>;

  // ---- HITL Suspend/Resume Support ----
  /** Run ID for correlation with gateway events. */
  runId?: string;
  /** Session key for correlation with gateway session. */
  sessionKey?: string;
  /** Callback when an interaction is requested (tool use needs approval). */
  onInteractionRequested?: (event: InteractionRequestedRuntimeEvent) => void;
};

/**
 * Probe whether the Claude Agent SDK is installed and importable.
 * Parallel to `probeClaudeCli()` in `claude-cli-bridge.ts`.
 */
export async function probeClaudeSdk(): Promise<ClaudeHealth> {
  log.debug('probe: begin');
  try {
    const mod = await import('@anthropic-ai/claude-agent-sdk');
    const hasQuery = typeof (mod as { query?: unknown }).query === 'function';
    log.debug('probe: resolved', { hasQuery });
    return {
      ok: hasQuery,
      cliExists: hasQuery,
      supportsStreamJson: hasQuery,
      message: hasQuery
        ? 'Claude Agent SDK 可用'
        : 'Claude Agent SDK 已安装，但未导出 query 函数',
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    log.warn('probe: import failed', { error: message });
    return {
      ok: false,
      cliExists: false,
      supportsStreamJson: false,
      message: `Claude Agent SDK 不可用: ${message}`,
    };
  }
}

/**
 * Run a single Claude prompt via the Claude Agent SDK, invoking `handlers`
 * as events arrive. Mirrors the contract of `startClaudePrompt()` from
 * `claude-cli-bridge.ts` so callers (e.g. `server.ts`) can swap bridges.
 */
export function startClaudePromptSdk(
  params: StartClaudeSdkParams,
  handlers?: ClaudePromptHandlers,
): RunningSdkPrompt {
  const abortController = new AbortController();
  const startedAt = Date.now();
  log.debug('query: start', {
    cwd: params.cwd,
    model: params.model,
    hasSystemPrompt: Boolean(params.systemPrompt?.trim()),
    messageLen: params.message.length,
  });

  let streamedText = '';
  let streamedThinking = '';
  const rawEvents: unknown[] = [];
  const toolUses: ToolUseInfo[] = [];
  const toolUseBlocks = new Map<number, { id: string; name: string; inputJson: string }>();
  const toolStartEmitted = new Set<string>(); // dedup: toolUseId → already emitted onToolStart
  const toolEndEmitted = new Set<string>(); // dedup: toolUseId → already emitted onToolEnd
  let stopReason: string | undefined;
  let lifecycleEmitted = false;

  const emitLifecycleStart = () => {
    if (lifecycleEmitted) return;
    lifecycleEmitted = true;
    handlers?.onLifecycle?.('start');
  };

  const handleStreamEvent = (event: any) => {
    // content_block_start
    if (event?.type === 'content_block_start') {
      const cb = event.content_block;
      const idx = event.index ?? 0;
      if (cb?.type === 'tool_use') {
        toolUseBlocks.set(idx, { id: cb.id, name: cb.name, inputJson: '' });
        log.debug('tool:start', { id: cb.id, name: cb.name });
        // Defer onToolStart until input is fully parsed (content_block_stop)
        // to ensure input is available for consumers.
      }
      handlers?.onContentBlockStart?.({
        index: idx,
        blockType: cb?.type ?? 'text',
        toolCallId: cb?.type === 'tool_use' ? cb.id : undefined,
        name: cb?.type === 'tool_use' ? cb.name : undefined,
      });
      emitLifecycleStart();
      return;
    }

    // content_block_delta
    if (event?.type === 'content_block_delta') {
      const delta = event.delta;
      if (delta?.type === 'text_delta') {
        const text = String(delta.text || '');
        if (text) {
          streamedText += text;
          handlers?.onTextDelta?.(streamedText, text);
        }
      }
      if (delta?.type === 'thinking_delta') {
        const text = String(delta.thinking || '');
        if (text) {
          streamedThinking += text;
          handlers?.onThinkingDelta?.(streamedThinking, text);
        }
      }
      if (delta?.type === 'input_json_delta') {
        const idx = event.index ?? 0;
        const tracker = toolUseBlocks.get(idx);
        if (tracker) {
          tracker.inputJson += delta.partial_json || '';
          handlers?.onToolUpdate?.(tracker.id, tracker.inputJson);
        }
      }
      return;
    }

    // content_block_stop
    if (event?.type === 'content_block_stop') {
      const idx = event.index ?? 0;
      const tracker = toolUseBlocks.get(idx);
      const blockType = tracker ? 'tool_use' : 'text';
      if (tracker) {
        toolUseBlocks.delete(idx);
        let input: Record<string, unknown> = {};
        try { input = JSON.parse(tracker.inputJson || '{}'); } catch { /* ignore */ }
        const toolInfo: ToolUseInfo = { id: tracker.id, name: tracker.name, input };
        toolUses.push(toolInfo);
        log.debug('tool:end', { id: tracker.id, name: tracker.name, inputKeys: Object.keys(input) });
        // TodoWrite → dedicated onTodoUpdate callback
        if (tracker.name === 'TodoWrite' && Array.isArray(input.todos)) {
          const todos = (input.todos as Array<Record<string, unknown>>)
            .filter(t => t && typeof t.content === 'string')
            .map(t => ({
              content: String(t.content),
              status: (t.status === 'pending' || t.status === 'in_progress' || t.status === 'completed') ? t.status as 'pending' | 'in_progress' | 'completed' : 'pending' as const,
              activeForm: typeof t.activeForm === 'string' ? String(t.activeForm) : String(t.content),
            }));
          handlers?.onTodoUpdate?.(todos, tracker.id);
        }
        // Emit onToolStart with full input before onToolEnd
        if (!toolStartEmitted.has(tracker.id)) {
          toolStartEmitted.add(tracker.id);
          handlers?.onToolStart?.(toolInfo);
        }
        if (!toolEndEmitted.has(tracker.id)) {
          toolEndEmitted.add(tracker.id);
          handlers?.onToolEnd?.(toolInfo);
        }
      }
      handlers?.onContentBlockStop?.({ index: idx, blockType });
      return;
    }

    // message_start
    if (event?.type === 'message_start') {
      const msg = event.message;
      handlers?.onMessageStart?.({
        messageId: msg?.id,
        model: msg?.model,
        usage: msg?.usage ? {
          inputTokens: msg.usage.input_tokens,
          outputTokens: msg.usage.output_tokens,
        } : undefined,
      });
      return;
    }

    // message_stop
    if (event?.type === 'message_stop') {
      handlers?.onMessageStop?.();
      return;
    }

    // message_delta (stop_reason, usage)
    if (event?.type === 'message_delta') {
      if (typeof event?.delta?.stop_reason === 'string') stopReason = event.delta.stop_reason;
      if (event?.usage) {
        handlers?.onUsage?.({
          inputTokens: event.usage.input_tokens,
          outputTokens: event.usage.output_tokens,
          cacheReadTokens: event.usage.cache_read_input_tokens,
          cacheCreationTokens: event.usage.cache_creation_input_tokens,
        });
      }
    }
  };

  const completed: Promise<ClaudePromptResult> = (async () => {
    let mod: any;
    try {
      mod = await import('@anthropic-ai/claude-agent-sdk');
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log.error('query: SDK import failed', { error: message });
      handlers?.onLifecycle?.('error', { error: message });
      return { ok: false, text: '', rawEvents, toolUses, error: `SDK not installed: ${message}` };
    }

    // Generate runId and sessionKey if not provided
    const runId = params.runId || `run:${Math.random().toString(36).slice(2)}`;
    const sessionKey = params.sessionKey || `session:${Math.random().toString(36).slice(2)}`;

    const claudeHomeOverride = process.env.RELAY_CLAUDE_HOME?.trim();
    const claudeConfigDirOverride = process.env.RELAY_CLAUDE_CONFIG_DIR?.trim();
    const modelProviderEnv = loadRelayModelProviderEnv();
    const options: Record<string, unknown> = {
      cwd: params.cwd,
      env: {
        ...process.env,
        ...modelProviderEnv,
        ...(claudeHomeOverride ? { HOME: claudeHomeOverride } : {}),
        ...(claudeConfigDirOverride ? { CLAUDE_CONFIG_DIR: claudeConfigDirOverride } : {}),
        ...(params.env ?? {}),
      },
      includePartialMessages: true,
      abortController,
      ...(params.sdkOptions ?? {}),
    };
    if (params.additionalDirectories?.length) {
      const cleaned = params.additionalDirectories.filter(p => typeof p === 'string' && p.trim()).map(p => p.trim());
      if (cleaned.length) options.additionalDirectories = cleaned;
    }

    log.debug('query: sdk params', {
      runId,
      sessionKey,
      permissionMode: params.permissionMode,
      model: params.model,
      hasOnInteractionRequested: Boolean(params.onInteractionRequested),
    });

    // Inject canUseTool hook for HITL suspend/resume
    if (params.onInteractionRequested) {
      // EXEC_APPROVAL_TIMEOUT_MS 统一从 interaction/builders 导出（可通过
      // RELAY_INTERACTION_TIMEOUT_MS 配置，默认 5min），避免多处硬编码。
      // 排查日志：确认 canUseTool 生效的审批超时值
      log.debug('canUseTool: interaction approval timeout in effect', { timeoutMs: EXEC_APPROVAL_TIMEOUT_MS });

      // SDK canUseTool signature: (toolName, input, options: { signal, suggestions?, agentID?, toolUseID, ... })
      options.canUseTool = async (
        toolName: string,
        input: Record<string, unknown>,
        canUseToolOptions: { signal: AbortSignal; suggestions?: unknown[]; agentID?: string; toolUseID?: string },
      ): Promise<PermissionResult> => {
        log.debug('canUseTool: invoked', { toolName, runId, sessionKey, agentID: canUseToolOptions.agentID });

        // Only gate controlled tools; let others pass through
        if (!shouldGateTool(toolName)) {
          log.debug('canUseTool: allowing non-gated tool', { toolName });
          return { behavior: 'allow' };
        }

        const interactionId = `int:${crypto.randomUUID()}`;
        const createdAtMs = Date.now();
        const expiresAtMs = createdAtMs + EXEC_APPROVAL_TIMEOUT_MS;

        // Build agentContext from SDK-provided agentID when running inside a subagent
        const agentContext: import('./types.js').AgentContext | undefined = canUseToolOptions.agentID
          ? { agentId: canUseToolOptions.agentID }
          : undefined;

        log.debug('canUseTool: suspending for approval', {
          interactionId,
          runId,
          sessionKey,
          toolName,
          inputKeys: Object.keys(input),
          hasSuggestions: Boolean(canUseToolOptions.suggestions),
          hasAgentContext: Boolean(agentContext),
        });

        // Create a promise that will be resolved when user approves/denies
        const permissionPromise = new Promise<PermissionResult>((resolve, reject) => {
          log.debug('canUseTool: creating permissionPromise', { interactionId, runId, sessionKey });

          const wait: PendingToolWait = {
            interactionId,
            runId,
            sessionKey,
            toolName,
            originalInput: input,
            resolve,
            reject,
            createdAtMs,
            expiresAtMs,
            signal: canUseToolOptions.signal,
            resolved: false,
            suggestions: canUseToolOptions.suggestions,
          };

          pendingToolWaits.set(interactionId, wait);
          log.debug('canUseTool: wait registered', { interactionId, pendingCount: pendingToolWaits.size });

          // Emit the interaction request event to gateway
          params.onInteractionRequested?.({
            interactionId,
            runId,
            sessionKey,
            toolName,
            toolCallId: canUseToolOptions.toolUseID,
            input,
            createdAtMs,
            expiresAtMs,
            suggestions: canUseToolOptions.suggestions,
            agentContext,
          });

          // Handle abort signal
          if (canUseToolOptions.signal) {
            const onAbort = () => {
              log.debug('canUseTool: abort signal received', { interactionId, runId, signalAborted: canUseToolOptions.signal?.aborted });
              rejectToolApproval(interactionId, new Error('Aborted'));
            };
            canUseToolOptions.signal.addEventListener('abort', onAbort, { once: true });
            // Also check if already aborted
            if (canUseToolOptions.signal.aborted) {
              log.debug('canUseTool: signal already aborted on setup', { interactionId, runId });
              rejectToolApproval(interactionId, new Error('Aborted'));
            }
          } else {
            log.warn('canUseTool: no signal provided', { interactionId, runId });
          }

          // Set up timeout cleanup
          setTimeout(() => {
            if (pendingToolWaits.has(interactionId)) {
              log.warn('canUseTool: timeout expired', { interactionId });
              rejectToolApproval(interactionId, new Error('Interaction expired'));
            }
          }, EXEC_APPROVAL_TIMEOUT_MS);
        });

        try {
          log.debug('canUseTool: awaiting permissionPromise', { interactionId });
          const result = await permissionPromise;
          const isAllowResult = result.behavior === 'allow';
          log.debug('canUseTool: resumed with result', { interactionId, behavior: result.behavior, hasUpdatedInput: isAllowResult && Boolean(result.updatedInput) });
          return result;
        } catch (err) {
          log.warn('canUseTool: rejected with error', { interactionId, error: (err as Error).message });
          throw err;
        }
      };
    }

    // Use explicit CLI path if provided (useful when SDK's bundled binary is unavailable)
    const claudePath = await getClaudeCodePath();
    if (claudePath) {
      options.pathToClaudeCodeExecutable = claudePath;
    }
    const appendParts: string[] = [];
    if (claudeHomeOverride) {
      appendParts.push(
        `Your working directory (PWD) is exactly: ${params.cwd}\n` +
        `Your HOME environment variable is exactly: ${claudeHomeOverride}\n` +
        'You are NOT running on /home/admin or any typical default location.\n' +
        'For any question about paths, environment variables, or "where are you", ' +
        'always verify with a tool call (`pwd`, `echo $VAR`, `ls`) rather than ' +
        'answering from prior knowledge or guessing based on conventions.',
      );
    }
    if (params.systemPrompt?.trim()) appendParts.push(params.systemPrompt.trim());
    if (appendParts.length) options.appendSystemPrompt = appendParts.join('\n\n');
    if (params.model) options.model = params.model;
    if (params.permissionMode) {
      options.permissionMode = params.permissionMode;
      if (params.permissionMode === 'bypassPermissions') {
        log.debug('the mode is bypassPermissions')
        // options.allowDangerouslySkipPermissions = true;
      }
    }
    log.debug('query: final sdk options', {
      runId,
      sessionKey,
      permissionMode: options.permissionMode,
      hasCanUseTool: typeof options.canUseTool === 'function',
      optionKeys: Object.keys(options),
    });
    // Session ID strategy: extract UUID from sessionKey and use as SDK session ID
    // so that gateway sessionKey and SDK sessionId are always consistent.
    // This eliminates the mapping problem where a lost binding would cause
    // "No conversation found" errors on resume.
    if (params.resumeSessionId) {
      if (params.isNewSession) {
        // First request for this session: set sessionId so the SDK creates a
        // new session with the specified UUID (extracted from sessionKey).
        options.sessionId = params.resumeSessionId;
        log.warn('query: sessionKey vs SDK sessionId', {
          sessionKey: params.sessionKey ?? 'none',
          sdkSessionId: params.resumeSessionId,
          mode: 'new (options.sessionId)',
        });
      } else {
        // Resume an existing SDK session using the UUID extracted from sessionKey
        options.resume = params.resumeSessionId;
        log.warn('query: sessionKey vs SDK sessionId', {
          sessionKey: params.sessionKey ?? 'none',
          sdkSessionId: params.resumeSessionId,
          mode: 'resume (options.resume)',
        });
      }
    } else if (params.sessionKey) {
      // Fallback: extract UUID from sessionKey and use as sessionId
      const derivedSessionId = extractUuidFromSessionKey(params.sessionKey);
      if (derivedSessionId) {
        options.sessionId = derivedSessionId;
        log.warn('query: sessionKey vs SDK sessionId', {
          sessionKey: params.sessionKey,
          sdkSessionId: derivedSessionId,
          mode: 'fallback (options.sessionId from sessionKey UUID)',
        });
      }
    }

    let sdkSessionId: string | undefined;

    let iter: AsyncIterable<any>;
    try {
      log.debug('query: invoking SDK', { optionKeys: Object.keys(options) });
      iter = mod.query({ prompt: params.message, options });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log.error('query: invocation threw', {
        error: message,
        cwd: params.cwd,
        pathToClaudeCodeExecutable: options.pathToClaudeCodeExecutable ?? null,
        ...(isBinaryNotFoundError(message)
          ? { binaryDiagnostics: await collectClaudePathDiagnostics() }
          : {}),
      });
      handlers?.onLifecycle?.('error', { error: message });
      return { ok: false, text: streamedText, rawEvents, toolUses, error: message };
    }

    let msgCount = 0;
    try {
      for await (const msg of iter) {
        rawEvents.push(msg);
        msgCount++;

        // SDK emits system init first — treat as lifecycle start.
        // When hooks or plugins are configured, the CLI may emit multiple
        // system events with different session_ids. Only the last one
        // (emitted after all hooks complete) carries the real session_id.
        // Always overwrite so we end up with the correct value.
        if (msg?.type === 'system') {
          if (typeof msg.session_id === 'string') {
            const prev = sdkSessionId;
            sdkSessionId = msg.session_id;
            const derivedId = params.sessionKey ? extractUuidFromSessionKey(params.sessionKey) : null;
            log.warn('query: SDK returned session_id', {
              sdkSessionId,
              previousSdkSessionId: prev ?? 'none',
              sessionKey: params.sessionKey ?? 'none',
              resumeSessionId: params.resumeSessionId ?? 'none',
              derivedFromSessionKey: derivedId ?? 'none',
              match: derivedId && sdkSessionId === derivedId ? 'YES' : 'NO',
              overwritten: prev != null && prev !== sdkSessionId,
            });
          }
          if (!lifecycleEmitted) {
            lifecycleEmitted = true;
            handlers?.onLifecycle?.('start', {
              sessionId: msg.session_id,
              cwd: msg.cwd,
              tools: msg.tools,
            });
          }
          continue;
        }

        // Partial assistant stream events (matches CLI `stream_event` wrapper)
        if (msg?.type === 'stream_event') {
          handleStreamEvent(msg.event);
          continue;
        }

        // Final result message
        if (msg?.type === 'result') {
          if (typeof msg.stop_reason === 'string') stopReason = msg.stop_reason;
          // result.session_id is authoritative — always overwrite
          if (typeof msg.session_id === 'string') {
            const prev = sdkSessionId;
            sdkSessionId = msg.session_id;
            if (prev !== sdkSessionId) {
              const derivedId = params.sessionKey ? extractUuidFromSessionKey(params.sessionKey) : null;
              log.warn('query: SDK returned session_id (from result)', {
                sdkSessionId,
                previousSdkSessionId: prev ?? 'none',
                sessionKey: params.sessionKey ?? 'none',
                resumeSessionId: params.resumeSessionId ?? 'none',
                derivedFromSessionKey: derivedId ?? 'none',
                match: derivedId && sdkSessionId === derivedId ? 'YES' : 'NO',
              });
            }
          }
          if (msg.usage) {
            handlers?.onUsage?.({
              inputTokens: msg.usage.input_tokens ?? msg.usage.inputTokens,
              outputTokens: msg.usage.output_tokens ?? msg.usage.outputTokens,
              cacheReadTokens: msg.usage.cache_read_input_tokens ?? msg.usage.cacheReadTokens,
              cacheCreationTokens: msg.usage.cache_creation_input_tokens ?? msg.usage.cacheCreationTokens,
            });
          }
          if (msg.costUsd != null || msg.durationMs != null || msg.numTurns != null) {
            handlers?.onCost?.({
              costUsd: msg.costUsd,
              durationMs: msg.durationMs,
              numTurns: msg.numTurns,
            });
          }
          // Fallback text if partial streaming didn't run
          if (!streamedText && typeof msg.result === 'string') {
            streamedText = msg.result;
          }
          continue;
        }

        // Complete assistant message — fallback when partials are disabled
        if (msg?.type === 'assistant') {
          if (!streamedText) {
            const content = Array.isArray(msg.message?.content) ? msg.message.content : [];
            for (const part of content) {
              if (part?.type === 'text' && typeof part.text === 'string') {
                streamedText += part.text;
              }
            }
          }
          continue;
        }

        if (msg?.type === 'tool_progress') {
          handlers?.onToolProgress?.({
            toolCallId: msg.tool_use_id ?? '',
            toolName: msg.tool_name ?? '',
            parentToolUseId: msg.parent_tool_use_id ?? null,
            elapsedSeconds: msg.elapsed_time_seconds ?? 0,
            taskId: msg.task_id,
          });
          log.debug('tool_progress', { toolUseId: msg.tool_use_id, elapsed: msg.elapsed_time_seconds });
          continue;
        }

        if (msg?.type === 'tool_use_summary') {
          handlers?.onToolSummary?.({
            summary: msg.summary ?? '',
            precedingToolUseIds: (msg.preceding_tool_use_ids ?? []) as string[],
          });
          log.debug('tool_use_summary', { summary: (msg.summary as string)?.slice(0, 80) });
          continue;
        }

        // Task events: task_started, task_progress, task_notification
        if (msg?.type === 'task_started' || msg?.type === 'task_progress' || msg?.type === 'task_notification') {
          handlers?.onTaskEvent?.({
            type: msg.type,
            taskId: msg.task_id ?? msg.taskId ?? '',
            toolUseId: msg.tool_use_id ?? msg.toolUseId,
            status: msg.status,
            description: msg.description,
            summary: msg.summary,
            outputFile: msg.output_file ?? msg.outputFile,
            usage: msg.usage,
            taskType: msg.task_type,
            workflowName: msg.workflow_name,
            prompt: msg.prompt,
            lastToolName: msg.last_tool_name,
          });
          log.debug('task:event', { type: msg.type, taskId: msg.task_id ?? msg.taskId });
          continue;
        }

        // System subtypes
        if (msg?.type === 'system') {
          const subtype = msg.subtype as string | undefined;
          if (subtype === 'status') {
            handlers?.onSystemEvent?.({
              type: 'status_change',
              status: msg.status ?? null,
              compactResult: msg.compact_result ?? null,
              compactError: msg.compact_error ?? null,
            });
            log.debug('system:status', { status: msg.status });
          } else if (subtype === 'api_retry') {
            handlers?.onSystemEvent?.({
              type: 'api_retry',
              attempt: msg.attempt ?? 0,
              maxRetries: msg.max_retries ?? 0,
              retryDelayMs: msg.retry_delay_ms ?? 0,
              errorStatus: msg.error_status ?? null,
              error: msg.error ?? '',
            });
            log.debug('system:api_retry', { attempt: msg.attempt });
          } else if (subtype === 'compact_boundary') {
            const meta = msg.compact_meta as Record<string, unknown> | undefined;
            handlers?.onSystemEvent?.({
              type: 'compact_boundary',
              trigger: (meta?.trigger as string) ?? 'auto',
              preTokens: (meta?.pre_tokens as number) ?? 0,
              postTokens: meta?.post_tokens as number | undefined,
              durationMs: meta?.duration_ms as number | undefined,
              compactedTurns: 0,
            });
            log.debug('system:compact_boundary');
          } else if (subtype === 'files_persisted') {
            handlers?.onSystemEvent?.({
              type: 'files_persisted',
              files: Array.isArray(msg.files) ? msg.files : [],
              failed: Array.isArray(msg.failed) ? msg.failed : [],
              processedAt: (msg.processed_at as string) ?? new Date().toISOString(),
            });
            log.debug('system:files_persisted');
          } else if (subtype === 'memory_recall') {
            handlers?.onMemoryRecall?.({
              mode: (msg.mode as string) ?? 'select',
              memories: Array.isArray(msg.memories) ? msg.memories : [],
            });
            log.debug('system:memory_recall', { mode: msg.mode });
          } else if (subtype === 'notification') {
            handlers?.onNotification?.({
              key: (msg.key as string) ?? '',
              text: (msg.text as string) ?? '',
              priority: (msg.priority as string) ?? 'medium',
              color: msg.color as string | undefined,
              timeoutMs: msg.timeout_ms as number | undefined,
            });
            log.debug('system:notification', { key: msg.key, priority: msg.priority });
          } else if (subtype === 'task_updated') {
            handlers?.onTaskEvent?.({
              type: 'task_updated',
              taskId: (msg.task_id as string) ?? '',
              patch: msg.patch as Record<string, unknown> | undefined,
            });
            log.debug('system:task_updated', { taskId: msg.task_id });
          }
          continue;
        }

        if (msg?.type === 'prompt_suggestion') {
          handlers?.onPromptSuggestion?.({
            suggestion: (msg.suggestion as string) ?? '',
          });
          log.debug('prompt_suggestion', { suggestion: (msg.suggestion as string)?.slice(0, 60) });
          continue;
        }

        if (msg?.type === 'rate_limit_event') {
          const info = msg.rate_limit_info as Record<string, unknown> | undefined;
          handlers?.onSystemEvent?.({
            type: 'rate_limit',
            status: info?.status ?? 'allowed',
            rateLimitType: info?.rateLimitType as string | undefined,
            utilization: info?.utilization as number | undefined,
            resetsAt: info?.resetsAt as number | undefined,
            overageStatus: info?.overageStatus as string | undefined,
            overageResetsAt: info?.overageResetsAt as number | undefined,
          });
          log.debug('rate_limit_event', { status: info?.status });
          continue;
        }

        if (msg?.type === 'user') {
          const content = Array.isArray(msg.message?.content) ? msg.message.content : [];

          const directToolUseResult = (msg && typeof msg === 'object' && 'tool_use_result' in msg)
            ? msg.tool_use_result as Record<string, unknown> | string | undefined
            : undefined;
          const toolResultBlock = content.find((part: any) => part?.type === 'tool_result');
          const toolUseId = toolResultBlock?.tool_use_id ?? toolResultBlock?.toolUseId;
          const rawResult = (toolResultBlock && typeof toolResultBlock === 'object' && 'content' in toolResultBlock)
            ? toolResultBlock.content
            : directToolUseResult;

          const normalizeToolResult = (value: unknown): { output: string; meta?: CommandOutputMeta } | null => {
            if (typeof value === 'string') return { output: value };
            if (!value || typeof value !== 'object') return null;
            const rec = value as Record<string, unknown>;
            const stdout = typeof rec.stdout === 'string' ? rec.stdout : '';
            const stderr = typeof rec.stderr === 'string' ? rec.stderr : '';
            const output = [ stdout, stderr ].filter(Boolean).join(stdout && stderr ? '\n' : '');
            const meta: CommandOutputMeta = {
              exitCode: typeof rec.exitCode === 'number' ? rec.exitCode : typeof rec.exit_code === 'number' ? rec.exit_code : null,
              durationMs: typeof rec.durationMs === 'number' ? rec.durationMs : typeof rec.duration_ms === 'number' ? rec.duration_ms : undefined,
              cwd: typeof rec.cwd === 'string' ? rec.cwd : undefined,
            };
            return { output: output || JSON.stringify(rec), meta };
          };

          const normalized = normalizeToolResult(rawResult);
          if (toolUseId && normalized) {
            handlers?.onCommandOutput?.(String(toolUseId), 'end', normalized.output, normalized.meta);
            log.debug('command_output:end', {
              toolCallId: String(toolUseId),
              outputLen: normalized.output.length,
              exitCode: normalized.meta?.exitCode,
              durationMs: normalized.meta?.durationMs,
              cwd: normalized.meta?.cwd,
            });
          }
          continue;
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const aborted = abortController.signal.aborted;
      log.error('query: iteration failed', {
        aborted,
        error: message,
        durationMs: Date.now() - startedAt,
        cwd: params.cwd,
        pathToClaudeCodeExecutable: options.pathToClaudeCodeExecutable ?? null,
        ...(!aborted && isBinaryNotFoundError(message)
          ? { binaryDiagnostics: await collectClaudePathDiagnostics() }
          : {}),
      });
      handlers?.onLifecycle?.('error', { error: aborted ? 'aborted' : message });
      return {
        ok: false,
        text: streamedText,
        rawEvents,
        toolUses,
        error: aborted ? 'aborted by signal SIGTERM' : message,
        stopReason: aborted ? 'cancelled' : stopReason,
        sdkSessionId,
      };
    }

    log.debug('query: done', {
      msgCount,
      toolUses: toolUses.length,
      textLen: streamedText.length,
      stopReason,
      sdkSessionId,
      durationMs: Date.now() - startedAt,
    });
    handlers?.onLifecycle?.('end', { stopReason });
    return { ok: true, text: streamedText.trim(), rawEvents, toolUses, stopReason, sdkSessionId };
  })();

  return {
    completed,
    abort: () => {
      log.debug('abort: requested', { runId: params.runId });
      // Abort the SDK first — this triggers the abort signal listener in canUseTool
      abortController.abort();
      // Also reject any pending tool waits for this run (in case abort signal didn't catch them)
      // rejectToolApproval checks if already resolved, so duplicate calls are safe
      for (const [ id, wait ] of pendingToolWaits) {
        if (wait.runId === params.runId && !wait.resolved) {
          rejectToolApproval(id, new Error('Aborted'));
        }
      }
    },
  };
}
