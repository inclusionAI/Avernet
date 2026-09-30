/**
 * LLM-Orchestrator executor — dynamically orchestrates workflow execution.
 *
 * The llm-orchestrator drives an iterative loop where an LLM selects actions
 * from an `availableActions` menu, nodes are materialized and injected into
 * the running DAG, executed, and the results fed back until:
 *
 * - The goal is met (LLM confirms)
 * - Max iterations reached
 * - Budget exhausted
 *
 * The executor returns a structured result that the Controller interprets
 * to inject nodes and manage the iteration lifecycle. The actual node
 * injection and re-execution is handled by the Controller, not this module.
 *
 * @module executors/llm-orchestrator
 */

import type {
  WorkflowNode,
  ExecutorResult,
  LlmOrchestratorExecutor,
  AvailableAction,
  InjectedNodeRecord,
  FlowBudget,
  OrchestrationRuntimeState,
  OrchestrationIterationState,
} from "../types.js";
import type { TemplateContext } from "../runner.js";
import { callLlm, checkLlmAvailability } from "../llm/client.js";
import { resolveTemplate } from "../runner.js";
import { BudgetTracker, createNoOpBudgetTracker } from "../budget/tracker.js";
import { BudgetEnforcer } from "../budget/enforcer.js";

// ── Types ──

/** LLM decision for a single orchestrator iteration. */
export type OrchestratorDecision = {
  /** The action the LLM selected from availableActions. */
  selectedAction: string;
  /** The LLM's reasoning for selecting this action. */
  reasoning: string;
  /** Whether the LLM considers the goal met after this step. */
  goalMet: boolean;
  /** Whether the LLM considers the goal definitively unachievable. */
  goalUnachievable?: boolean;
  /** Suggested params overrides for the materialized node. */
  params?: Record<string, unknown>;
};

/** Result returned by the orchestrator executor. */
export type OrchestratorExecutorResult = {
  /** The LLM's decision for this iteration. */
  decision: OrchestratorDecision;
  /** The materialized node specification to inject. */
  materializedNode: MaterializedNodeSpec;
  /** Updated orchestration runtime state. */
  orchestrationState: OrchestrationRuntimeState;
  /** The injected node record for tracking. */
  injectedNode: InjectedNodeRecord;
  /** LLM token usage. */
  llmUsage?: { promptTokens: number; completionTokens: number; totalTokens: number };
  /** LLM model used. */
  llmModel?: string;
};

/** Specification for a node to be materialized and injected into the DAG. */
export type MaterializedNodeSpec = {
  id: string;
  title: string;
  phase: string;
  dependsOn: string[];
  executor: Record<string, unknown>;
};

/** Verification result from the adversarial verifier. */
export type VerificationResult = {
  /** Whether the verifier accepts the orchestrator's result. */
  accepted: boolean;
  /** The verifier's reasoning. */
  reason: string;
  /** Number of votes for acceptance. */
  votesFor: number;
  /** Total number of voters. */
  totalVoters: number;
  /** LLM usage from verifier calls. */
  usage?: { promptTokens: number; completionTokens: number; totalTokens: number };
  /** Model used for verification. */
  model?: string;
};

// ── Node ID naming convention ──

/**
 * Generate a node ID for an injected orchestrator node.
 * Convention: `${orchestratorId}__step${stepNum}__${actionName}`
 */
export function generateInjectedNodeId(
  orchestratorId: string,
  stepNum: number,
  actionName: string,
): string {
  // Sanitize action name: replace spaces/special chars with hyphens
  const sanitized = actionName.replace(/[^a-zA-Z0-9_-]/g, "-").replace(/-+/g, "-");
  return `${orchestratorId}__step${stepNum}__${sanitized}`;
}

// ── Build iteration context ──

/**
 * Build the context for the LLM orchestrator decision prompt.
 * Includes: goal, prior iterations, budget status, available actions.
 */
export function buildIterationContext(
  goal: string,
  availableActions: AvailableAction[],
  priorIterations: OrchestrationIterationState[],
  budgetTracker: BudgetTracker,
  templateCtx: TemplateContext,
): string {
  const parts: string[] = [];

  // Goal
  parts.push("## Goal");
  parts.push(resolveTemplate(goal, templateCtx));
  parts.push("");

  // Available actions
  parts.push("## Available Actions");
  for (const action of availableActions) {
    const desc = action.description ?? `Execute ${action.type} action`;
    parts.push(`- **${action.name}** (${action.type}): ${desc}`);
  }
  parts.push("");

  // Prior iterations
  if (priorIterations.length > 0) {
    parts.push("## Previous Iterations");
    for (const iter of priorIterations) {
      const resultSummary = iter.result
        ? JSON.stringify(iter.result).slice(0, 500)
        : "(no result)";
      parts.push(`### Step ${iter.iteration}: ${iter.selectedAction} → ${iter.injectedNodeId}`);
      if (iter.llmReasoning) {
        parts.push(`Reasoning: ${iter.llmReasoning}`);
      }
      parts.push(`Result: ${resultSummary}`);
      parts.push("");
    }
  }

  // Budget status
  const budgetStatus = budgetTracker.check(Date.now() - (priorIterations.length * 30000));
  if (budgetStatus.overall !== "ok") {
    parts.push("## ⚠️ Budget Status");
    parts.push(`Overall: ${budgetStatus.overall}`);
    const consumption = budgetTracker.getConsumption();
    if (budgetStatus.tokens !== "ok") {
      const pct = budgetStatus.consumption.tokensUsed && budgetStatus.limits.maxTokens
        ? ((consumption.tokensUsed / budgetStatus.limits.maxTokens) * 100).toFixed(1)
        : "?";
      parts.push(`- tokens: ${pct}% (${budgetStatus.tokens})`);
    }
    if (budgetStatus.iterations !== "ok") {
      const pct = budgetStatus.limits.maxIterations
        ? ((consumption.iterationsUsed / budgetStatus.limits.maxIterations) * 100).toFixed(1)
        : "?";
      parts.push(`- iterations: ${pct}% (${budgetStatus.iterations})`);
    }
    if (budgetStatus.nodes !== "ok") {
      const pct = budgetStatus.limits.maxNodes
        ? ((consumption.nodesInjected / budgetStatus.limits.maxNodes) * 100).toFixed(1)
        : "?";
      parts.push(`- nodes: ${pct}% (${budgetStatus.nodes})`);
    }
    parts.push("");
  }

  return parts.join("\n");
}

// ── System prompt ──

function buildSystemPrompt(): string {
  return [
    "You are a workflow orchestrator. Your job is to iteratively select actions to achieve a goal.",
    "",
    "At each step, you must respond with a JSON object containing:",
    '- "selectedAction": string — the name of the action to execute (must match one of the available actions)',
    '- "reasoning": string — your reasoning for selecting this action',
    '- "goalMet": boolean — true if you believe the goal has been achieved after considering all prior results',
    '- "goalUnachievable": boolean — true if you believe the goal cannot be achieved with the available actions',
    '- "params": object — optional parameter overrides for the materialized node',
    "",
    "Be strategic: consider what has been tried and what information is still needed.",
    "Only set goalMet=true when the evidence clearly supports it.",
    "If you have exhausted reasonable approaches, set goalUnachievable=true.",
  ].join("\n");
}

// ── Parse LLM decision ──

/** Parse the LLM's orchestrator decision response. Exported for testing. */
export function parseOrchestratorDecision(raw: string, availableActions: AvailableAction[]): OrchestratorDecision {
  let parsed: Record<string, unknown>;

  // Try direct JSON parse
  try {
    parsed = JSON.parse(raw);
  } catch {
    // Try extracting from markdown code block
    const jsonMatch = raw.match(/```(?:json)?\s*\n?([\s\S]*?)\n?```/);
    if (jsonMatch) {
      try {
        parsed = JSON.parse(jsonMatch[1].trim());
      } catch {
        return fallbackDecisionParse(raw, availableActions);
      }
    } else {
      return fallbackDecisionParse(raw, availableActions);
    }
  }

  const selectedAction = typeof parsed.selectedAction === "string" ? parsed.selectedAction : "";
  const reasoning = typeof parsed.reasoning === "string" ? parsed.reasoning : "";
  const goalMet = typeof parsed.goalMet === "boolean" ? parsed.goalMet : false;
  const goalUnachievable = typeof parsed.goalUnachievable === "boolean" ? parsed.goalUnachievable : false;
  const params = typeof parsed.params === "object" && parsed.params !== null
    ? parsed.params as Record<string, unknown>
    : undefined;

  // Validate selectedAction against available actions
  const validAction = availableActions.some((a) => a.name === selectedAction);
  if (!validAction && availableActions.length > 0) {
    return {
      selectedAction: availableActions[0].name,
      reasoning: `LLM selected invalid action "${selectedAction}", defaulting to first available action. ${reasoning}`.trim(),
      goalMet: false,
      goalUnachievable: false,
    };
  }

  return { selectedAction, reasoning, goalMet, goalUnachievable, params };
}

/** Fallback parser for unparseable LLM responses. */
function fallbackDecisionParse(raw: string, availableActions: AvailableAction[]): OrchestratorDecision {
  // Try to find an action name in the text
  for (const action of availableActions) {
    if (raw.toLowerCase().includes(action.name.toLowerCase())) {
      return {
        selectedAction: action.name,
        reasoning: `Extracted from unstructured response: ${raw.slice(0, 300)}`,
        goalMet: false,
        goalUnachievable: false,
      };
    }
  }

  // Last resort: select first action
  const defaultAction = availableActions[0]?.name ?? "unknown";
  return {
    selectedAction: defaultAction,
    reasoning: `Failed to parse LLM decision, defaulting to "${defaultAction}": ${raw.slice(0, 200)}`,
    goalMet: false,
    goalUnachievable: availableActions.length === 0,
  };
}

// ── Materialize node from action ──

/**
 * Convert an AvailableAction and LLM decision into a WorkflowNode specification
 * for injection into the DAG.
 */
export function materializeNodeFromAction(
  orchestratorId: string,
  action: AvailableAction,
  decision: OrchestratorDecision,
  stepNum: number,
  templateCtx: TemplateContext,
): MaterializedNodeSpec {
  const nodeId = generateInjectedNodeId(orchestratorId, stepNum, action.name);

  // Build executor spec: merge action.type with action.params and decision.params
  const executor: Record<string, unknown> = {
    type: action.type,
    ...(action.params ?? {}),
    ...(decision.params ?? {}),
  };

  // Resolve any template variables in params
  for (const [key, value] of Object.entries(executor)) {
    if (typeof value === "string") {
      executor[key] = resolveTemplate(value, templateCtx);
    }
  }

  return {
    id: nodeId,
    title: `${action.name} (step ${stepNum})`,
    phase: "main",
    dependsOn: [orchestratorId],
    executor,
  };
}

// ── Adversarial verification ──

/**
 * Run adversarial verification: independent LLM calls that challenge the
 * orchestrator's conclusion. The result is accepted only if enough verifiers
 * vote to accept.
 */
export async function runAdversarialVerification(
  goal: string,
  iterations: OrchestrationIterationState[],
  verificationConfig: NonNullable<LlmOrchestratorExecutor["verification"]>,
  templateCtx: TemplateContext,
): Promise<VerificationResult> {
  const availability = checkLlmAvailability();
  if (!availability.available) {
    // LLM unavailable — skip verification, accept by default
    return {
      accepted: true,
      reason: `Verification skipped: LLM unavailable (${availability.reason})`,
      votesFor: verificationConfig.totalVoters ?? 1,
      totalVoters: verificationConfig.totalVoters ?? 1,
    };
  }

  const totalVoters = verificationConfig.totalVoters ?? 3;
  const minVotes = verificationConfig.minVotes ?? Math.ceil(totalVoters / 2);
  const model = verificationConfig.model;

  // Build verification prompt
  const resolvedPrompt = resolveTemplate(verificationConfig.prompt, templateCtx);
  const iterationsSummary = iterations
    .map((iter) => `Step ${iter.iteration}: ${iter.selectedAction} → ${JSON.stringify(iter.result ?? {}).slice(0, 300)}`)
    .join("\n");

  const userPrompt = [
    "## Goal",
    goal,
    "",
    "## Orchestrator's Execution Trace",
    iterationsSummary || "(no iterations completed)",
    "",
    "## Verification Question",
    resolvedPrompt,
    "",
    "You MUST respond with JSON:",
    '{ "accepted": boolean, "reason": string }',
  ].join("\n");

  const systemPrompt = [
    "You are an adversarial verifier. Your job is to CHALLENGE the orchestrator's conclusion.",
    "Be skeptical: only vote accepted=true if the evidence is compelling.",
    "If there is any reasonable doubt, vote accepted=false with your concerns.",
  ].join("\n");

  let votesFor = 0;
  let lastReason = "";
  let totalUsage = { promptTokens: 0, completionTokens: 0, totalTokens: 0 };
  let usedModel: string | undefined;

  // Run voters in parallel
  const voterPromises = Array.from({ length: totalVoters }, () =>
    callLlm({
      systemPrompt,
      userPrompt,
      model,
      temperature: 0.7, // Higher temperature for diverse perspectives
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
        if (parsed.accepted === true) {
          votesFor++;
        }
        lastReason = typeof parsed.reason === "string" ? parsed.reason : lastReason;
      } catch {
        // Unparseable response counts as rejection
      }
      totalUsage.promptTokens += result.value.usage?.promptTokens ?? 0;
      totalUsage.completionTokens += result.value.usage?.completionTokens ?? 0;
      totalUsage.totalTokens += result.value.usage?.totalTokens ?? 0;
      usedModel = result.value.model;
    }
  }

  return {
    accepted: votesFor >= minVotes,
    reason: lastReason,
    votesFor,
    totalVoters,
    usage: totalUsage,
    model: usedModel,
  };
}

// ── Main executor ──

/**
 * Execute one iteration of the llm-orchestrator.
 *
 * This function is called once per orchestrator iteration. It:
 * 1. Builds the iteration context (goal, prior iterations, budget)
 * 2. Calls the LLM to select an action
 * 3. Materializes the selected action into a node specification
 * 4. Returns the result for the Controller to handle injection
 *
 * The Controller is responsible for:
 * - Actually injecting the materialized node
 * - Executing the injected node
 * - Re-triggering the orchestrator for the next iteration
 */
export async function executeLlmOrchestrator(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  priorIterations: OrchestrationIterationState[] = [],
  orchestrationState?: OrchestrationRuntimeState,
): Promise<ExecutorResult> {
  const executor = node.executor as LlmOrchestratorExecutor;
  const maxIterations = executor.maxIterations ?? 10;
  const currentIteration = (orchestrationState?.currentIteration ?? priorIterations.length) + 1;

  // Check if max iterations reached (before LLM call to save tokens)
  if (currentIteration > maxIterations) {
    return {
      status: "succeeded",
      result: {
        _orchestratorComplete: true,
        _orchestratorStatus: "max-iterations-reached",
        _orchestratorReason: `Max iterations (${maxIterations}) reached`,
        _orchestrationState: {
          orchestratorId: node.id,
          status: "failed",
          currentIteration: maxIterations,
          maxIterations,
          iterations: priorIterations,
        } satisfies OrchestrationRuntimeState,
      },
    };
  }

  // Check LLM availability
  const availability = checkLlmAvailability();
  if (!availability.available) {
    return {
      status: "failed",
      error: `LLM orchestrator requires LLM but it is unavailable: ${availability.reason}`,
    };
  }

  // Set up budget tracking
  const budgetTracker = executor.budget
    ? new BudgetTracker(executor.budget)
    : createNoOpBudgetTracker();

  // Account for tokens already consumed in prior iterations
  if (orchestrationState?.budgetUsed?.maxTokens) {
    const priorTokens = priorIterations.reduce(
      (sum, iter) => sum + ((iter.result?.__llmUsage as Record<string, number>)?.totalTokens ?? 0),
      0,
    );
    budgetTracker.recordTokens(priorTokens);
  }
  for (const _iter of priorIterations) {
    budgetTracker.recordIteration();
  }

  // Check if budget is exhausted before making LLM call
  const startedAtMs = Date.now() - (priorIterations.length * 30000); // Approximate start time
  const budgetStatus = budgetTracker.check(startedAtMs);
  if (budgetStatus.overall === "exhausted") {
    return {
      status: "succeeded",
      result: {
        _orchestratorComplete: true,
        _orchestratorStatus: "budget-exhausted",
        _orchestratorReason: `Budget exhausted before iteration ${currentIteration}`,
        _orchestrationState: {
          orchestratorId: node.id,
          status: "budget-exhausted",
          currentIteration: currentIteration - 1,
          maxIterations,
          iterations: priorIterations,
        } satisfies OrchestrationRuntimeState,
      },
    };
  }

  // Build iteration context and call LLM
  const iterationContext = buildIterationContext(
    executor.goal,
    executor.availableActions,
    priorIterations,
    budgetTracker,
    templateCtx,
  );

  try {
    const llmResult = await callLlm({
      systemPrompt: buildSystemPrompt(),
      userPrompt: iterationContext,
      maxTokens: 1024,
      timeoutMs: 30000,
      jsonMode: true,
    });

    // Track token usage
    if (llmResult.usage) {
      budgetTracker.recordTokens(llmResult.usage.totalTokens);
    }
    budgetTracker.recordIteration();

    // Check budget after LLM call
    const postCallBudgetStatus = budgetTracker.check(startedAtMs);
    if (postCallBudgetStatus.overall === "exhausted") {
      return {
        status: "succeeded",
        result: {
          _orchestratorComplete: true,
          _orchestratorStatus: "budget-exhausted",
          _orchestratorReason: "Budget exhausted after LLM call",
          _orchestrationState: {
            orchestratorId: node.id,
            status: "budget-exhausted",
            currentIteration,
            maxIterations,
            iterations: priorIterations,
          } satisfies OrchestrationRuntimeState,
          _llmUsage: llmResult.usage,
          _llmModel: llmResult.model,
        },
      };
    }

    // Parse decision
    const decision = parseOrchestratorDecision(llmResult.content, executor.availableActions);

    // Find the selected action
    const selectedAction = executor.availableActions.find((a) => a.name === decision.selectedAction)
      ?? executor.availableActions[0];

    if (!selectedAction) {
      return {
        status: "failed",
        error: "No available actions to select from",
      };
    }

    // Materialize the node
    const materializedNode = materializeNodeFromAction(
      node.id,
      selectedAction,
      decision,
      currentIteration,
      templateCtx,
    );

    // Build the injected node record
    const injectedNode: InjectedNodeRecord = {
      nodeId: materializedNode.id,
      sourceNodeId: node.id,
      actionName: selectedAction.name,
      stepNum: currentIteration,
      materializedAt: Date.now(),
    };

    // Build updated orchestration state
    const updatedState: OrchestrationRuntimeState = {
      orchestratorId: node.id,
      status: decision.goalMet ? "succeeded" : "running",
      currentIteration,
      maxIterations,
      iterations: [
        ...priorIterations,
        {
          iteration: currentIteration,
          selectedAction: selectedAction.name,
          injectedNodeId: materializedNode.id,
          llmReasoning: decision.reasoning,
        } satisfies OrchestrationIterationState,
      ],
    };

    // Check if goal is met or unachievable
    if (decision.goalMet || decision.goalUnachievable) {
      const status = decision.goalMet ? "succeeded" : "failed";

      // Run adversarial verification if configured and goal is met
      let verificationResult: VerificationResult | undefined;
      if (decision.goalMet && executor.verification) {
        verificationResult = await runAdversarialVerification(
          executor.goal,
          updatedState.iterations,
          executor.verification,
          templateCtx,
        );

        if (!verificationResult.accepted) {
          // Verification rejected — continue iterating
          return {
            status: "succeeded",
            result: {
              _orchestratorComplete: false,
              _orchestratorStatus: "verification-rejected",
              _orchestratorReason: `Verification rejected (${verificationResult.votesFor}/${verificationResult.totalVoters} votes): ${verificationResult.reason}`,
              _orchestratorDecision: decision,
              _materializedNode: materializedNode,
              _injectedNode: injectedNode,
              _orchestrationState: {
                ...updatedState,
                status: "running",
              } satisfies OrchestrationRuntimeState,
              _verificationResult: verificationResult,
              _llmUsage: llmResult.usage,
              _llmModel: llmResult.model,
            },
          };
        }
      }

      return {
        status: "succeeded",
        result: {
          _orchestratorComplete: true,
          _orchestratorStatus: status,
          _orchestratorReason: decision.goalMet
            ? decision.reasoning
            : `Goal unachievable: ${decision.reasoning}`,
          _orchestrationState: {
            ...updatedState,
            status,
          } satisfies OrchestrationRuntimeState,
          ...(verificationResult ? { _verificationResult: verificationResult } : {}),
          _llmUsage: llmResult.usage,
          _llmModel: llmResult.model,
        },
      };
    }

    // Goal not yet met — return iteration result for Controller to inject node
    return {
      status: "succeeded",
      result: {
        _orchestratorComplete: false,
        _orchestratorStatus: "iterating",
        _orchestratorDecision: decision,
        _materializedNode: materializedNode,
        _injectedNode: injectedNode,
        _orchestrationState: updatedState,
        _llmUsage: llmResult.usage,
        _llmModel: llmResult.model,
      },
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      status: "failed",
      error: `LLM orchestrator call failed (iteration ${currentIteration}/${maxIterations}): ${message}`,
      rawError: err,
    };
  }
}