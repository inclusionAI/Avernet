import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createLogger } from './debug.js';
import { loadRelayModelProviderEnv } from './model-provider-settings.js';

const log = createLogger('cli');

export type ClaudeHealth = {
  ok: boolean;
  cliExists: boolean;
  supportsStreamJson: boolean;
  message: string;
};

export type ToolUseInfo = {
  id: string;
  name: string;
  input: Record<string, unknown>;
};

export type ClaudePromptResult = {
  ok: boolean;
  text: string;
  rawEvents: unknown[];
  toolUses: ToolUseInfo[];
  error?: string;
  stopReason?: string;
  /** Claude Agent SDK session ID for resuming conversations (SDK bridge only). */
  sdkSessionId?: string;
};

export type RunningClaudePrompt = {
  /**
   * Child process handle. Present when the CLI bridge is used; absent when
   * the SDK bridge runs in-process (see `claude-sdk-bridge.ts`). Treat as
   * optional at consumer call sites.
   */
  child?: ChildProcessWithoutNullStreams;
  completed: Promise<ClaudePromptResult>;
  abort: () => void;
};

export type ClaudePromptHandlers = {
  onTextDelta?: (fullText: string, delta: string) => void;
  onThinkingDelta?: (fullText: string, delta: string) => void;
  onToolStart?: (tool: ToolUseInfo) => void;
  onToolUpdate?: (toolCallId: string, partialJson: string) => void;
  onToolEnd?: (tool: ToolUseInfo) => void;
  onCommandOutput?: (toolCallId: string, phase: 'delta' | 'end', output: string, meta?: { exitCode?: number | null; durationMs?: number; cwd?: string }) => void;
  onLifecycle?: (phase: 'start' | 'end' | 'error', data?: Record<string, unknown>) => void;
  onUsage?: (usage: { inputTokens?: number; outputTokens?: number; cacheReadTokens?: number; cacheCreationTokens?: number }) => void;
  onMessageStart?: (data: { messageId?: string; model?: string; usage?: { inputTokens?: number; outputTokens?: number } }) => void;
  onMessageStop?: () => void;
  onContentBlockStart?: (data: { index: number; blockType: string; toolCallId?: string; name?: string }) => void;
  onContentBlockStop?: (data: { index: number; blockType: string }) => void;
  onCost?: (data: { costUsd?: number; durationMs?: number; numTurns?: number }) => void;
  /** Task 事件 (task_started / task_progress / task_notification / task_updated) */
  onTaskEvent?: (event: {
    type: 'task_started' | 'task_progress' | 'task_notification' | 'task_updated';
    taskId: string;
    toolUseId?: string;
    status?: 'pending' | 'running' | 'completed' | 'failed' | 'stopped' | 'killed';
    description?: string;
    summary?: string;
    outputFile?: string;
    usage?: { totalTokens: number; toolUses: number; durationMs: number };
    taskType?: string;
    workflowName?: string;
    prompt?: string;
    lastToolName?: string;
    patch?: Record<string, unknown>;
  }) => void;
  /** TodoWrite tool call — full replacement todo list. */
  onTodoUpdate?: (todos: Array<{ content: string; status: 'pending' | 'in_progress' | 'completed'; activeForm: string }>, toolCallId?: string) => void;
  /** Tool progress event — long-running tool heartbeat. */
  onToolProgress?: (data: { toolCallId: string; toolName: string; parentToolUseId: string | null; elapsedSeconds: number; taskId?: string }) => void;
  /** Tool use summary — aggregated summary after tool execution. */
  onToolSummary?: (data: { summary: string; precedingToolUseIds: string[] }) => void;
  /** System events (status_change, api_retry, rate_limit, compact_boundary, files_persisted). */
  onSystemEvent?: (data: Record<string, unknown>) => void;
  /** Memory recall event. */
  onMemoryRecall?: (data: { mode: string; memories: unknown[] }) => void;
  /** Notification event. */
  onNotification?: (data: { key: string; text: string; priority: string; color?: string; timeoutMs?: number }) => void;
  /** Prompt suggestion event. */
  onPromptSuggestion?: (data: { suggestion: string }) => void;
};

export async function probeClaudeCli(): Promise<ClaudeHealth> {
  const help = await runProcess('claude', [ '--help' ]);
  if (!help.ok) {
    return { ok: false, cliExists: false, supportsStreamJson: false, message: help.error || 'claude command not found' };
  }
  const text = `${help.stdout}\n${help.stderr}`;
  const supportsStreamJson = text.includes('stream-json');
  return {
    ok: supportsStreamJson,
    cliExists: true,
    supportsStreamJson,
    message: supportsStreamJson ? 'Claude CLI 可用，支持 stream-json' : 'Claude CLI 已安装，但未检测到 stream-json 支持',
  };
}

export function startClaudePrompt(
  params: {
    cwd: string;
    message: string;
    systemPrompt?: string;
    model?: string;
    permissionMode?: string;
    env?: Record<string, string>;
    /**
     * Extra directories beyond `cwd` to expose to Claude — emitted as one
     * `--add-dir <path>` flag per entry, matching `claude` CLI semantics.
     */
    additionalDirectories?: string[];
    /** Session id from a previous run to resume. Wired to `claude --resume <id>`. */
    resumeSessionId?: string;
    /** Whether this is a new session (unused by CLI bridge, for SDK bridge compatibility). */
    isNewSession?: boolean;
  },
  handlers?: ClaudePromptHandlers,
): RunningClaudePrompt {
  const args = [ '-p', '--output-format', 'stream-json', '--include-partial-messages', '--verbose' ];
  const cliHomeOverride = process.env.RELAY_CLAUDE_HOME?.trim();
  const appendParts: string[] = [];
  if (cliHomeOverride) {
    appendParts.push(
      `Your working directory (PWD) is exactly: ${params.cwd}\n` +
      `Your HOME environment variable is exactly: ${cliHomeOverride}\n` +
      'You are NOT running on /home/admin or any typical default location.\n' +
      'For any question about paths, environment variables, or "where are you", ' +
      'always verify with a tool call (`pwd`, `echo $VAR`, `ls`) rather than ' +
      'answering from prior knowledge or guessing based on conventions.',
    );
  }
  if (params.systemPrompt?.trim()) appendParts.push(params.systemPrompt.trim());
  if (appendParts.length) {
    args.push('--append-system-prompt', appendParts.join('\n\n'));
  }
  if (params.model) {
    args.push('--model', params.model);
  }
  if (params.additionalDirectories?.length) {
    for (const dir of params.additionalDirectories) {
      if (dir && dir.trim()) args.push('--add-dir', dir.trim());
    }
  }
  if (params.resumeSessionId) {
    args.push('--resume', params.resumeSessionId);
    log.debug('cli-bridge: resuming session', { sessionId: params.resumeSessionId });
  }
  if (params.permissionMode) {
    if (params.permissionMode === 'default') {
      // default mode = no flag; Claude CLI prompts interactively for each tool
      log.debug('cli-bridge: permissionMode=default, no flag added');
    } else {
      // args.push('--permission-mode', params.permissionMode);
      log.debug('cli-bridge: added --permission-mode flag', { permissionMode: params.permissionMode });
    }
  }
  args.push(params.message);

  const startedAt = Date.now();
  log.debug('spawn', {
    cwd: params.cwd,
    hasSystemPrompt: Boolean(params.systemPrompt?.trim()),
    messageLen: params.message.length,
  });
  const claudeHomeOverride = process.env.RELAY_CLAUDE_HOME?.trim();
  const claudeConfigDirOverride = process.env.RELAY_CLAUDE_CONFIG_DIR?.trim();
  const modelProviderEnv = loadRelayModelProviderEnv();
  const spawnEnv = {
    ...process.env,
    ...modelProviderEnv,
    ...(claudeHomeOverride ? { HOME: claudeHomeOverride } : {}),
    ...(claudeConfigDirOverride ? { CLAUDE_CONFIG_DIR: claudeConfigDirOverride } : {}),
    ...(params.env ?? {}),
  };
  const child = spawn('claude', args, {
    cwd: params.cwd,
    env: spawnEnv,
    stdio: [ 'pipe', 'pipe', 'pipe' ],
  });

  let stdout = '';
  let stderr = '';
  let stdoutBuffer = '';
  let streamedText = '';
  let streamedThinking = '';
  const rawEvents: unknown[] = [];
  const toolUses: ToolUseInfo[] = [];
  let stopReason: string | undefined;
  let sdkSessionId: string | undefined;
  const toolUseBlocks = new Map<number, { id: string; name: string; inputJson: string }>();
  let lifecycleEmitted = false;

  const handleLine = (line: string) => {
    const trimmed = line.trim();
    if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) return;
    try {
      const evt = JSON.parse(trimmed);
      rawEvents.push(evt);

      // -- Top-level events (non-stream_event) --
      if (evt?.type === 'system') {
        if (typeof evt.session_id === 'string' && evt.session_id) {
          sdkSessionId = evt.session_id;
        }
        if (!lifecycleEmitted) {
          lifecycleEmitted = true;
          handlers?.onLifecycle?.('start', {
            sessionId: evt.session_id,
            cwd: evt.cwd,
            tools: evt.tools,
          });
        }
        return;
      }

      if (evt?.type === 'result') {
        if (typeof evt?.stop_reason === 'string') {
          stopReason = evt.stop_reason;
        }
        if (typeof evt?.session_id === 'string' && evt.session_id) {
          sdkSessionId = evt.session_id;
        }
        if (typeof evt?.usage === 'object' && evt.usage) {
          handlers?.onUsage?.({
            inputTokens: evt.usage.input_tokens ?? evt.usage.inputTokens,
            outputTokens: evt.usage.output_tokens ?? evt.usage.outputTokens,
            cacheReadTokens: evt.usage.cache_read_input_tokens ?? evt.usage.cacheReadTokens,
            cacheCreationTokens: evt.usage.cache_creation_input_tokens ?? evt.usage.cacheCreationTokens,
          });
        }
        if (evt.costUsd != null || evt.durationMs != null || evt.numTurns != null) {
          handlers?.onCost?.({
            costUsd: evt.costUsd,
            durationMs: evt.durationMs,
            numTurns: evt.numTurns,
          });
        }
        return;
      }

      if (evt?.type !== 'stream_event') return;
      const event = evt.event;

      // -- content_block_start --
      if (event?.type === 'content_block_start') {
        const cb = event.content_block;
        const idx = event.index ?? 0;
        if (cb?.type === 'tool_use') {
          toolUseBlocks.set(idx, { id: cb.id, name: cb.name, inputJson: '' });
          const toolInfo = { id: cb.id, name: cb.name, input: {} };
          handlers?.onToolStart?.(toolInfo);
        }
        // Emit content_block start for text/thinking/tool_use
        handlers?.onContentBlockStart?.({
          index: idx,
          blockType: cb?.type ?? 'text',
          toolCallId: cb?.type === 'tool_use' ? cb.id : undefined,
          name: cb?.type === 'tool_use' ? cb.name : undefined,
        });
        if (!lifecycleEmitted) {
          lifecycleEmitted = true;
          handlers?.onLifecycle?.('start');
        }
        return;
      }

      // -- content_block_delta --
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

      // -- content_block_stop --
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
          handlers?.onToolEnd?.(toolInfo);
        }
        handlers?.onContentBlockStop?.({ index: idx, blockType });
        return;
      }

      // -- message_start --
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

      // -- message_stop --
      if (event?.type === 'message_stop') {
        handlers?.onMessageStop?.();
        return;
      }

      // -- message_delta (stop_reason, usage) --
      if (event?.type === 'message_delta') {
        if (typeof event?.delta?.stop_reason === 'string') {
          stopReason = event.delta.stop_reason;
        }
        if (event?.usage) {
          handlers?.onUsage?.({
            inputTokens: event.usage.input_tokens,
            outputTokens: event.usage.output_tokens,
            cacheReadTokens: event.usage.cache_read_input_tokens,
            cacheCreationTokens: event.usage.cache_creation_input_tokens,
          });
        }
        return;
      }
    } catch {
      // ignore malformed line
    }
  };

  child.stdout.on('data', (buf: Buffer) => {
    const chunk = buf.toString();
    stdout += chunk;
    stdoutBuffer += chunk;
    let idx = stdoutBuffer.indexOf('\n');
    while (idx >= 0) {
      const line = stdoutBuffer.slice(0, idx);
      stdoutBuffer = stdoutBuffer.slice(idx + 1);
      handleLine(line);
      idx = stdoutBuffer.indexOf('\n');
    }
  });
  child.stderr.on('data', (buf: Buffer) => { stderr += buf.toString(); });

  const completed = new Promise<ClaudePromptResult>(resolve => {
    child.on('error', err => {
      log.error('child:error', { error: err.message });
      handlers?.onLifecycle?.('error', { error: err.message });
      resolve({ ok: false, text: streamedText, rawEvents, toolUses, error: err.message });
    });
    child.on('close', (code, signal) => {
      if (stdoutBuffer.trim()) handleLine(stdoutBuffer);
      const parsed = parseClaudeOutput(stdout, stderr, rawEvents, streamedText, stopReason);
      const durationMs = Date.now() - startedAt;
      if (signal) {
        log.debug('child:aborted', { signal, durationMs });
        handlers?.onLifecycle?.('error', { error: `aborted by signal ${signal}` });
        resolve({ ok: false, text: parsed.text, rawEvents: parsed.rawEvents, toolUses, error: `aborted by signal ${signal}`, stopReason: 'cancelled', sdkSessionId });
        return;
      }
      if (code !== 0) {
        log.error('child:exit nonzero', { code, durationMs, error: parsed.error });
        handlers?.onLifecycle?.('error', { error: parsed.error || `exit code ${code}` });
        resolve({ ok: false, text: parsed.text, rawEvents: parsed.rawEvents, toolUses, error: parsed.error || `exit code ${code}`, stopReason: parsed.stopReason, sdkSessionId });
        return;
      }
      log.debug('child:close ok', {
        durationMs,
        toolUses: toolUses.length,
        textLen: parsed.text.length,
        stopReason: parsed.stopReason,
        sdkSessionId,
      });
      handlers?.onLifecycle?.('end', { stopReason: parsed.stopReason });
      resolve({ ok: true, text: parsed.text, rawEvents: parsed.rawEvents, toolUses, stopReason: parsed.stopReason, sdkSessionId });
    });
  });

  return {
    child,
    completed,
    abort: () => {
      log.debug('abort: SIGTERM');
      child.kill('SIGTERM');
    },
  };
}

function parseClaudeOutput(
  stdout: string,
  stderr: string,
  existingEvents: unknown[],
  streamedText: string,
  knownStopReason?: string,
): { text: string; rawEvents: unknown[]; stopReason?: string; error?: string } {
  const output = `${stdout}\n${stderr}`
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .filter(line => line.startsWith('{') && line.endsWith('}'));

  const rawEvents: unknown[] = existingEvents.length > 0 ? existingEvents : [];
  const textParts: string[] = streamedText ? [ streamedText ] : [];
  let stopReason = knownStopReason;
  for (const line of output) {
    try {
      const evt = JSON.parse(line);
      if (existingEvents.length === 0) rawEvents.push(evt);
      const type = evt?.type;
      if (type === 'assistant' && textParts.length === 0) {
        const message = evt?.message;
        const content = Array.isArray(message?.content) ? message.content : [];
        for (const part of content) {
          if (part?.type === 'text' && typeof part.text === 'string') {
            textParts.push(part.text);
          }
        }
      }
      if (type === 'result') {
        if (typeof evt?.result === 'string' && textParts.length === 0) {
          textParts.push(evt.result);
        }
        if (typeof evt?.stop_reason === 'string') {
          stopReason = evt.stop_reason;
        }
      }
    } catch {
      // ignore
    }
  }
  return {
    text: textParts.join('').trim(),
    rawEvents,
    stopReason,
    error: stderr.trim() || undefined,
  };
}

async function runProcess(command: string, args: string[]): Promise<{ ok: boolean; stdout: string; stderr: string; error?: string }> {
  return new Promise(resolve => {
    const child = spawn(command, args, { env: process.env, stdio: [ 'ignore', 'pipe', 'pipe' ] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (buf: Buffer) => { stdout += buf.toString(); });
    child.stderr.on('data', (buf: Buffer) => { stderr += buf.toString(); });
    child.on('error', err => resolve({ ok: false, stdout, stderr, error: err.message }));
    child.on('close', code => resolve({ ok: code === 0, stdout, stderr, error: code === 0 ? undefined : `exit code ${code}` }));
  });
}
