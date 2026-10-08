/**
 * Adapter-to-ControllerDeps Bridge — converts a PlatformAdapter into ControllerDeps.
 *
 * This is the core mapping function: it takes a platform-agnostic PlatformAdapter
 * and per-invocation context (executeNode, workflows, packs, etc.) and produces
 * a complete ControllerDeps object that the Controller can use.
 *
 * Phase 1: Only OpenClawAdapter is available, but the bridge is already generic.
 * Phase 2+: McpServerAdapter will reuse the same bridge.
 *
 * @module platform/adapter-to-deps
 */

import type { ControllerDeps } from "../controller.js";
import type { PlatformAdapter } from "./types.js";

/**
 * Extra context needed to build ControllerDeps beyond what the adapter provides.
 *
 * These are per-invocation concerns that don't belong in the platform abstraction:
 * - executeNode depends on the specific workflow run context (actionRegistry, toolCtx, workflow spec)
 * - resolvedWorkflows/resolvedPacks are loaded at runtime, not platform-bound
 * - api is OpenClaw-specific passthrough (Phase 1 only; Phase 2 removes this)
 */
export interface AdapterDepsExtras {
  /** Action registry for the current workflow run. Always provided by the caller. */
  actionRegistry: ControllerDeps["actionRegistry"];
  /** Executor dispatch function — must be provided by the caller. */
  executeNode: ControllerDeps["executeNode"];
  /** Resolved workflow catalog. */
  resolvedWorkflows?: ControllerDeps["resolvedWorkflows"];
  /** Resolved pack catalog. */
  resolvedPacks?: ControllerDeps["resolvedPacks"];
  /** Workflows that failed to load/validate. */
  failedWorkflows?: ControllerDeps["failedWorkflows"];
  /** Workflow registry (id → spec map). */
  workflowRegistry?: ControllerDeps["workflowRegistry"];
  /** Command formatter for facade workflows. */
  formatWorkflowCommand?: ControllerDeps["formatWorkflowCommand"];
  /** Subworkflow execution deps. */
  subworkflowDeps?: ControllerDeps["subworkflowDeps"];
  /** Flow control service instance. */
  flowControl?: ControllerDeps["flowControl"];
  /** OpenClaw PluginApi passthrough (Phase 1 only — will be removed in Phase 2). */
  api?: ControllerDeps["api"];
  /** Database instance. */
  db?: ControllerDeps["db"];
  /** Workflow spec API repository. */
  workflowSpecApiRepo?: ControllerDeps["workflowSpecApiRepo"];
  /** Workflow log directory. */
  workflowLogDir?: ControllerDeps["workflowLogDir"];
  /** Inject level for chatInject notifications ("perf"|"simple"|"full"). Default "full". */
  chatInjectLevel?: ControllerDeps["chatInjectLevel"];
  /** Dynamic workflow observability emitter. */
  eventEmitter?: ControllerDeps["eventEmitter"];
  /** Packs root directory for git versioning operations. */
  packsRoot?: string;
  /** ClawWeb API base URL. */
  clawWebBaseUrl?: string;
  /** Bot ID for versioning (per-bot branch). */
  botId?: string;
  /** Owner ID for versioning (per-bot branch). */
  ownerId?: string;
  /** Ed25519 private key (base64) for signing internal API requests. */
  signatureKey?: string;
/** Git remote URL for per-pack repos. */
  gitRemoteUrl?: string;
/** Git username for credential-cache. */
  gitUsername?: string;
/** Git token/password for credential-cache. */
  gitToken?: string;
/** Git commit author email. */
  gitEmail?: string;
  /** Facade binding repository — written by handleDeploy (facade_bindings table). */
  facadeBindingRepo?: ControllerDeps["facadeBindingRepo"];
}

/**
 * Build a ControllerDeps object from a PlatformAdapter and per-invocation extras.
 *
 * This function replaces the inline `buildDeps()` in index.ts, moving the
 * adapter-provided fields (sessionKey, chatInject, taskFlow, etc.) to come
 * from the adapter rather than being manually destructured from the API.
 */
export function buildControllerDeps(
  adapter: PlatformAdapter,
  extras: AdapterDepsExtras,
): ControllerDeps {
  return {
    // ── From adapter ──
    boundTaskFlow: adapter.taskFlow,
    chatInject: adapter.chatInject.inject,
    sessionKey: adapter.session.sessionKey,
    sessionId: adapter.session.sessionId,
    skillRoot: adapter.session.skillRoot,
    user: adapter.session.user,
    onProgress: adapter.progress.onProgress,
    abortSignal: adapter.abort.signal,
    transportMode: adapter.transportMode,

    // ── From extras ──
    actionRegistry: extras.actionRegistry,
    executeNode: extras.executeNode,
    resolvedWorkflows: extras.resolvedWorkflows,
    failedWorkflows: extras.failedWorkflows,
    resolvedPacks: extras.resolvedPacks,
    workflowRegistry: extras.workflowRegistry,
    formatWorkflowCommand: extras.formatWorkflowCommand,
    subworkflowDeps: extras.subworkflowDeps,
    flowControl: extras.flowControl,
    api: extras.api,
    db: extras.db,
    workflowSpecApiRepo: extras.workflowSpecApiRepo,
    workflowLogDir: extras.workflowLogDir,
    chatInjectLevel: extras.chatInjectLevel,
    eventEmitter: extras.eventEmitter,
    packsRoot: extras.packsRoot,
    clawWebBaseUrl: extras.clawWebBaseUrl,
    botId: extras.botId,
    ownerId: extras.ownerId,
    signatureKey: extras.signatureKey,
    gitRemoteUrl: extras.gitRemoteUrl,
    gitUsername: extras.gitUsername,
    gitToken: extras.gitToken,
    gitEmail: extras.gitEmail,
    facadeBindingRepo: extras.facadeBindingRepo,
  };
}