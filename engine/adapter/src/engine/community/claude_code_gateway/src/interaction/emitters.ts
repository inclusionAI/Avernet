// Interaction event emission + reconnect replay.
//
// These helpers own the "fire the requested event + register pending" atomic
// combo, and (on reconnect) the replay of outstanding pending interactions so
// the UI can re-render them.

import { createLogger } from '../debug.js';
import type { ConnectionContext } from '../gateway/connection-context.js';
import type { AgentModeTransitionData, InteractionRequestedEvent } from '../types.js';
import type { PendingInteractionRegistry } from './registry.js';
import type { PendingInteraction } from './types.js';
import { EXEC_APPROVAL_TIMEOUT_MS } from './builders.js';

const log = createLogger('server');

export function buildExpiryCallback(
  ctx: ConnectionContext,
  pending: Pick<PendingInteraction, 'interactionId' | 'runId' | 'sessionKey' | 'kind' | 'fromMode' | 'toMode'>,
): () => void {
  return () => {
    log.debug('interaction:expired', {
      interactionId: pending.interactionId,
      runId: pending.runId,
      kind: pending.kind,
    });
    const expiredAtMs = Date.now();
    try {
      if (pending.kind === 'mode_switch') {
        const modeData: AgentModeTransitionData = {
          phase: 'expired',
          transitionId: pending.interactionId,
          kind: 'exit_plan_mode',
          fromMode: pending.fromMode,
          toMode: pending.toMode,
          resolvedAtMs: expiredAtMs,
        };
        ctx.agentEvent(pending.runId, pending.sessionKey, 'mode_transition', modeData as unknown as Record<string, unknown>);
        return;
      }

      const expiredPayload = {
        interactionId: pending.interactionId,
        runId: pending.runId,
        sessionKey: pending.sessionKey,
        kind: pending.kind,
        phase: 'expired' as const,
        resolvedBy: 'system',
        reason: 'timeout',
        expiredAtMs,
      };
      ctx.event('interaction.resolved', expiredPayload as unknown as Record<string, unknown>);
    } catch (err) {
      log.warn('interaction:expire-emit-failed', {
        interactionId: pending.interactionId,
        error: (err as Error).message,
      });
    }
  };
}

export function emitInteractionRequested(
  ctx: ConnectionContext,
  event: InteractionRequestedEvent,
  pendingMeta: PendingInteraction,
  registry: PendingInteractionRegistry,
) {
  log.debug('interaction:requested', {
    interactionId: event.interactionId,
    runId: event.runId,
    kind: event.kind,
    subjectType: event.subject?.type,
  });

  pendingMeta.onExpire = buildExpiryCallback(ctx, pendingMeta);

  const registered = registry.register(pendingMeta);
  if (!registered) {
    log.warn('interaction:requested:registration-failed', {
      interactionId: event.interactionId,
      runId: event.runId,
      kind: event.kind,
    });
    return;
  }

  ctx.event('interaction.requested', event);

  setTimeout(() => {
    const stillPending = registry.peek(event.interactionId);
    if (stillPending) {
      registry.delete(event.interactionId);
      try { stillPending.onExpire?.(); } catch (err) {
        log.error('interaction:fallback-expire-threw', {
          interactionId: event.interactionId,
          error: (err as Error).message,
        });
      }
    }
  }, EXEC_APPROVAL_TIMEOUT_MS);
}

export function replayPendingInteractions(
  ctx: ConnectionContext,
  registry: PendingInteractionRegistry,
): void {
  // Replay pending interactions for sessions the connection is attached to,
  // or fall back to connection-based replay for backward compatibility
  const pending = registry.getForConnection(ctx.connId);
  if (pending.length === 0) return;

  log.debug('interaction:replay', { connId: ctx.connId, count: pending.length });

  for (const p of pending) {
    if (p.expiresAtMs <= Date.now()) {
      registry.delete(p.interactionId);
      continue;
    }

    const createdAtMs = p.createdAtMs ?? (p.expiresAtMs - EXEC_APPROVAL_TIMEOUT_MS);

    if (p.kind === 'mode_switch') {
      const modeData: AgentModeTransitionData = {
        phase: 'requested',
        transitionId: p.interactionId,
        kind: 'exit_plan_mode',
        fromMode: p.fromMode,
        toMode: p.toMode,
        summary: p.summary,
        createdAtMs,
        expiresAtMs: p.expiresAtMs,
      };
      ctx.agentEvent(p.runId, p.sessionKey, 'mode_transition', modeData as unknown as Record<string, unknown>);
      continue;
    }

    const event: InteractionRequestedEvent = {
      interactionId: p.interactionId,
      runId: p.runId,
      sessionKey: p.sessionKey,
      kind: p.kind,
      title: p.subject?.toolName || p.kind,
      subject: p.subject,
      prompt: p.prompt,
      questions: p.questions,
      options: p.options,
      command: p.command,
      cwd: p.cwd,
      inputSchema: p.inputSchema,
      uiHints: p.uiHints,
      agentContext: p.agentContext,
      createdAtMs,
      expiresAtMs: p.expiresAtMs,
    };

    ctx.event('interaction.requested', event);
  }
}
