/**
 * Goal-loop executor — adaptive loop fusing L2 (evaluate) + L3 (orchestrate) + L4 (synthesize).
 *
 * Each invocation executes ONE phase of the four-phase cycle and returns
 * an ExecutorResult with _goalLoopPhase indicating what the Controller should do next:
 *
 * Phase 1 (plan):       → _goalLoopPhase: "execute", _subWorkflowSpec: generated YAML
 * Phase 2 (execute):    → handled by Controller (normal node execution)
 * Phase 3 (evaluate):   → _goalLoopPhase: "complete" (if met) or "repair" (if not met)
 * Phase 4a (repair):    → _goalLoopPhase: "execute", _injectYamlFragment: fragment
 * Phase 4b (replan):    → _goalLoopPhase: "execute", _replaceWorkflowSpec: new YAML
 * Complete:             → _goalLoopPhase: "complete", _evaluation: { met, reason }
 *
 * @module executors/goal-loop
 */

import type {
  WorkflowNode,
  ExecutorResult,
  GoalLoopExecutor,
  GoalLoopRuntimeState,
  WorkflowSpec,
} from "../types.js";
import type { TemplateContext } from "../runner.js";
import { callLlm, checkLlmAvailability } from "../llm/client.js";
import { resolveTemplate } from "../runner.js";
import { checkConvergence } from "../goal-loop/convergence-detector.js";
import {
  generateLocalRepair,
  shouldEscalateToReplan,
  type RepairResult,
} from "../goal-loop/repair-strategy.js";

// ── Public API ──

/**
 * Execute one phase of the goal-loop cycle.
 *
 * Called by the Controller's dispatch switch. The Controller passes
 * the current goalLoopState (from FlowState) as priorState.
 */
export async function executeGoalLoop(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  priorState?: GoalLoopRuntimeState,
): Promise<ExecutorResult> {
  const executor = node.executor as GoalLoopExecutor;

  // Initialize state on first call
  const state: GoalLoopRuntimeState = priorState ?? initGoalLoopState(node.id, executor.goal);

  // Determine which phase to execute
  switch (state.currentPhase) {
    case "plan":
      return executePlanPhase(node, executor, state, templateCtx);
    case "evaluate":
      return executeEvaluatePhase(node, executor, state, templateCtx);
    case "repair":
      return executeRepairPhase(node, executor, state, templateCtx);
    case "execute":
      // Should not be called directly — Controller handles execution
      return {
        status: "failed",
        error: "goal-loop in 'execute' phase should be handled by Controller, not dispatched to executor",
      };
    case "complete":
      return {
        status: "succeeded",
        result: {
          _goalLoopComplete: true,
          _evaluation: state.finalEvaluation ?? { met: false, reason: "unknown" },
        },
      };
    default:
      return { status: "failed", error: `unknown goal-loop phase: ${state.currentPhase}` };
  }
}

// ── Phase implementations ──

async function executePlanPhase(
  node: WorkflowNode,
  executor: GoalLoopExecutor,
  state: GoalLoopRuntimeState,
  templateCtx: TemplateContext,
): Promise<ExecutorResult> {
  const resolvedGoal = resolveTemplate(executor.goal, templateCtx);

  // If initialPlan.type is "spec", use the provided spec
  if (executor.initialPlan?.type === "spec" && executor.initialPlan.spec) {
    state.currentPhase = "execute";
    state.lastWorkflowSpec = executor.initialPlan.spec;
    state.currentIteration = 1;
    state.iterations.push({
      iteration: 1,
      phase: "plan",
      resultSummary: "Using explicit spec from initialPlan",
    });

    return {
      status: "succeeded",
      result: {
        _goalLoopPhase: "execute",
        _subWorkflowSpec: executor.initialPlan.spec,
        _goalLoopState: state,
      },
    };
  }

  // initialPlan.type is "synthesize" — we need the Controller to call synthesize()
  // The executor signals this by returning a special result
  state.currentPhase = "execute";
  state.currentIteration = 1;
  state.iterations.push({
    iteration: 1,
    phase: "plan",
    resultSummary: "Requested LLM synthesis for initial plan",
  });

  return {
    status: "succeeded",
    result: {
      _goalLoopPhase: "plan-synthesize",
      _goalLoopGoal: resolvedGoal,
      _goalLoopHints: executor.initialPlan?.hints ?? [],
      _goalLoopState: state,
    },
  };
}

async function executeEvaluatePhase(
  node: WorkflowNode,
  executor: GoalLoopExecutor,
  state: GoalLoopRuntimeState,
  templateCtx: TemplateContext,
): Promise<ExecutorResult> {
  const availability = checkLlmAvailability();
  if (!availability.available) {
    return {
      status: "failed",
      error: `goal-loop evaluation requires LLM but it is unavailable: ${availability.reason}`,
    };
  }

  const resolvedGoal = resolveTemplate(executor.goal, templateCtx);
  const criteria = executor.evaluation.criteria.map((c) => resolveTemplate(c, templateCtx));

  // Build evaluation prompt
  const systemPrompt = buildEvaluationSystemPrompt();
  const userPrompt = buildEvaluationUserPrompt(resolvedGoal, criteria, state);

  let llmResult: Awaited<ReturnType<typeof callLlm>>;
  try {
    llmResult = await callLlm({
      systemPrompt,
      userPrompt,
      model: executor.evaluation.model,
      temperature: executor.evaluation.temperature ?? 0.2,
      maxTokens: 1024,
      jsonMode: true,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    state.iterations.push({
      iteration: state.currentIteration,
      phase: "evaluate",
      resultSummary: "LLM evaluation call failed",
      failureReason: msg,
    });
    return { status: "failed", error: `Evaluation LLM call failed: ${msg}` };
  }

  // Track token usage
  const usage = llmResult.usage ?? { promptTokens: 0, completionTokens: 0, totalTokens: 0 };
  state.budgetUsed.tokens += usage.totalTokens;

  // Parse evaluation result
  const evalResult = parseEvaluationResult(llmResult.content);

  // Record iteration
  state.iterations.push({
    iteration: state.currentIteration,
    phase: "evaluate",
    resultSummary: evalResult.reason,
    failureReason: evalResult.met ? undefined : evalResult.reason,
    tokenUsage: usage,
  });

  // Campaign: append evaluation result to evidence chain (every iteration, not just final)
  if (executor.campaignId) {
    try {
      const { onGoalLoopIteration } = await import("../campaign/campaign-hooks.js");
      await onGoalLoopIteration({
        campaignId: executor.campaignId,
        flowId: state.goalLoopNodeId,
        nodeId: state.goalLoopNodeId,
        iteration: state.currentIteration,
        met: evalResult.met,
        reason: evalResult.reason,
      });
    } catch { /* best-effort */ }
  }

  if (evalResult.met) {
    // Goal met — optionally run adversarial verification
    if (executor.verification) {
      const verified = await runVerification(executor, state, templateCtx);
      if (!verified.accepted) {
        // Verification failed — continue to repair
        evalResult.met = false;
        evalResult.reason = `Verification failed: ${verified.reason}`;
      }
    }

    if (evalResult.met) {
      state.currentPhase = "complete";
      state.finalEvaluation = evalResult;
      state.iterations.push({
        iteration: state.currentIteration,
        phase: "complete",
        resultSummary: `Goal achieved: ${evalResult.reason}`,
      });

      // Campaign: append final evaluation to evidence + update campaign status
      if (executor.campaignId) {
        try {
          const { onGoalLoopIteration, onGoalLoopConverged } = await import("../campaign/campaign-hooks.js");
          await onGoalLoopIteration({
            campaignId: executor.campaignId,
            flowId: state.goalLoopNodeId,
            nodeId: state.goalLoopNodeId,
            iteration: state.currentIteration,
            met: true,
            reason: evalResult.reason,
          });
          await onGoalLoopConverged({ campaignId: executor.campaignId, met: true, reason: evalResult.reason });
        } catch { /* best-effort */ }
      }

      return {
        status: "succeeded",
        result: {
          _goalLoopPhase: "complete",
          _evaluation: evalResult,
          _goalLoopState: state,
        },
      };
    }
  }

  // Goal not met — check convergence
  const convergence = checkConvergence(state, executor);
  state.convergenceStatus = convergence;

  if (convergence.status === "stop") {
    state.currentPhase = "complete";
    state.finalEvaluation = { met: false, reason: `Stopped: ${convergence.reason}` };

    // Campaign: append convergence stop to evidence
    if (executor.campaignId) {
      try {
        const { onGoalLoopIteration, onGoalLoopConverged } = await import("../campaign/campaign-hooks.js");
        await onGoalLoopIteration({
          campaignId: executor.campaignId,
          flowId: state.goalLoopNodeId,
          nodeId: state.goalLoopNodeId,
          iteration: state.currentIteration,
          met: false,
          reason: `Convergence stopped: ${convergence.reason}`,
        });
        await onGoalLoopConverged({ campaignId: executor.campaignId, met: false, reason: convergence.reason });
      } catch { /* best-effort */ }
    }

    return {
      status: "succeeded",
      result: {
        _goalLoopPhase: "complete",
        _evaluation: { met: false, reason: `Convergence stopped: ${convergence.reason}` },
        _goalLoopState: state,
      },
    };
  }

  // Continue to repair phase
  state.currentPhase = "repair";
  state.repairAttempts = 0;

  return {
    status: "succeeded",
    result: {
      _goalLoopPhase: "repair",
      _evaluation: evalResult,
      _goalLoopState: state,
    },
  };
}

async function executeRepairPhase(
  node: WorkflowNode,
  executor: GoalLoopExecutor,
  state: GoalLoopRuntimeState,
  templateCtx: TemplateContext,
): Promise<ExecutorResult> {
  const repairConfig = executor.repair ?? { mode: "adaptive" as const };
  const maxLocalRepairs = repairConfig.maxLocalRepairs ?? 3;

  // Check if we should escalate to replan
  const shouldReplan = shouldEscalateToReplan(
    state.repairAttempts,
    maxLocalRepairs,
    repairConfig.mode,
  );

  // Get the last failure
  const lastFailure = [...state.iterations].reverse().find((iter) => iter.failureReason);
  if (!lastFailure) {
    // No failure found — shouldn't happen, but handle gracefully
    state.currentPhase = "evaluate";
    state.currentIteration += 1;
    state.budgetUsed.iterations += 1;
    return {
      status: "succeeded",
      result: {
        _goalLoopPhase: "evaluate",
        _goalLoopState: state,
      },
    };
  }

  if (shouldReplan) {
    // Phase 4b: Global replan
    state.replans += 1;
    state.currentPhase = "execute";
    state.repairAttempts = 0;
    state.currentIteration += 1;
    state.budgetUsed.iterations += 1;

    // Collect failure reasons from history
    const failureReasons = state.iterations
      .filter((iter) => iter.failureReason)
      .slice(-5)
      .map((iter) => iter.failureReason!);

    // Signal replan to Controller — Controller will call synthesize() with correctionContext
    state.iterations.push({
      iteration: state.currentIteration,
      phase: "repair",
      resultSummary: `Triggered global replan (${state.replans} total)`,
      failureReason: lastFailure.failureReason,
      triggeredReplan: true,
    });

    return {
      status: "succeeded",
      result: {
        _goalLoopPhase: "replan",
        _replanGoal: resolveTemplate(executor.goal, templateCtx),
        _replanFailureReasons: failureReasons,
        _replanFailedSpec: state.lastWorkflowSpec,
        _goalLoopState: state,
      },
    };
  }

  // Phase 4a: Local repair
  state.repairAttempts += 1;

  // Call repair strategy
  const repairActions = repairConfig.repairActions ?? [];
  const allowDynamic = repairConfig.allowDynamicActions ?? false;
  const currentWorkflow = state.lastWorkflowSpec ?? { nodes: [], version: 1 } as WorkflowSpec;

  let repairResult: RepairResult;
  try {
    repairResult = await generateLocalRepair({
      goal: resolveTemplate(executor.goal, templateCtx),
      failureReason: lastFailure.failureReason!,
      failedNodeId: lastFailure.failedNodeId,
      iterations: state.iterations,
      repairActions,
      allowDynamicActions: allowDynamic,
      currentWorkflow,
      model: executor.evaluation.model,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    repairResult = { kind: "failed", error: msg };
  }

  if (repairResult.kind === "failed") {
    // Repair failed — will escalate on next call
    state.iterations.push({
      iteration: state.currentIteration,
      phase: "repair",
      resultSummary: `Local repair attempt ${state.repairAttempts} failed`,
      failureReason: repairResult.error,
    });

    // Stay in repair phase — next call will check escalation
    return {
      status: "succeeded",
      result: {
        _goalLoopPhase: "repair",
        _goalLoopState: state,
      },
    };
  }

  if (repairResult.kind === "local") {
    // Inject the fragment
    state.currentPhase = "execute";
    state.budgetUsed.nodes += repairResult.fragment.nodes.length;

    const injectedNodeIds = repairResult.fragment.nodes.map((n) => n.id);
    state.iterations.push({
      iteration: state.currentIteration,
      phase: "repair",
      resultSummary: `Injected ${injectedNodeIds.length} repair nodes`,
      injectedNodes: injectedNodeIds,
    });

    return {
      status: "succeeded",
      result: {
        _goalLoopPhase: "execute",
        _injectYamlFragment: repairResult.fragment,
        _goalLoopState: state,
      },
    };
  }

  // Should not reach here for local repair
  return {
    status: "failed",
    error: "Unexpected repair result type",
  };
}

// ── Verification ──

async function runVerification(
  executor: GoalLoopExecutor,
  state: GoalLoopRuntimeState,
  templateCtx: TemplateContext,
): Promise<{ accepted: boolean; reason: string }> {
  const verification = executor.verification!;
  const totalVoters = verification.totalVoters ?? 3;
  const minVotes = verification.minVotes ?? Math.ceil(totalVoters / 2);
  const model = verification.model;

  const resolvedPrompt = resolveTemplate(verification.prompt, templateCtx);
  const iterationsSummary = state.iterations
    .slice(-5)
    .map((iter) => `Iter ${iter.iteration}: ${iter.resultSummary}`)
    .join("\n");

  const systemPrompt = [
    "You are an adversarial verifier. Your job is to CHALLENGE the goal-loop's conclusion.",
    "Be skeptical: only vote accepted=true if the evidence is compelling.",
    "If there is any reasonable doubt, vote accepted=false with your concerns.",
  ].join("\n");

  const userPrompt = [
    "## Goal",
    executor.goal,
    "",
    "## Execution Trace",
    iterationsSummary || "(no iterations)",
    "",
    "## Verification Question",
    resolvedPrompt,
    "",
    'Respond with JSON: { "accepted": boolean, "reason": string }',
  ].join("\n");

  let votesFor = 0;
  let lastReason = "";

  const voterPromises = Array.from({ length: totalVoters }, () =>
    callLlm({
      systemPrompt,
      userPrompt,
      model,
      temperature: 0.7,
      maxTokens: 512,
      timeoutMs: 30000,
      jsonMode: true,
    }),
  );

  const results = await Promise.allSettled(voterPromises);
  for (const result of results) {
    if (result.status === "fulfilled") {
      try {
        const parsed = JSON.parse(result.value.content);
        if (parsed.accepted === true) votesFor++;
        lastReason = typeof parsed.reason === "string" ? parsed.reason : lastReason;
      } catch {
        // Unparseable counts as rejection
      }
      state.budgetUsed.tokens += result.value.usage?.totalTokens ?? 0;
    }
  }

  return {
    accepted: votesFor >= minVotes,
    reason: lastReason,
  };
}

// ── Helpers ──

function initGoalLoopState(nodeId: string, goal: string): GoalLoopRuntimeState {
  return {
    goalLoopNodeId: nodeId,
    goal,
    currentIteration: 0,
    currentPhase: "plan",
    iterations: [],
    replans: 0,
    repairAttempts: 0,
    convergenceStatus: { status: "continue" },
    budgetUsed: { tokens: 0, iterations: 0, nodes: 0 },
  };
}

function buildEvaluationSystemPrompt(): string {
  return [
    "You are a goal evaluator for an adaptive workflow loop. Your job is to determine whether a specific goal has been achieved.",
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

function buildEvaluationUserPrompt(
  goal: string,
  criteria: string[],
  state: GoalLoopRuntimeState,
): string {
  const parts: string[] = [];

  parts.push("## Goal");
  parts.push(goal);
  parts.push("");

  parts.push("## Evaluation Criteria");
  for (const c of criteria) {
    parts.push(`- ${c}`);
  }
  parts.push("");

  // Execution history summary
  parts.push("## Execution History");
  if (state.iterations.length === 0) {
    parts.push("(no iterations yet)");
  } else {
    const recent = state.iterations.slice(-10);
    for (const iter of recent) {
      const status = iter.failureReason ? `[FAILED: ${iter.failureReason}]` : "[OK]";
      parts.push(`- Iter ${iter.iteration} (${iter.phase}): ${iter.resultSummary} ${status}`);
    }
  }
  parts.push("");

  // Replan history
  if (state.replans > 0) {
    parts.push(`## Replans: ${state.replans}`);
    parts.push("");
  }

  parts.push("Based on the execution history above, has the goal been achieved?");
  parts.push('Respond with JSON: { "met": boolean, "reason": string }');

  return parts.join("\n");
}

function parseEvaluationResult(raw: string): { met: boolean; reason: string } {
  try {
    const parsed = JSON.parse(raw);
    return {
      met: typeof parsed.met === "boolean" ? parsed.met : false,
      reason: typeof parsed.reason === "string" ? parsed.reason : "unknown",
    };
  } catch {
    // Try markdown code block
    const jsonMatch = raw.match(/```(?:json)?\s*\n?([\s\S]*?)\n?```/);
    if (jsonMatch) {
      try {
        const parsed = JSON.parse(jsonMatch[1].trim());
        return {
          met: typeof parsed.met === "boolean" ? parsed.met : false,
          reason: typeof parsed.reason === "string" ? parsed.reason : "unknown",
        };
      } catch {
        // fall through
      }
    }
    return { met: false, reason: `Failed to parse evaluation: ${raw.slice(0, 200)}` };
  }
}