/**
 * Rule-based hallucination detection for embedded-agent execution steps.
 *
 * Analyzes node_step_traces data to detect common hallucination patterns
 * without requiring LLM calls. Each check is a pure function returning
 * pass/fail with evidence.
 *
 * Called after step traces are persisted — fast, free, deterministic.
 */

import type { NodeStepTraceRow } from "../db/repositories/types.js";
import {
  checkErrorIgnoring,
  checkUngroundedClaim,
  checkFabricatedOutput,
  checkHallucinatedTool,
  checkContradiction,
} from "./hallucination-rules.js";
import type {
  HallucinationCheck,
  HallucinationCheckResult,
  HallucinationSeverity,
} from "./hallucination-types.js";
import { SEVERITY_WEIGHT } from "./hallucination-types.js";

/**
 * Run all hallucination checks against a node's step trace data.
 *
 * @param steps - Step records from node_step_traces table.
 * @param flowId - Flow run ID.
 * @param nodeId - Node ID.
 * @param attempt - Execution attempt number.
 * @returns Aggregate check result with risk score and level.
 */
export function checkHallucination(
  steps: NodeStepTraceRow[],
  flowId: string,
  nodeId: string,
  attempt: number,
): HallucinationCheckResult {
  const checks: HallucinationCheck[] = [
    checkErrorIgnoring(steps),
    checkUngroundedClaim(steps),
    checkFabricatedOutput(steps),
    checkHallucinatedTool(steps),
    checkContradiction(steps),
  ];

  // Compute risk score from failed checks
  const riskScore = Math.min(
    100,
    checks
      .filter((c) => !c.passed)
      .reduce((sum, c) => sum + SEVERITY_WEIGHT[c.severity], 0),
  );

  const riskLevel = deriveRiskLevel(riskScore);

  return { flowId, nodeId, attempt, checks, riskScore, riskLevel };
}

function deriveRiskLevel(
  score: number,
): "none" | "low" | "medium" | "high" {
  if (score === 0) return "none";
  if (score <= 15) return "low";
  if (score <= 45) return "medium";
  return "high";
}