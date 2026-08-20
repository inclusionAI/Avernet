/**
 * Hallucination detection types for rule-based AI agent output checking.
 *
 * These checks are deterministic, fast, and free (no LLM calls).
 * They complement the LLM-based analysis in `server/routes/analysis.ts`
 * which already checks `hallucinationRisk: true/false` at higher cost.
 */

/** Types of hallucination checks. */
export type HallucinationCheckType =
  | "error_ignoring"
  | "ungrounded_claim"
  | "fabricated_output"
  | "hallucinated_tool"
  | "contradiction";

/** Severity levels for failed checks. */
export type HallucinationSeverity = "low" | "medium" | "high";

/** A single check result. */
export type HallucinationCheck = {
  checkType: HallucinationCheckType;
  severity: HallucinationSeverity;
  /** Whether the check passed (true = no hallucination detected). */
  passed: boolean;
  /** Human-readable description of what was checked and the result. */
  description: string;
  /** Evidence snippet supporting a failed check, or null if passed. */
  evidence: string | null;
};

/** Aggregate result for a node execution. */
export type HallucinationCheckResult = {
  flowId: string;
  nodeId: string;
  attempt: number;
  /** Individual check results. */
  checks: HallucinationCheck[];
  /** Risk score: 0 (safe) – 100 (high risk). */
  riskScore: number;
  /** Risk level derived from score. */
  riskLevel: "none" | "low" | "medium" | "high";
};

/** Severity → score weight for failed checks. */
export const SEVERITY_WEIGHT: Record<HallucinationSeverity, number> = {
  low: 5,
  medium: 20,
  high: 40,
};