/**
 * Guardian Agent type definitions.
 */

/** Repair action types the guardian agent can recommend. */
export type RepairAction =
  | "patch-prompt"      // Replace the node's prompt with a patched version
  | "adjust-params"     // Override executor parameters
  | "adjust-timeout"    // Change the timeout
  | "skip-retry"        // Not worth retrying, terminate early
  | "retry-as-is";      // No specific fix, retry with original config

/** Failure reason categories. */
export type FailureReason =
  | "prompt-ambiguity"
  | "timeout"
  | "param-type-mismatch"
  | "tool-not-found"
  | "output-contract"
  | "other";

/** Structured repair strategy returned by the guardian agent. */
export type GuardianRepair = {
  failureReason: FailureReason;
  action: RepairAction;
  patchedPrompt?: string;                    // Only for patch-prompt
  paramOverrides?: Record<string, unknown>;  // Only for adjust-params
  timeoutOverride?: number;                   // Only for adjust-timeout (ms)
  reasoning: string;
};

/** Input parameters for the guardian agent's analyze() method. */
export type GuardianAnalysisParams = {
  nodeId: string;
  executorType: string;
  error: string;
  resolvedPrompt?: string;
  inputJson?: string;
  outputJson?: string;
  attempt: number;
  maxAttempts: number;
};

/** Guardian agent configuration. */
export type GuardianConfig = {
  enabled: boolean;
  analysisTimeoutSeconds: number;  // Timeout for the embedded-agent analysis call
  maxPromptMultiplier: number;     // Max ratio of patched prompt to original
};

/** Default guardian configuration. */
export const DEFAULT_GUARDIAN_CONFIG: GuardianConfig = {
  enabled: true,
  analysisTimeoutSeconds: 60,
  maxPromptMultiplier: 2,
};