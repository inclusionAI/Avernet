// Follow-up continuation: after the operator resolves a pending interaction,
// build an appropriate continuation prompt and kick off a new chat run that
// inherits the session context.
//
// Stage-1 implementation (current): a fresh `startChatRun` is spawned with the
// continuation prompt prepended. Stage-2 will replace this with SDK-native
// suspend/resume, so this module centralizes the prompt-shaping logic behind
// one function.
//
// Session-owned: run lifecycle is managed via SessionRuntimeRegistry, not
// ctx.activeRuns.

import { randomUUID } from 'node:crypto';
import { createLogger } from '../debug.js';
import { startChatRun, buildConversationContext, type OrchestratorHistoryEntry } from '../chat-orchestrator.js';
import type { SessionStore } from '../store.js';
import type { SessionHistoryMessage } from '../types.js';
import type { PendingInteraction, ResolvedInteractionInput } from './types.js';
import { DEFAULT_CWD } from '../gateway/handlers/sessions.js';
import type { ConnectionContext } from '../gateway/connection-context.js';
import type { BridgeOrchestratorFn } from '../gateway/orchestrator-bridge.js';
import type { SessionRuntimeRegistry } from '../runtime/session-runtime-registry.js';

const log = createLogger('server');

function nowIso() {
  return new Date().toISOString();
}

function toOrchestratorHistory(history: SessionHistoryMessage[] | undefined): OrchestratorHistoryEntry[] {
  if (!history) return [];
  const result: OrchestratorHistoryEntry[] = [];
  for (const m of history) {
    if (m.role === 'user' || m.role === 'assistant') {
      result.push({ role: m.role, text: m.text });
    }
  }
  return result;
}

export function buildAskUserContinuationPrompt(
  pending: PendingInteraction,
  params: ResolvedInteractionInput,
): string {
  if (params.phase === 'cancelled') {
    return pending.prompt
      ? `You previously asked: "${pending.prompt}"\nThe user declined to answer. Please propose an alternative approach or ask again differently.`
      : 'The user declined to answer your question. Please propose an alternative approach or ask again differently.';
  }

  const answer = params.answer ?? params.selectedOptions?.join(', ') ?? '';
  return pending.prompt
    ? `You previously asked: "${pending.prompt}"\nThe human answered: "${answer}". Please continue based on this answer.`
    : `The human answered: "${answer}". Please continue based on this answer.`;
}

export function buildExecContinuationPrompt(
  pending: PendingInteraction,
  params: ResolvedInteractionInput,
): string {
  const subject = pending.subject;
  const action = subject?.type === 'command' ? `command "${subject.command}"` :
    subject?.type === 'file' ? `${subject.toolName} on "${subject.filePath}"` :
      'the requested action';

  if (params.phase === 'allowed') {
    const alwaysSuffix = params.decision === 'allow-always'
      ? ' The user also granted standing permission for similar actions; proceed without re-prompting in this session.'
      : '';
    const toolInputJson = pending.toolInput
      ? `\n\nOriginal tool call details (re-issue exactly these parameters):\n${JSON.stringify(pending.toolInput, null, 2)}`
      : '';
    return `The user approved ${action}. Please proceed with the exact same operation.${alwaysSuffix}${toolInputJson}`;
  }
  return `The user denied ${action}. Please propose an alternative approach.`;
}

export function buildModeSwitchContinuationPrompt(
  pending: PendingInteraction,
  params: ResolvedInteractionInput,
): string {
  const fromMode = pending.fromMode ?? pending.subject?.fromMode ?? 'plan';
  const toMode = pending.toMode ?? pending.subject?.toMode ?? 'execute';

  if (params.phase === 'allowed') {
    return `The user agreed to switch from ${fromMode} to ${toMode}. Please proceed.`;
  }
  return `The user chose to stay in ${fromMode} mode. Please continue planning.`;
}

export async function continueByFollowUpChat(opts: {
  ctx: ConnectionContext;
  pending: PendingInteraction;
  params: ResolvedInteractionInput;
  store: SessionStore;
  bridge: BridgeOrchestratorFn;
  runtimeRegistry: SessionRuntimeRegistry;
  contextTurns: number;
  maxContextChars: number;
}): Promise<void> {
  const { ctx, pending, params, store, bridge, runtimeRegistry, contextTurns, maxContextChars } = opts;
  const binding = store.getByGatewaySessionKey(pending.sessionKey);
  if (!binding) return;

  const followUpRunId = pending.runId;
  const cwd = binding.cwd || DEFAULT_CWD;

  let continuationPrompt: string;
  switch (pending.kind) {
    case 'ask_user':
      continuationPrompt = buildAskUserContinuationPrompt(pending, params);
      break;
    case 'exec':
      continuationPrompt = buildExecContinuationPrompt(pending, params);
      break;
    case 'mode_switch':
      continuationPrompt = buildModeSwitchContinuationPrompt(pending, params);
      break;
    default:
      continuationPrompt = 'Please continue based on the user\'s response.';
  }

  const priorHistory = [ ...(binding.history ?? []) ];
  const systemPrompt = buildConversationContext({
    history: toOrchestratorHistory(priorHistory),
    contextTurns,
    maxContextChars,
  });

  const running = startChatRun({
    cwd,
    message: continuationPrompt,
    systemPrompt,
    model: pending.model,
    permissionMode: pending.permissionMode,
    resumeSessionId: binding.sdkSessionId,
  });
  const { getLastStreamedText, wasInterrupted } = bridge(ctx, followUpRunId, pending.sessionKey, cwd, running, {
    withApproval: true,
    chatMeta: { model: pending.model, permissionMode: pending.permissionMode },
  });

  // Register follow-up run to session runtime (not ctx.activeRuns)
  runtimeRegistry.registerRun(pending.sessionKey, {
    runId: followUpRunId,
    sessionKey: pending.sessionKey,
    abort: running.abort,
    startedAt: Date.now(),
    state: 'running',
  });

  const result = await running.completed;

  if (wasInterrupted()) {
    // Re-paused for nested interaction — update run state
    runtimeRegistry.updateRunState(pending.sessionKey, 'paused_for_interaction');
    log.debug('continuation: re-paused for nested interaction', {
      runId: followUpRunId,
      interactionId: pending.interactionId,
    });
    return;
  }

  // Run completed — remove from session runtime
  runtimeRegistry.completeRun(pending.sessionKey, followUpRunId);
  ctx.agentSeq.delete(followUpRunId);

  if (!result.ok) {
    ctx.chatEvent(followUpRunId, pending.sessionKey, {
      state: 'error',
      errorMessage: result.error || 'Follow-up CLI call failed',
    });
    return;
  }

  const text = result.text || getLastStreamedText() || '(Claude CLI returned but no text was parsed)';
  ctx.chatEvent(followUpRunId, pending.sessionKey, {
    state: 'final',
    stopReason: result.stopReason || 'end_turn',
    message: {
      role: 'assistant',
      content: [{ type: 'text', text }],
      timestamp: Date.now(),
    },
  });
  store.appendHistory(pending.sessionKey, { id: randomUUID(), role: 'assistant', text, timestamp: nowIso(), runId: followUpRunId });
}
