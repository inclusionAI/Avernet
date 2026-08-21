/**
 * ChatInject level gating.
 *
 * Replaces the legacy boolean `isPerformanceMode` with a unified level model
 * (perf/simple/full). Each inject event carries a minLevel (see
 * {@link module:inject-level}); an event fires only when
 * `rank(resolveInjectLevelForFlow(flowId)) >= rank(event.minLevel)`.
 *
 * Resolution order (per flow): per-flow level (set by the run trigger / workflow
 * YAML) → global `chatInject.level` from config (cached) → default "full".
 *
 * @module inject-gating
 */
import { loadConfig } from "./config/loader.js";
import {
  shouldInject as shouldInjectPure,
  type InjectLevel,
  type InjectEvent,
} from "./inject-level.js";

// Per-flow level override (set at run launch, cleared at flow end).
const _levelByFlowId = new Map<string, InjectLevel>();

/** Cache for the global fallback level (config does not change per process). */
let cachedGlobal: InjectLevel | null = null;

function globalLevel(): InjectLevel {
  if (cachedGlobal !== null) return cachedGlobal;
  try {
    cachedGlobal = loadConfig().app.chatInject.level ?? "full";
  } catch {
    cachedGlobal = "full";
  }
  return cachedGlobal;
}

/** Bind a per-flow inject level. Called at workflow launch with the resolved
 *  level (trigger param > workflow YAML > global). Pass to clear via {@link clearFlowInjectLevel}. */
export function setFlowInjectLevel(level: InjectLevel, flowId: string): void {
  _levelByFlowId.set(flowId, level);
  console.log(`[inject-gating] setFlowInjectLevel: level=${level} flowId=${flowId}`);
}

/** Clear a per-flow binding (call at flow end). */
export function clearFlowInjectLevel(flowId: string): void {
  _levelByFlowId.delete(flowId);
}

/** Resolve the effective level for a flow: per-flow override, else global, else full. */
export function resolveInjectLevelForFlow(flowId: string | undefined): InjectLevel {
  if (flowId) {
    const perFlow = _levelByFlowId.get(flowId);
    if (perFlow) return perFlow;
  }
  return globalLevel();
}

/** True when the event should be injected for the given flow's effective level. */
export function shouldInjectForFlow(flowId: string | undefined, event: InjectEvent): boolean {
  return shouldInjectPure(resolveInjectLevelForFlow(flowId), event);
}

/** Force the next {@link resolveInjectLevelForFlow} global fallback to re-read config. Test/reload only. */
export function resetInjectGateCache(): void {
  cachedGlobal = null;
}

/** Test-only: is a per-flow level binding present? (resource-leak guard for
 *  `__getAsyncExecutionResourceStateForTest`, replacing the old
 *  `_verbosityByFlowId.has` check since the binding moved to this module.) */
export function hasFlowInjectLevel(flowId: string): boolean {
  return _levelByFlowId.has(flowId);
}