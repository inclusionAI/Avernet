// interaction resolve handlers + compatibility alias for mode_transition.resolve.
//
// Exports INTERACTION_METHODS, consumed by the frame dispatcher. Each entry
// is a ctx-aware handler that writes its own response frame.
//
// Session-aware: resolve authority is now based on session controller, not connId.

import { createLogger } from '../debug.js';
import type { ConnectionContext } from '../gateway/connection-context.js';
import type {
  AgentModeTransitionData,
  GatewayRequestFrame,
  InteractionKind,
  InteractionPhase,
} from '../types.js';
import type { SessionStore } from '../store.js';
import type { BridgeOrchestratorFn } from '../gateway/orchestrator-bridge.js';
import type { PendingInteractionRegistry } from './registry.js';
import type { SessionRuntimeRegistry } from '../runtime/session-runtime-registry.js';
import { buildInteractionResolvedEvent, buildModeTransitionResolved } from './builders.js';
import { continueByFollowUpChat } from './continuation.js';

const log = createLogger('server');

export type InteractionHandlerDeps = {
  registry: PendingInteractionRegistry;
  store: SessionStore;
  bridge: BridgeOrchestratorFn;
  runtimeRegistry: SessionRuntimeRegistry;
  contextTurns: number;
  maxContextChars: number;
};

export type InteractionHandler = (
  ctx: ConnectionContext,
  frame: GatewayRequestFrame,
  deps: InteractionHandlerDeps,
) => Promise<void>;

function normalizeInteractionResolveParams(params: Record<string, unknown>): {
  interactionId: string;
  decision: string;
  answer?: string;
  answers?: Record<string, string>;
  values?: Record<string, unknown>;
  selectedOptions?: string[];
} {
  const rawDecision = typeof params.decision === 'string'
    ? params.decision
    : typeof params.action === 'string'
      ? params.action
      : 'submit';
  const answer = typeof params.answer === 'string'
    ? params.answer
    : typeof params.message === 'string'
      ? params.message
      : undefined;

  let normalizedDecision = rawDecision;
  if (rawDecision === 'approved' || rawDecision === 'allow' || rawDecision === 'approve') {
    normalizedDecision = 'allow-once';
  }
  if (rawDecision === 'denied' || rawDecision === 'reject') {
    normalizedDecision = 'deny';
  }

  const answers = typeof params.answers === 'object' && params.answers !== null
    ? (Object.fromEntries(
        Object.entries(params.answers as Record<string, unknown>)
          .filter(([ , v ]) => typeof v === 'string'),
      ) as Record<string, string>)
    : undefined;

  return {
    interactionId: String(params.interactionId ?? params.id ?? params.approvalId ?? ''),
    decision: normalizedDecision,
    answer,
    answers,
    values: typeof params.values === 'object' && params.values !== null
      ? params.values as Record<string, unknown>
      : undefined,
    selectedOptions: Array.isArray(params.selectedOptions)
      ? params.selectedOptions as string[]
      : undefined,
  };
}

function validateDecisionForKind(kind: InteractionKind, decision: string): boolean {
  const validDecisions: Record<InteractionKind, string[]> = {
    ask_user: [ 'submit', 'cancel' ],
    exec: [ 'allow-once', 'allow-always', 'deny' ],
    mode_switch: [],
  };
  return validDecisions[kind]?.includes(decision) ?? false;
}

function mapDecisionToPhase(kind: InteractionKind, decision: string): InteractionPhase {
  const mapping: Record<string, InteractionPhase> = {
    'ask_user:submit': 'answered',
    'ask_user:cancel': 'cancelled',
    'exec:allow-once': 'allowed',
    'exec:allow-always': 'allowed',
    'exec:deny': 'denied',
    'mode_switch:proceed': 'allowed',
    'mode_switch:stay': 'denied',
  };
  return mapping[`${kind}:${decision}`] ?? 'cancelled';
}

export const handleInteractionResolve: InteractionHandler = async (ctx, frame, deps) => {
  const rawParams = (frame.params ?? {}) as Record<string, unknown>;
  const params = normalizeInteractionResolveParams(rawParams);

  if (!params.interactionId) {
    ctx.response(frame.id, false, undefined, { message: 'interactionId required', code: 'INVALID_REQUEST' });
    return;
  }

  const pending = deps.registry.take(params.interactionId);
  if (!pending) {
    ctx.response(frame.id, false, undefined, { message: 'No pending interaction found', code: 'NOT_FOUND' });
    return;
  }

  // Session-based authority: verify current connection is controller of the session
  if (!deps.runtimeRegistry.isController(pending.sessionKey, ctx.connId)) {
    // Put the pending back so the rightful controller can still resolve it
    deps.registry.register(pending);
    ctx.response(frame.id, false, undefined, { message: 'Not controller of this session', code: 'FORBIDDEN' });
    return;
  }

  if (!validateDecisionForKind(pending.kind, params.decision)) {
    deps.registry.register(pending);
    const hint = pending.kind === 'mode_switch'
      ? "; mode_switch interactions must be resolved via 'mode_transition.resolve' with decision='proceed'|'stay'"
      : '';
    ctx.response(frame.id, false, undefined, {
      message: `Decision '${params.decision}' is not valid for kind '${pending.kind}'${hint}`,
      code: 'UNSUPPORTED_DECISION',
    });
    return;
  }

  const phase = mapDecisionToPhase(pending.kind, params.decision);

  log.debug('interaction:resolved', {
    interactionId: params.interactionId,
    decision: params.decision,
    kind: pending.kind,
    phase,
    hasAnswer: Boolean(params.answer),
  });

  const resolvedEvent = buildInteractionResolvedEvent(pending, { ...params, phase });

  ctx.event('interaction.resolved', resolvedEvent);

  // Update run state back to running
  if (pending.kind !== 'mode_switch') {
    deps.runtimeRegistry.updateRunState(pending.sessionKey, 'running');
  }

  ctx.response(frame.id, true, {
    accepted: true,
    interactionId: params.interactionId,
    decision: params.decision,
    kind: pending.kind,
  });

  // Prefer resolver-based resume; fall back to compatibility continuation only when needed.
  if (pending.resolver) {
    log.debug('interaction:using-resolver', {
      interactionId: params.interactionId,
      kind: pending.kind,
    });
    pending.resolver({ ...params, phase });
  } else {
    log.debug('interaction:using-continuation-fallback', {
      interactionId: params.interactionId,
      kind: pending.kind,
    });
    void continueByFollowUpChat({
      ctx,
      pending,
      params: { ...params, phase },
      store: deps.store,
      bridge: deps.bridge,
      runtimeRegistry: deps.runtimeRegistry,
      contextTurns: deps.contextTurns,
      maxContextChars: deps.maxContextChars,
    }).catch(err => {
      log.error('interaction:continuation-threw', {
        interactionId: params.interactionId,
        runId: pending.runId,
        sessionKey: pending.sessionKey,
        error: (err as Error).message,
      });
    });
  }
};

export const handleInteractionPendingList: InteractionHandler = async (ctx, frame, deps) => {
  const params = (frame.params ?? {}) as Record<string, unknown>;
  const sessionKey = String(params.sessionKey ?? '').trim();

  // If sessionKey provided, query by session; otherwise fallback to connection for compat
  let pendingList;
  if (sessionKey) {
    pendingList = deps.registry.listForSession(sessionKey);
  } else {
    pendingList = deps.registry.getForConnection(ctx.connId);
  }

  const interactions = pendingList.map(p => ({
    interactionId: p.interactionId,
    runId: p.runId,
    sessionKey: p.sessionKey,
    kind: p.kind,
    title: p.subject?.toolName || p.kind,
    subject: p.subject,
    questions: p.questions,
    options: p.options,
    command: p.command,
    cwd: p.cwd,
    fromMode: p.fromMode,
    toMode: p.toMode,
    summary: p.summary,
    inputSchema: p.inputSchema,
    uiHints: p.uiHints,
    createdAtMs: p.createdAtMs ?? (p.expiresAtMs - 5 * 60 * 1000),
    expiresAtMs: p.expiresAtMs,
  }));
  ctx.response(frame.id, true, { interactions });
};

/** Compatibility alias for resolving a mode_switch interaction.
 * Accepts { transitionId, decision: 'proceed'|'stay' }. */
export const handleModeTransitionResolve: InteractionHandler = async (ctx, frame, deps) => {
  const params = (frame.params ?? {}) as Record<string, unknown>;
  const transitionId = String(params.transitionId ?? params.interactionId ?? '');
  const decision = String(params.decision ?? 'proceed');

  if (!transitionId) {
    ctx.response(frame.id, false, undefined, { message: 'transitionId required', code: 'INVALID_REQUEST' });
    return;
  }
  if (decision !== 'proceed' && decision !== 'stay') {
    ctx.response(frame.id, false, undefined, {
      message: `Decision '${decision}' invalid; expected 'proceed' or 'stay'`,
      code: 'UNSUPPORTED_DECISION',
    });
    return;
  }

  const pending = deps.registry.take(transitionId);
  if (!pending || pending.kind !== 'mode_switch') {
    ctx.response(frame.id, false, undefined, { message: 'No pending mode transition found', code: 'NOT_FOUND' });
    return;
  }

  // Session-based authority check
  if (!deps.runtimeRegistry.isController(pending.sessionKey, ctx.connId)) {
    deps.registry.register(pending);
    ctx.response(frame.id, false, undefined, { message: 'Not controller of this session', code: 'FORBIDDEN' });
    return;
  }

  const phase: InteractionPhase = decision === 'proceed' ? 'allowed' : 'denied';

  log.debug('mode_transition:resolved', {
    transitionId,
    decision,
    runId: pending.runId,
    sessionKey: pending.sessionKey,
  });

  const resolvedData: AgentModeTransitionData = buildModeTransitionResolved({
    transitionId,
    fromMode: pending.fromMode ?? 'plan',
    toMode: pending.toMode ?? 'execute',
    decision: decision as 'proceed' | 'stay',
  });
  ctx.agentEvent(pending.runId, pending.sessionKey, 'mode_transition', resolvedData as unknown as Record<string, unknown>);

  ctx.response(frame.id, true, {
    accepted: true,
    transitionId,
    decision,
  });

  if (pending.resolver) {
    log.debug('mode_transition:using-resolver', {
      transitionId,
      decision,
    });
    pending.resolver({ decision, phase });
  } else {
    log.debug('mode_transition:using-continuation-fallback', {
      transitionId,
      decision,
    });
    void continueByFollowUpChat({
      ctx,
      pending,
      params: { decision, phase },
      store: deps.store,
      bridge: deps.bridge,
      runtimeRegistry: deps.runtimeRegistry,
      contextTurns: deps.contextTurns,
      maxContextChars: deps.maxContextChars,
    }).catch(err => {
      log.error('mode_transition:continuation-threw', {
        transitionId,
        runId: pending.runId,
        sessionKey: pending.sessionKey,
        error: (err as Error).message,
      });
    });
  }
};

export const INTERACTION_METHODS: Record<string, InteractionHandler> = {
  'interaction.resolve': handleInteractionResolve,
  'interaction.pending.list': handleInteractionPendingList,
  'mode_transition.resolve': handleModeTransitionResolve,
};
