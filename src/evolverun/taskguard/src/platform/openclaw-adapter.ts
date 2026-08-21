/**
 * OpenClaw Adapter — wraps OpenClaw Plugin SDK APIs into PlatformAdapter.
 *
 * This is a factory function, not a class. It creates a PlatformAdapter
 * from OpenClaw's PluginApi and session context.
 *
 * The adapter delegates to the same OpenClaw APIs that index.ts currently
 * calls directly — no behavior change, just organized differently.
 *
 * @module platform/openclaw-adapter
 */

import type { PlatformAdapter, TaskFlowAdapter, ChatInjectAdapter, SessionAdapter, ProgressAdapter, AbortAdapter, CapabilityMatrix } from "./types.js";
import { PLATFORM_CAPABILITIES } from "./types.js";
import type { PluginApi } from "./openclaw-types.js";

/** Options for creating an OpenClaw adapter. */
export interface OpenClawAdapterOptions {
  /** The OpenClaw PluginApi instance from the register() callback. */
  api: PluginApi;
  /** Session key identifying the current conversation. */
  sessionKey: string;
  /** Optional session ID. */
  sessionId?: string;
  /** Skill root directory — typically "." for the current working directory. */
  skillRoot?: string;
  /** OpenClaw delivery context (used for runtime user resolution and execution mode). */
  deliveryContext?: Record<string, unknown>;
  /** Progress callback forwarded from the controller. */
  onProgress?: (text: string, details?: Record<string, unknown>) => void;
  /** Abort signal for cancelling the workflow run. */
  abortSignal?: AbortSignal;
  /**
   * Chat inject function — the adapter delegates message injection to this callback
   * because the real implementation in index.ts has complex DingTalk/card logic
   * that we don't want to duplicate here.
   *
   * Signature matches ControllerDeps["chatInject"].
   */
  chatInjectFn: (message: string, idempotencyKey: string) => Promise<void>;
  /**
   * Function to resolve the runtime user from delivery context.
   * Imported from src/runtime/user-context.js by the caller.
   */
  resolveUser: (params: { deliveryContext?: Record<string, unknown>; workflowDefaults?: Record<string, unknown> }) => { id?: string; name?: string } | undefined;
  /** Optional workflow defaults for user resolution. */
  workflowDefaults?: Record<string, unknown>;
}

// ── Factory ──

/**
 * Create a PlatformAdapter backed by OpenClaw's Plugin SDK.
 *
 * This is the initial adapter for Phase 1 — it wraps the same API calls
 * that index.ts currently makes directly, so behavior is identical.
 */
export function createOpenClawAdapter(options: OpenClawAdapterOptions): PlatformAdapter {
  const { api, sessionKey, sessionId, skillRoot, deliveryContext, onProgress, abortSignal, chatInjectFn, resolveUser, workflowDefaults } = options;

  // ── TaskFlow: delegate to OpenClaw's boundTaskFlow ──
  // Cast required: OpenClaw SDK's bindSession() returns its own TaskFlow type
  // which is structurally identical but nominally distinct from our TaskFlowAdapter.
  const taskFlow: TaskFlowAdapter = api.runtime.taskFlow.bindSession({ sessionKey }) as unknown as TaskFlowAdapter;

  // ── ChatInject: delegate to caller-provided function ──
  const chatInject: ChatInjectAdapter = {
    inject: chatInjectFn,
  };

  // ── Session: construct from options ──
  const user = resolveUser({ deliveryContext, workflowDefaults });
  const session: SessionAdapter = {
    sessionKey,
    sessionId,
    skillRoot: skillRoot ?? ".",
    user,
    deliveryContext,
  };

  // ── Progress: forward callback ──
  const progress: ProgressAdapter = {
    onProgress,
  };

  // ── Abort: forward signal ──
  const abort: AbortAdapter = {
    signal: abortSignal,
  };

  return {
    platform: "openclaw",
    taskFlow,
    chatInject,
    session,
    progress,
    abort,
    capabilities: PLATFORM_CAPABILITIES.openclaw,
  };
}