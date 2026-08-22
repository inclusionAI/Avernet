/**
 * LLM-based on-result branch evaluation.
 *
 * When a `NodeOnResultBranch` has `llmEvaluate` configured, this module
 * provides the async function to call an LLM and determine if the branch
 * should be selected. Falls back to rule-based `match` on LLM failure.
 *
 * @module llm/evaluate-branch
 */

import type { NodeOnResultBranch, LlmEvaluationResult } from "../types.js";
import type { TemplateContext } from "../runner.js";
import { callLlm, checkLlmAvailability } from "./client.js";
import { resolveTemplate } from "../runner.js";

// ── Types ──

export type LlmBranchEvaluationResult = {
  /** Whether the LLM determined the condition was met. */
  met: boolean;
  /** Index of the matched branch, or -1 if none matched. */
  matchedBranchIndex: number;
  /** The LLM's reasoning. */
  reason: string;
  /** Token usage from the LLM call. */
  usage?: { promptTokens: number; completionTokens: number; totalTokens: number };
  /** Model used. */
  model?: string;
  /** Whether this was a fallback to rule-based matching. */
  fallback: boolean;
};

// ── Prompt builders ──

function buildBranchEvalSystemPrompt(): string {
  return [
    "You are a workflow branch evaluator. You must determine whether a specific condition is met based on the provided node outputs.",
    "",
    "Respond with a JSON object:",
    '- "met": boolean — true if the condition is met',
    '- "reason": string — brief explanation of your judgment',
    "",
    "Be strict and precise. Only return met=true when the evidence clearly supports the condition.",
  ].join("\n");
}

function buildBranchEvalUserPrompt(
  condition: string,
  templateCtx: TemplateContext,
): string {
  // Resolve condition template (may reference {{nodeId.output}})
  const resolvedCondition = resolveTemplate(condition, templateCtx);

  const parts: string[] = [];
  parts.push("## Condition to Evaluate");
  parts.push(resolvedCondition);
  parts.push("");
  parts.push("Based on the context above, is this condition met?");
  parts.push('Respond with JSON: { "met": boolean, "reason": string }');
  return parts.join("\n");
}

// ── Parse response ──

function parseEvaluationResponse(raw: string): { met: boolean; reason: string } {
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed.met === "boolean" && typeof parsed.reason === "string") {
      return { met: parsed.met, reason: parsed.reason };
    }
  } catch {
    // Try markdown code block
    const jsonMatch = raw.match(/```(?:json)?\s*\n?([\s\S]*?)\n?```/);
    if (jsonMatch) {
      try {
        const parsed = JSON.parse(jsonMatch[1].trim());
        if (typeof parsed.met === "boolean" && typeof parsed.reason === "string") {
          return { met: parsed.met, reason: parsed.reason };
        }
      } catch {
        // Fall through
      }
    }
  }

  // Last resort
  const metMatch = raw.match(/"met"\s*:\s*(true|false)/i);
  if (metMatch) {
    const met = metMatch[1].toLowerCase() === "true";
    const reasonMatch = raw.match(/"reason"\s*:\s*"([^"]*)"/);
    return { met, reason: reasonMatch?.[1] ?? raw.slice(0, 300) };
  }

  return { met: false, reason: `Unparseable LLM response: ${raw.slice(0, 200)}` };
}

// ── Main evaluation function ──

/**
 * Evaluate onResult branches using LLM.
 *
 * For each branch with `llmEvaluate`, calls the LLM to check the condition.
 * First branch whose LLM evaluation returns met=true wins.
 * If no LLM branch matches, or if all LLM calls fail, falls back to
 * rule-based matching using `branch.match`.
 *
 * Returns the evaluation result for observability persistence.
 */
export async function evaluateBranchesWithLlm(
  branches: NodeOnResultBranch[],
  templateCtx: TemplateContext,
): Promise<LlmBranchEvaluationResult> {
  // Check availability upfront
  const availability = checkLlmAvailability();
  if (!availability.available) {
    // No LLM available — will be handled by caller falling back to rules
    return {
      met: false,
      matchedBranchIndex: -1,
      reason: `LLM unavailable: ${availability.reason}`,
      fallback: true,
    };
  }

  for (let i = 0; i < branches.length; i++) {
    const branch = branches[i];
    if (!branch.llmEvaluate) continue;

    const spec = branch.llmEvaluate;
    const systemPrompt = buildBranchEvalSystemPrompt();
    const userPrompt = buildBranchEvalUserPrompt(spec.condition, templateCtx);

    try {
      const llmResult = await callLlm({
        systemPrompt,
        userPrompt,
        model: spec.model,
        temperature: spec.temperature ?? 0.2,
        maxTokens: 512,
        timeoutMs: spec.timeoutMs ?? 15000,
        jsonMode: true,
      });

      const parsed = parseEvaluationResponse(llmResult.content);

      if (parsed.met) {
        return {
          met: true,
          matchedBranchIndex: i,
          reason: parsed.reason,
          usage: llmResult.usage,
          model: llmResult.model,
          fallback: false,
        };
      }
    } catch (err) {
      // LLM call failed for this branch — continue to next
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[llm-evaluate-branch] LLM call failed for branch ${i}: ${msg}`);
    }
  }

  // No LLM branch matched
  return {
    met: false,
    matchedBranchIndex: -1,
    reason: "No LLM-evaluated branch condition was met",
    fallback: false,
  };
}

/**
 * Build an LlmEvaluationResult for persistence on the node state.
 */
export function toLlmEvaluationResult(
  branchResult: LlmBranchEvaluationResult,
): LlmEvaluationResult | undefined {
  // Don't persist if no LLM call was made (LLM unavailable)
  if (branchResult.fallback && !branchResult.model) return undefined;

  return {
    met: branchResult.met,
    matchedBranchId: branchResult.matchedBranchIndex >= 0
      ? `llm-branch-${branchResult.matchedBranchIndex}`
      : undefined,
    reason: branchResult.reason,
    usage: branchResult.usage
      ? {
          input: branchResult.usage.promptTokens,
          output: branchResult.usage.completionTokens,
          totalTokens: branchResult.usage.totalTokens,
        }
      : undefined,
    model: branchResult.model,
  };
}