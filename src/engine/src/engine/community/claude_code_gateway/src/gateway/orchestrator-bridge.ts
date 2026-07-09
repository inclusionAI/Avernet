// Orchestrator → Gateway event translation.
//
// Subscribes to a RunningOrchestration and maps each normalized event into the
// OpenClaw WebSocket event shapes (chat / agent / interaction.requested).
// When `opts.withApproval` is true, tool-end events for AskUserQuestion /
// ExitPlanMode / Bash|Edit|Write|Read spawn pending interactions via the
// registry.

import { randomUUID } from 'node:crypto';
import type {
  OrchestratorEvent,
  RunningOrchestration,
} from '../chat-orchestrator.js';
import type {
  AgentAssistantData,
  AgentContentBlockData,
  AgentLifecycleData,
  AgentMessageData,
  InteractionQuestion,
  InteractionSubject,
} from '../types.js';
import {
  EXEC_APPROVAL_TIMEOUT_MS,
  buildAskUserInteraction,
  buildExecInteraction,
  buildModeTransitionRequested,
} from '../interaction/builders.js';
import { buildExpiryCallback, emitInteractionRequested } from '../interaction/emitters.js';
import type { PendingInteraction } from '../interaction/types.js';
import type { PendingInteractionRegistry } from '../interaction/registry.js';
import type { ConnectionContext } from './connection-context.js';

// Collected events for history persistence (written on run completion)
export type ToolUseCollected = {
  type: 'tool_use';
  toolCallId: string;
  toolName: string;
  input: Record<string, unknown>;
  title?: string;
  description?: string;
  subject?: InteractionSubject;
};

export type ToolResultCollected = {
  toolName: string;
  toolCallId: string;
  output: string;
  exitCode?: number;
  durationMs?: number;
  isError?: boolean;
  isSynthetic?: boolean;
  title?: string;
  description?: string;
  subject?: InteractionSubject;
};

export type CollectedEvent =
  | { kind: 'assistant_text'; text: string }
  | { kind: 'tool_use'; data: ToolUseCollected }
  | { kind: 'tool_result'; data: ToolResultCollected }
  | { kind: 'thinking'; fullText: string };

export type BridgeOrchestratorFn = (
  ctx: ConnectionContext,
  runId: string,
  sessionKey: string,
  cwd: string,
  running: RunningOrchestration,
  opts?: {
    withApproval?: boolean;
    chatMeta?: { model?: string; permissionMode?: string };
    hitlRuntimeMode?: 'continuation' | 'sdk_suspend_resume';
    /** Incremental callback: fired each time a collected event is ready for persistence. */
    onCollectedEvent?: (event: CollectedEvent) => void;
  },
) => {
  getLastStreamedText: () => string;
  wasInterrupted: () => boolean;
  getCollectedEvents: () => CollectedEvent[];
};

/** Derive subject/title/description from a tool call for history metadata. */
function deriveToolMeta(
  toolName: string,
  toolCallId: string,
  input: Record<string, unknown>,
  cwd?: string,
): Pick<ToolUseCollected, 'title' | 'description' | 'subject'> {
  if (toolName === 'Bash') {
    const command = String(input.command ?? '');
    return {
      title: 'Execute command',
      description: command,
      subject: { type: 'command', toolName: 'Bash', toolCallId, command, cwd },
    };
  }
  if (toolName === 'Edit') {
    const filePath = String(input.file_path ?? '');
    const oldStr = String(input.old_string ?? '');
    const newStr = String(input.new_string ?? '');
    return {
      title: 'Edit file',
      description: filePath,
      subject: { type: 'file', toolName: 'Edit', toolCallId, filePath, old_string: oldStr, new_string: newStr, operation: 'edit' },
    };
  }
  if (toolName === 'Write') {
    const filePath = String(input.file_path ?? '');
    const content = String(input.content ?? '');
    return {
      title: 'Write file',
      description: filePath,
      subject: { type: 'file', toolName: 'Write', toolCallId, filePath, operation: 'create', description: content.slice(0, 100) + (content.length > 100 ? '...' : '') },
    };
  }
  if (toolName === 'Read') {
    const filePath = String(input.file_path ?? '');
    return {
      title: 'Read file',
      description: filePath,
      subject: { type: 'file', toolName: 'Read', toolCallId, filePath, operation: 'read' },
    };
  }
  if (toolName === 'AskUserQuestion') {
    const prompt = String(input.prompt ?? '');
    return {
      title: 'Ask user',
      description: prompt,
      subject: { type: 'tool', toolName, toolCallId },
    };
  }
  if (toolName === 'ExitPlanMode') {
    const fromMode = String(input.fromMode ?? 'plan');
    const toMode = String(input.toMode ?? 'execute');
    const summary = String(input.plan ?? input.summary ?? '');
    return {
      title: 'Exit plan mode',
      description: summary,
      subject: { type: 'mode', toolName, toolCallId, fromMode, toMode },
    };
  }
  return {
    title: toolName,
    description: '',
    subject: { type: 'tool', toolName, toolCallId },
  };
}

export function createOrchestratorBridge(registry: PendingInteractionRegistry): BridgeOrchestratorFn {
  return function bridgeOrchestratorToGateway(
    ctx,
    runId,
    sessionKey,
    cwd,
    running,
    opts = {},
  ) {
    let lastStreamedText = '';
    let interrupted = false;
    const collectedEvents: CollectedEvent[] = [];
    let currentThinkingText = '';
    let textBlockStart = 0; // position in lastStreamedText where current text block began
    let currentAgentMode: 'plan' | 'execute' = opts.chatMeta?.permissionMode === 'plan' ? 'plan' : 'execute';

    // Helper: push a collected event and immediately notify the incremental callback
    const pushEvent = (event: CollectedEvent) => {
      collectedEvents.push(event);
      opts.onCollectedEvent?.(event);
    };

    const interruptForInteraction = () => {
      interrupted = true;
      running.abort();
    };

    // Emit phase change event
    const emitPhaseChange = (newMode: 'plan' | 'execute') => {
      if (newMode === currentAgentMode) return;
      const oldMode = currentAgentMode;
      currentAgentMode = newMode;
      ctx.agentEvent(runId, sessionKey, 'phase', {
        phase: 'changed',
        fromPhase: oldMode,
        toPhase: newMode,
        timestamp: Date.now(),
      } as unknown as Record<string, unknown>);
    };

    const suppressToolStartIds = new Set<string>();
    const suppressBlockIndices = new Set<number>();
    // Track content block types by index to correctly identify text blocks
    // (bridge may report thinking blocks as 'text' in contentBlockStop)
    const blockTypeMap = new Map<number, string>();

    running.subscribe((event: OrchestratorEvent) => {
      if (interrupted) return;

      switch (event.kind) {
        case 'textDelta': {
          lastStreamedText = event.fullText;
          ctx.chatEvent(runId, sessionKey, {
            state: 'delta',
            delta: event.delta,
            message: {
              role: 'assistant',
              content: [{ type: 'text', text: event.fullText }],
              timestamp: Date.now(),
            },
          });
          return;
        }
        case 'thinkingDelta': {
          currentThinkingText = event.fullText;
          ctx.agentEvent(runId, sessionKey, 'thinking', { text: event.fullText, delta: event.delta } as unknown as Record<string, unknown>, event.delta);
          return;
        }
        case 'commandOutput': {
          ctx.agentEvent(runId, sessionKey, 'command_output', {
            toolCallId: event.toolCallId,
            phase: event.phase,
            output: event.output,
            exitCode: event.meta?.exitCode,
            durationMs: event.meta?.durationMs,
            cwd: event.meta?.cwd,
          } as unknown as Record<string, unknown>);
          // Collect tool_result for history persistence (only on phase='end')
          if (event.phase === 'end') {
            pushEvent({
              kind: 'tool_result',
              data: {
                toolName: '', // filled from matched tool_use
                toolCallId: event.toolCallId,
                output: event.output ?? '',
                exitCode: event.meta?.exitCode ?? undefined,
                durationMs: event.meta?.durationMs ?? undefined,
                isError: event.meta?.exitCode != null && event.meta.exitCode !== 0,
              },
            });
          }
          return;
        }
        case 'toolStart': {
          if (event.tool.name === 'AskUserQuestion') {
            suppressToolStartIds.add(event.tool.id);
            return; // suppress — only tool:result will be emitted
          }
          if (event.tool.name === 'TodoWrite' || event.tool.name === 'TodoRead') {
            suppressToolStartIds.add(event.tool.id);
            return; // suppress — todo tools emit on the dedicated 'todo' stream
          }
          const data = { type: 'start', toolCallId: event.tool.id, toolName: event.tool.name, input: event.tool.input };
          ctx.agentEvent(runId, sessionKey, 'tool', data as unknown as Record<string, unknown>);
          return;
        }
        case 'toolUpdate': {
          if (suppressToolStartIds.has(event.toolCallId)) return;
          const data = { type: 'update', toolCallId: event.toolCallId, toolName: '', partialInput: event.partialJson };
          ctx.agentEvent(runId, sessionKey, 'tool', data as unknown as Record<string, unknown>);
          return;
        }
        case 'toolEnd': {
          const tool = event.tool;
          const isAskUser = tool.name === 'AskUserQuestion';
          const isExitPlanMode = tool.name === 'ExitPlanMode';
          const isExecTool = tool.name === 'Bash' || tool.name === 'Edit' || tool.name === 'Write' || tool.name === 'Read';
          const isGatedTool = isAskUser || isExitPlanMode || isExecTool;
          // In SDK suspend/resume strategy, gated tools are handled by canUseTool;
          // the bridge only forwards observable events.
          const useSdkSuspendResume = opts.hitlRuntimeMode === 'sdk_suspend_resume';

          suppressToolStartIds.delete(tool.id);

          // TodoWrite / TodoRead → dedicated 'todo' stream (persistent panel, not chat)
          if (tool.name === 'TodoWrite') {
            const todos = Array.isArray((tool.input as { todos?: unknown })?.todos)
              ? (tool.input as { todos: Array<{ content: string; status: string; activeForm: string }> }).todos
                  .filter(t => t && typeof t.content === 'string')
                  .map(t => ({
                    content: t.content,
                    status: (t.status === 'pending' || t.status === 'in_progress' || t.status === 'completed') ? t.status : 'pending',
                    activeForm: typeof t.activeForm === 'string' ? t.activeForm : t.content,
                  }))
              : [];
            ctx.agentEvent(runId, sessionKey, 'todo', { todos, toolCallId: tool.id } as unknown as Record<string, unknown>);
            return;
          }
          if (tool.name === 'TodoRead') {
            // TodoRead has no input that changes state; emit an empty update so
            // clients know the tool was invoked but no list change occurred.
            ctx.agentEvent(runId, sessionKey, 'todo', { todos: [], toolCallId: tool.id } as unknown as Record<string, unknown>);
            return;
          }

          // Parse questions for ask_user tool (used for both tool result and interaction)
          const parseQuestions = (): InteractionQuestion[] => {
            const rawQuestions = (tool.input as { questions?: Array<Record<string, unknown>> })?.questions ?? [];
            return rawQuestions.map(q => ({
              question: String(q.question ?? ''),
              header: typeof q.header === 'string' ? q.header : undefined,
              multiSelect: typeof q.multiSelect === 'boolean' ? q.multiSelect : undefined,
              options: Array.isArray(q.options)
                ? q.options.map((o: Record<string, unknown>) => ({
                  label: String(o.label ?? o.text ?? ''),
                  description: typeof o.description === 'string' ? o.description : undefined,
                  preview: typeof o.preview === 'string' ? o.preview : undefined,
                }))
                : undefined,
            }));
          };

          // Build tool result data
          let data: Record<string, unknown> = { type: 'result', toolCallId: tool.id, toolName: tool.name, output: tool.input };

          // For ask_user, add interaction info to tool result for inline rendering
          if (isAskUser) {
            const questions = parseQuestions();
            const interactionId = `int:${randomUUID()}`;
            data = {
              ...data,
              requiresInteraction: true,
              interaction: {
                interactionId,
                kind: 'ask_user' as const,
                questions,
                options: questions.flatMap(q => q.options ?? []).map(o => ({
                  value: o.label,
                  label: o.label,
                  description: o.description,
                })),
                inputSchema: {
                  type: questions.some(q => q.options && q.options.length > 0) ? 'choices' : 'text',
                  multiSelect: questions.some(q => q.multiSelect),
                },
                uiHints: { variant: 'question', severity: 'info' },
              },
            };
          }

          ctx.agentEvent(runId, sessionKey, 'tool', data as unknown as Record<string, unknown>);

          // Collect tool_use for history persistence
          const toolMeta = deriveToolMeta(tool.name, tool.id, tool.input as Record<string, unknown>, cwd);
          pushEvent({
            kind: 'tool_use',
             data: {
              type: 'tool_use',
              toolCallId: tool.id,
              toolName: tool.name,
              input: tool.input as Record<string, unknown>,
              title: toolMeta.title,
              description: toolMeta.description,
              subject: toolMeta.subject,
            },
          });

          if (interrupted) return;

          // In SDK suspend/resume strategy, gated tools are handled by canUseTool.
          // Skip the compatibility toolEnd -> pending interaction -> abort flow.
          if (useSdkSuspendResume && isGatedTool) {
            return;
          }

          // AskUserQuestion: 同时发送 tool stream（消息流渲染）和 interaction.requested（弹出面板）
          if (opts.withApproval && isAskUser) {
            const questions = parseQuestions();
            const prompt = questions.map(q => q.question).join('; ');
            const interactionId = `int:${randomUUID()}`;

            const requestedEvent = buildAskUserInteraction({
              interactionId, runId, sessionKey, toolCallId: tool.id, prompt, questions,
            });

            const pendingMeta: PendingInteraction = {
              interactionId,
              createdByConnId: ctx.connId,
              runId,
              sessionKey,
              kind: 'ask_user',
              toolCallId: tool.id,
              subject: requestedEvent.subject,
              prompt,
              questions,
              inputSchema: requestedEvent.inputSchema,
              uiHints: requestedEvent.uiHints,
              expiresAtMs: requestedEvent.expiresAtMs,
              toolInput: tool.input as Record<string, unknown>,
              model: opts.chatMeta?.model,
              permissionMode: opts.chatMeta?.permissionMode,
            };

            emitInteractionRequested(ctx, requestedEvent, pendingMeta, registry);
            interruptForInteraction();
          }

          if (opts.withApproval && isExitPlanMode) {
            // mode_switch is still mirrored onto agent.stream='mode_transition' for
            // compatibility. The registry tracks the pending entry so either the
            // compatibility resolver or the unified path can resume correctly.
            const transitionId = `mt:${randomUUID()}`;
            const fromMode = String((tool.input as { fromMode?: string })?.fromMode ?? 'plan');
            const toMode = String((tool.input as { toMode?: string })?.toMode ?? 'execute');
            const summary = typeof (tool.input as { plan?: string })?.plan === 'string'
              ? String((tool.input as { plan?: string }).plan)
              : typeof (tool.input as { summary?: string })?.summary === 'string'
                ? String((tool.input as { summary?: string }).summary)
                : '';

            // Emit phase change event when ExitPlanMode is called
            // This signals the agent is requesting to transition from plan to execute
            emitPhaseChange(toMode === 'execute' ? 'execute' : 'plan');

            const streamData = buildModeTransitionRequested({ transitionId, fromMode, toMode, summary });

            const pendingMeta: PendingInteraction = {
              interactionId: transitionId,
              createdByConnId: ctx.connId,
              runId,
              sessionKey,
              kind: 'mode_switch',
              toolCallId: tool.id,
              subject: {
                type: 'mode',
                toolName: 'ExitPlanMode',
                toolCallId: tool.id,
                fromMode,
                toMode,
              },
              fromMode,
              toMode,
              summary,
              inputSchema: { type: 'choices', multiSelect: false },
              uiHints: { variant: 'plan', severity: 'info' },
              expiresAtMs: streamData.expiresAtMs ?? Date.now() + EXEC_APPROVAL_TIMEOUT_MS,
              toolInput: tool.input as Record<string, unknown>,
              model: opts.chatMeta?.model,
              permissionMode: opts.chatMeta?.permissionMode,
            };

            // Because mode_switch is mirrored through the compatibility
            // mode_transition stream, we wire expiry handling explicitly here so
            // older plan cards still clear correctly on timeout.
            pendingMeta.onExpire = buildExpiryCallback(ctx, pendingMeta);
            const registered = registry.register(pendingMeta);
            if (!registered) return;
            ctx.agentEvent(runId, sessionKey, 'mode_transition', streamData as unknown as Record<string, unknown>);
            interruptForInteraction();
          }

          if (opts.withApproval && isExecTool) {
            const interactionId = `int:${randomUUID()}`;

            const requestedEvent = buildExecInteraction({
              interactionId, runId, sessionKey, tool, cwd,
            });

            const pendingMeta: PendingInteraction = {
              interactionId,
              createdByConnId: ctx.connId,
              runId,
              sessionKey,
              kind: 'exec',
              toolCallId: tool.id,
              subject: requestedEvent.subject,
              command: requestedEvent.command,
              cwd: requestedEvent.cwd,
              inputSchema: requestedEvent.inputSchema,
              uiHints: requestedEvent.uiHints,
              expiresAtMs: requestedEvent.expiresAtMs,
              toolInput: tool.input as Record<string, unknown>,
              model: opts.chatMeta?.model,
              permissionMode: opts.chatMeta?.permissionMode,
            };

            emitInteractionRequested(ctx, requestedEvent, pendingMeta, registry);
            interruptForInteraction();
            // Do not emit command_output phase='end' here: the command has not
            // actually executed yet. Emitting a synthetic success would conflict with
            // the pending approval UI. The real execution result is reported after the
            // run resumes and the tool actually executes.
          }
          return;
        }
        case 'lifecycle': {
          const lifecycleData: AgentLifecycleData = {
            phase: event.phase,
            agentMode: currentAgentMode,
            ...(event.data ?? {}),
          } as AgentLifecycleData;
          ctx.agentEvent(runId, sessionKey, 'lifecycle', lifecycleData as unknown as Record<string, unknown>);

          // Emit initial phase event on lifecycle start if in plan mode
          if (event.phase === 'start' && currentAgentMode === 'plan') {
            ctx.agentEvent(runId, sessionKey, 'phase', {
              phase: 'changed',
              fromPhase: 'execute',
              toPhase: 'plan',
              timestamp: Date.now(),
            } as unknown as Record<string, unknown>);
          }

          // On lifecycle end, collect any remaining thinking text (fallback for
          // bridges that don't emit contentBlockStop), and backfill tool_result
          if (event.phase === 'end') {
            if (currentThinkingText) {
              // Only push if not already pushed in contentBlockStop
              const alreadyPushed = collectedEvents.some(e => e.kind === 'thinking' && e.fullText === currentThinkingText);
              if (!alreadyPushed) {
                pushEvent({ kind: 'thinking', fullText: currentThinkingText });
              }
              currentThinkingText = '';
            }
            // Backfill toolName/subject/title/description in tool_result events from matching tool_use events
            const toolUseMap = new Map<string, ToolUseCollected>();
            for (const ce of collectedEvents) {
              if (ce.kind === 'tool_use') toolUseMap.set(ce.data.toolCallId, ce.data);
            }
            for (const ce of collectedEvents) {
              if (ce.kind === 'tool_result') {
                const tu = toolUseMap.get(ce.data.toolCallId);
                if (tu) {
                  if (!ce.data.toolName) ce.data.toolName = tu.toolName;
                  if (!ce.data.title) ce.data.title = tu.title;
                  if (!ce.data.description) ce.data.description = tu.description;
                  if (!ce.data.subject) ce.data.subject = tu.subject;
                }
              }
            }
          }
          return;
        }
        case 'usage': {
          const data: AgentAssistantData = { usage: event.usage };
          ctx.agentEvent(runId, sessionKey, 'assistant', data as unknown as Record<string, unknown>);
          return;
        }
        case 'messageStart': {
          const msgData: AgentMessageData = { phase: 'start', ...event.data };
          ctx.agentEvent(runId, sessionKey, 'message', msgData as unknown as Record<string, unknown>);
          return;
        }
        case 'messageStop': {
          const msgData: AgentMessageData = { phase: 'stop' };
          ctx.agentEvent(runId, sessionKey, 'message', msgData as unknown as Record<string, unknown>);
          return;
        }
        case 'contentBlockStart': {
          if (event.data.blockType === 'tool_use' && event.data.name === 'AskUserQuestion') {
            suppressBlockIndices.add(event.data.index);
          }
          // Record block type by index for accurate stop handling
          blockTypeMap.set(event.data.index, event.data.blockType);
          // Mark the start position of a text content block for history segmentation
          if (event.data.blockType === 'text') {
            textBlockStart = lastStreamedText.length;
          }
          if (event.data.blockType === 'tool_use' && event.data.name === 'AskUserQuestion') {
            return;
          }
          const blockData: AgentContentBlockData = {
            phase: 'start',
            index: event.data.index,
            blockType: event.data.blockType as 'text' | 'thinking' | 'tool_use',
            toolCallId: event.data.toolCallId,
            name: event.data.name,
          };
          ctx.agentEvent(runId, sessionKey, 'content_block', blockData as unknown as Record<string, unknown>);
          return;
        }
        case 'contentBlockStop': {
          if (event.data.blockType === 'tool_use' && suppressBlockIndices.has(event.data.index)) {
            suppressBlockIndices.delete(event.data.index);
            return;
          }
          // Use the block type recorded at start, not the one from the bridge
          // (bridge may report thinking blocks as 'text' in contentBlockStop)
          const actualBlockType = blockTypeMap.get(event.data.index) ?? event.data.blockType;
          blockTypeMap.delete(event.data.index);
          // Capture completed thinking content block as a thinking history segment
          if (actualBlockType === 'thinking' && currentThinkingText) {
            pushEvent({ kind: 'thinking', fullText: currentThinkingText });
            // Reset thinking text so it's not duplicated in lifecycle.end
            currentThinkingText = '';
          }
          // Capture completed text content block as an assistant_text history segment
          if (actualBlockType === 'text' && lastStreamedText.length > textBlockStart) {
            pushEvent({ kind: 'assistant_text', text: lastStreamedText.slice(textBlockStart) });
          }
          const blockData: AgentContentBlockData = {
            phase: 'stop',
            index: event.data.index,
            blockType: event.data.blockType as 'text' | 'thinking' | 'tool_use',
          };
          ctx.agentEvent(runId, sessionKey, 'content_block', blockData as unknown as Record<string, unknown>);
          return;
        }
        case 'cost': {
          const assistantData: AgentAssistantData = {
            costUsd: event.data.costUsd,
            durationMs: event.data.durationMs,
            numTurns: event.data.numTurns,
          };
          ctx.agentEvent(runId, sessionKey, 'assistant', assistantData as unknown as Record<string, unknown>);
          return;
        }
        case 'task': {
          // Emit task events on dedicated 'task' stream (sub-agent panel)
          const taskEvent = event.data;
          ctx.agentEvent(runId, sessionKey, 'task', {
            type: taskEvent.type,
            taskId: taskEvent.taskId,
            toolUseId: taskEvent.toolUseId,
            status: taskEvent.status,
            description: taskEvent.description,
            summary: taskEvent.summary,
            outputFile: taskEvent.outputFile,
            usage: taskEvent.usage,
            taskType: taskEvent.taskType,
            workflowName: taskEvent.workflowName,
            prompt: taskEvent.prompt,
            lastToolName: taskEvent.lastToolName,
            patch: taskEvent.patch,
          } as unknown as Record<string, unknown>);
          return;
        }
        case 'todoUpdate': {
          // Emit todo update on dedicated 'todo' stream (persistent panel)
          ctx.agentEvent(runId, sessionKey, 'todo', {
            todos: event.todos,
            toolCallId: event.toolCallId,
          } as unknown as Record<string, unknown>);
          return;
        }
        case 'toolProgress': {
          const d = event.data;
          const agentContext = d.parentToolUseId
            ? { parentToolUseId: d.parentToolUseId, ...(d.taskId && { taskId: d.taskId }) }
            : undefined;
          ctx.agentEvent(runId, sessionKey, 'tool', {
            type: 'progress',
            toolCallId: d.toolCallId,
            toolName: d.toolName,
            progress: { elapsedSeconds: d.elapsedSeconds },
            ...(agentContext && { agentContext }),
          } as unknown as Record<string, unknown>);
          return;
        }
        case 'toolSummary': {
          ctx.agentEvent(runId, sessionKey, 'tool', {
            type: 'summary',
            precedingToolUseIds: event.data.precedingToolUseIds,
            summary: event.data.summary,
          } as unknown as Record<string, unknown>);
          return;
        }
        case 'system': {
          ctx.agentEvent(runId, sessionKey, 'system', event.data);
          return;
        }
        case 'memoryRecall': {
          ctx.agentEvent(runId, sessionKey, 'memory', {
            type: 'recall',
            mode: event.data.mode,
            memories: event.data.memories,
          } as unknown as Record<string, unknown>);
          return;
        }
        case 'notification': {
          ctx.notificationEvent({
            key: event.data.key,
            text: event.data.text,
            priority: event.data.priority,
            color: event.data.color,
            timeoutMs: event.data.timeoutMs,
            sessionKey,
            runId,
          });
          return;
        }
        case 'promptSuggestion': {
          ctx.promptSuggestionEvent({
            runId,
            sessionKey,
            suggestions: [{ text: event.data.suggestion }],
          });
          return;
        }
        default:
          return;
      }
    });

    return {
      getLastStreamedText: () => lastStreamedText,
      wasInterrupted: () => interrupted,
      getCollectedEvents: () => collectedEvents,
    };
  };
}
