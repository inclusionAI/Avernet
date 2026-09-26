// Internal types for the unified HITL (Human-in-the-Loop) interaction subsystem.
//
// These types describe the *server-side* pending record — the shape sent over
// the wire lives in `src/types.ts` (InteractionRequestedEvent / InteractionResolvedEvent).

import type {
  AgentContext,
  InteractionKind,
  InteractionPhase,
  InteractionSubject,
  InteractionOption,
  InteractionQuestion,
  InteractionUiHints,
  InteractionInputSchema,
} from '../types.js';

export type PendingInteraction = {
  interactionId: string;
  /** @deprecated Use createdByConnId. Kept for backward compat — prefer sessionKey for authority. */
  connId?: string;
  createdByConnId?: string;
  runId: string;
  sessionKey: string;
  kind: InteractionKind;
  toolCallId?: string;
  subject?: InteractionSubject;
  prompt?: string;
  questions?: InteractionQuestion[];
  options?: InteractionOption[];
  inputSchema?: InteractionInputSchema;
  uiHints?: InteractionUiHints;
  expiresAtMs: number;
  onExpire?: () => void;

  // kind='exec' wire-level fields (flattened onto replayed events)
  command?: string;
  cwd?: string;
  /** SDK-provided permission suggestions; forwarded as updatedPermissions on allow-always. */
  suggestions?: unknown[];
  /** Raw tool input captured at toolEnd. Kept so the continuation prompt
   * can echo the exact parameters (file paths, diffs, full command,
   * AskUserQuestion options) and Claude doesn't have to re-derive them. */
  toolInput?: Record<string, unknown>;

  // kind='mode_switch' wire-level fields for agent.stream='mode_transition' replay
  fromMode?: string;
  toMode?: string;
  summary?: string;

  // chat context captured from the originating chat.send so the follow-up
  // continuation can preserve model / permissionMode (sdkSessionId is read fresh from
  // the store at continuation time).
  model?: string;
  permissionMode?: string;

  // ownership / authorization — reserved for future cross-connection resolve
  actorId?: string;

  // ---- Stage-2 Runtime Continuation Support ----
  /** Creation timestamp for accurate age calculation */
  createdAtMs?: number;
  /** Resolver callback to resume the suspended SDK run */
  resolver?: (resolution: ResolvedInteractionInput) => void;
  /** Rejecter callback to abort the suspended SDK run */
  rejecter?: (error: Error) => void;
  /** Source of the interaction for migration compatibility */
  runtimeSource?: 'sdk-canUseTool' | 'followup';
  /** Current status to prevent duplicate resolve */
  status?: 'pending' | 'resolved' | 'expired' | 'cancelled';

  /** Subagent context — present when this interaction originates from a subagent. */
  agentContext?: AgentContext;
};

export type ResolvedInteractionInput = {
  decision: string;
  answer?: string;
  answers?: Record<string, string>;
  values?: Record<string, unknown>;
  selectedOptions?: string[];
  phase: InteractionPhase;
};
