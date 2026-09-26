/**
 * Goal-evaluator executor — uses an LLM to assess whether a goal has been met.
 *
 * Unlike traditional executors that produce a result, the goal-evaluator acts
 * as a checkpoint: it calls an LLM to evaluate if the workflow's goal is
 * satisfied based on upstream node outputs. If the goal is not met, it can
 * trigger a rerun loop with feedback until maxAttempts is reached.
 *
 * The executor itself returns a structured `ExecutorResult` with:
 * - `result.met`: boolean indicating goal satisfaction
 * - `result.reason`: the LLM's reasoning
 * - `result.attempt`: current attempt number
 * - `result.history`: evaluation history from all attempts
 *
 * The rerun loop (onNotMet) is handled by the Controller's onResult pipeline,
 * not by this executor directly.
 *
 * @module executors/goal-evaluator
 */

import type { WorkflowNode, ExecutorResult, GoalEvaluatorExecutor } from "../types.js";
import type { TemplateContext } from "../runner.js";
import { callLlm, checkLlmAvailability } from "../llm/client.js";
import { resolveTemplate } from "../runner.js";

// ── Types ──

/** Structured evaluation result from the LLM. */
export type GoalEvaluationResult = {
  /** Whether the goal was met. */
  met: boolean;
  /** The LLM's reasoning. */
  reason: string;
  /** Token usage from the LLM call. */
  usage?: { promptTokens: number; completionTokens: number; totalTokens: number };
  /** Model used for the evaluation. */
  model?: string;
  /** Attempt number (1-based). */
  attempt: number;
};

/** Full evaluation history across multiple attempts. */
export type GoalEvaluationHistory = {
  attempts: GoalEvaluationResult[];
  /** Total tokens consumed across all attempts. */
  totalTokens: number;
  /** Final verdict. */
  finalMet: boolean;
};

// ── System prompt builder ──

function buildSystemPrompt(): string {
  return [
    "You are a goal evaluator. Your job is to determine whether a specific goal has been achieved.",
    "",
    "You MUST respond with a JSON object containing exactly:",
    '- "met": boolean — true if the goal is satisfied, false otherwise',
    '- "reason": string — your reasoning for the verdict',
    "",
    "Be strict: only return met=true when the evidence clearly supports it.",
    "If there is ambiguity or the goal is partially met, return met=false.",
    "Provide actionable feedback in your reason when met=false.",
  ].join("\n");
}

// ── User prompt builder ──

function buildUserPrompt(
  goal: string,
  templateCtx: TemplateContext,
  evaluatorPrompt: string,
  attempt: number,
  previousAttemptResults: Array<{ met: boolean; reason: string }>,
): string {
  const parts: string[] = [];

  // Goal statement
  parts.push("## Goal");
  parts.push(goal);
  parts.push("");

  // Resolved evaluator prompt (may reference {{nodeId.output}} values)
  const resolvedPrompt = resolveTemplate(evaluatorPrompt, templateCtx);
  if (resolvedPrompt && resolvedPrompt !== evaluatorPrompt) {
    parts.push("## Evaluation Context");
    parts.push(resolvedPrompt);
    parts.push("");
  }

  // Previous attempt feedback
  if (previousAttemptResults.length > 0) {
    parts.push("## Previous Attempt Feedback");
    for (let i = 0; i < previousAttemptResults.length; i++) {
      const prev = previousAttemptResults[i];
      parts.push(`### Attempt ${i + 1}: ${prev.met ? "MET" : "NOT MET"}`);
      parts.push(prev.reason);
    }
    parts.push("");
  }

  // Current attempt
  parts.push(`## Current Attempt: ${attempt}`);
  parts.push("Evaluate whether the goal has been met based on the context above.");
  parts.push('Respond with JSON: { "met": boolean, "reason": string }');

  return parts.join("\n");
}

// ── Parse LLM response ──

/** Parse an LLM evaluation response into a structured result. Exported for testing. */
export function parseEvaluationResponse(raw: string): { met: boolean; reason: string } {
  // Try direct JSON parse
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed.met === "boolean" && typeof parsed.reason === "string") {
      return { met: parsed.met, reason: parsed.reason };
    }
  } catch {
    // Not valid JSON — try to extract from markdown code block
  }

  // Try extracting JSON from ```json ... ``` block
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

  // Fallback: look for "met": true/false pattern anywhere in text
  const metMatch = raw.match(/"met"\s*:\s*(true|false)/i);
  if (metMatch) {
    const met = metMatch[1].toLowerCase() === "true";
    // Try to extract reason
    const reasonMatch = raw.match(/"reason"\s*:\s*"([^"]*)"/);
    const reason = reasonMatch ? reasonMatch[1] : raw.slice(0, 500);
    return { met, reason };
  }

  // Last resort: default to not-met with the raw response as reason
  return { met: false, reason: `Failed to parse LLM evaluation response: ${raw.slice(0, 300)}` };
}

// ── Executor ──

/**
 * Execute a goal-evaluator node.
 *
 * This calls an LLM to evaluate whether the goal is met. The result includes
 * evaluation history useful for rerun feedback.
 */
export async function executeGoalEvaluator(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  attemptNumber?: number,
): Promise<ExecutorResult> {
  const executor = node.executor as GoalEvaluatorExecutor;
  const attempt = attemptNumber ?? 1;
  const maxAttempts = executor.maxAttempts ?? 3;

  // Check LLM availability
  const availability = checkLlmAvailability();
  if (!availability.available) {
    return {
      status: "failed",
      error: `Goal evaluator requires LLM but it is unavailable: ${availability.reason}`,
    };
  }

  // Build previous attempt results from template context
  // The controller passes previous evaluation history via nodeOutput.__goalEvalHistory
  const previousAttempts: Array<{ met: boolean; reason: string }> = [];
  const historyData = templateCtx.nodeOutput?.[node.id]?.__goalEvalHistory;
  if (Array.isArray(historyData)) {
    for (const entry of historyData) {
      if (entry && typeof entry === "object" && "met" in entry && "reason" in entry) {
        previousAttempts.push({ met: Boolean(entry.met), reason: String(entry.reason) });
      }
    }
  }

  // Build prompts
  const systemPrompt = buildSystemPrompt();
  const userPrompt = buildUserPrompt(
    executor.goal,
    templateCtx,
    executor.evaluator.prompt,
    attempt,
    previousAttempts,
  );

  // Call LLM
  try {
    const result = await callLlm({
      systemPrompt,
      userPrompt,
      model: executor.evaluator.model,
      temperature: executor.evaluator.temperature ?? 0.2,
      maxTokens: 1024,
      timeoutMs: executor.evaluator.timeoutMs ?? 30000,
      jsonMode: true,
    });

    // Parse response
    const parsed = parseEvaluationResponse(result.content);

    const evaluation: GoalEvaluationResult = {
      met: parsed.met,
      reason: parsed.reason,
      usage: result.usage,
      model: result.model,
      attempt,
    };

    // Build history including this attempt
    const allAttempts = [...previousAttempts, { met: parsed.met, reason: parsed.reason }];
    const totalTokens = allAttempts.length * (result.usage?.totalTokens ?? 0);

    const history: GoalEvaluationHistory = {
      attempts: [evaluation],
      totalTokens,
      finalMet: parsed.met,
    };

    return {
      status: "succeeded",
      result: {
        met: parsed.met,
        reason: parsed.reason,
        attempt,
        maxAttempts,
        history,
        llmUsage: result.usage,
        llmModel: result.model,
      },
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      status: "failed",
      error: `Goal evaluator LLM call failed (attempt ${attempt}/${maxAttempts}): ${message}`,
      rawError: err,
    };
  }
}