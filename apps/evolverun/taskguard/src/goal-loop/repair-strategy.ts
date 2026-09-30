/**
 * Repair strategy — generates local repair YAML fragments and global replans.
 *
 * Two repair modes:
 * - Local repair: LLM generates a YamlFragment (1-3 nodes) injected into the DAG
 * - Global replan: calls synthesize() with correctionContext to generate a new WorkflowSpec
 *
 * Escalation: if local repair fails maxLocalRepairs times, escalate to global replan.
 *
 * @module goal-loop/repair-strategy
 */

import type {
  AvailableAction,
  GoalLoopIterationRecord,
  WorkflowSpec,
  WorkflowNode,
  YamlFragment,
} from "../types.js";
import { callLlm, checkLlmAvailability } from "../llm/client.js";

// ── Public types ──

export type RepairResult =
  | { kind: "local"; fragment: YamlFragment }
  | { kind: "replan"; spec: WorkflowSpec }
  | { kind: "failed"; error: string };

export type ValidationResult = {
  valid: boolean;
  errors: string[];
};

// ── Local repair ──

/**
 * Generate a local repair YAML fragment by calling LLM.
 *
 * The LLM receives the failure reason, iteration history, and available
 * repair actions, and returns a YAML fragment with 1-3 new nodes to inject.
 */
export async function generateLocalRepair(params: {
  goal: string;
  failureReason: string;
  failedNodeId?: string;
  iterations: GoalLoopIterationRecord[];
  repairActions: AvailableAction[];
  allowDynamicActions: boolean;
  currentWorkflow: WorkflowSpec;
  model?: string;
}): Promise<RepairResult> {
  const availability = checkLlmAvailability();
  if (!availability.available) {
    return { kind: "failed", error: `LLM unavailable: ${availability.reason}` };
  }

  const systemPrompt = buildLocalRepairSystemPrompt(params.allowDynamicActions, params.repairActions);
  const userPrompt = buildLocalRepairUserPrompt(params);

  let llmResult: Awaited<ReturnType<typeof callLlm>>;
  try {
    llmResult = await callLlm({
      systemPrompt,
      userPrompt,
      model: params.model,
      temperature: 0.4,
      maxTokens: 2048,
      jsonMode: true,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { kind: "failed", error: `LLM call failed: ${msg}` };
  }

  // Parse LLM output as a YamlFragment
  const fragment = parseRepairYaml(llmResult.content);
  if (!fragment) {
    return { kind: "failed", error: "Failed to parse LLM repair output as YAML fragment" };
  }

  // Validate fragment
  const validation = validateYamlFragment(fragment, params.currentWorkflow);
  if (!validation.valid) {
    return { kind: "failed", error: `Fragment validation failed: ${validation.errors.join("; ")}` };
  }

  return { kind: "local", fragment };
}

// ── Global replan ──

/**
 * Generate a global replan by calling synthesize() with correction context.
 *
 * Returns a complete new WorkflowSpec that replaces the current one.
 */
export async function generateReplan(params: {
  goal: string;
  failedSpec: WorkflowSpec;
  failureReasons: string[];
  iterationHistory: GoalLoopIterationRecord[];
  synthesizeFn: (goal: string, correctionContext?: CorrectionContext) => Promise<{ success: boolean; spec?: WorkflowSpec; error?: string }>;
}): Promise<RepairResult> {
  const correctionContext: CorrectionContext = {
    failedSpecSummary: summarizeSpec(params.failedSpec),
    failureReasons: params.failureReasons,
    iterationSummary: params.iterationHistory.slice(-5).map((iter) => ({
      iteration: iter.iteration,
      phase: iter.phase,
      result: iter.resultSummary,
      failure: iter.failureReason,
    })),
  };

  const result = await params.synthesizeFn(params.goal, correctionContext);

  if (!result.success || !result.spec) {
    return { kind: "failed", error: result.error ?? "Replan synthesis failed" };
  }

  return { kind: "replan", spec: result.spec };
}

// ── Escalation logic ──

/**
 * Determine whether to escalate from local repair to global replan.
 */
export function shouldEscalateToReplan(
  repairAttempts: number,
  maxLocalRepairs: number,
  repairMode: "adaptive" | "local-only" | "replan-only",
): boolean {
  if (repairMode === "replan-only") return true;
  if (repairMode === "local-only") return false;
  // adaptive: escalate after maxLocalRepairs
  return repairAttempts >= maxLocalRepairs;
}

// ── Fragment validation ──

/**
 * Validate that a YamlFragment can be safely injected into the current workflow.
 */
export function validateYamlFragment(
  fragment: YamlFragment,
  currentWorkflow: WorkflowSpec,
): ValidationResult {
  const errors: string[] = [];
  const existingNodeIds = new Set(currentWorkflow.nodes.map((n) => n.id));

  // Check each node has a valid id and executor
  for (let i = 0; i < fragment.nodes.length; i++) {
    const node = fragment.nodes[i];
    if (!node.id || node.id.trim() === "") {
      errors.push(`fragment.nodes[${i}].id must not be empty`);
    }
    if (existingNodeIds.has(node.id)) {
      errors.push(`fragment.nodes[${i}].id "${node.id}" already exists in workflow`);
    }
    if (!node.executor || !node.executor.type) {
      errors.push(`fragment.nodes[${i}].executor.type is required`);
    }
    existingNodeIds.add(node.id);
  }

  // Check dependsOn references
  for (let i = 0; i < fragment.nodes.length; i++) {
    const node = fragment.nodes[i];
    if (node.dependsOn) {
      for (const dep of node.dependsOn) {
        if (!existingNodeIds.has(dep)) {
          errors.push(`fragment.nodes[${i}].dependsOn "${dep}" does not exist in workflow`);
        }
      }
    }
  }

  // Check dependsOnDeclarations
  if (fragment.dependsOnDeclarations) {
    for (const [nodeId, deps] of Object.entries(fragment.dependsOnDeclarations)) {
      if (!existingNodeIds.has(nodeId) && !fragment.nodes.some((n) => n.id === nodeId)) {
        errors.push(`dependsOnDeclarations key "${nodeId}" does not exist`);
      }
      for (const dep of deps) {
        if (!existingNodeIds.has(dep) && !fragment.nodes.some((n) => n.id === dep)) {
          errors.push(`dependsOnDeclarations["${nodeId}"] references non-existent "${dep}"`);
        }
      }
    }
  }

  return { valid: errors.length === 0, errors };
}

// ── Internal helpers ──

type CorrectionContext = {
  failedSpecSummary: string;
  failureReasons: string[];
  iterationSummary: Array<{ iteration: number; phase: string; result: string; failure?: string }>;
};

function summarizeSpec(spec: WorkflowSpec): string {
  const nodeIds = spec.nodes.map((n) => `${n.id}(${(n.executor as { type: string }).type})`).join(", ");
  return `Workflow with ${spec.nodes.length} nodes: ${nodeIds}`;
}

function buildLocalRepairSystemPrompt(allowDynamic: boolean, repairActions: AvailableAction[]): string {
  const actionList = repairActions.map((a) => `- ${a.name} (${a.type}): ${a.description ?? "no description"}`).join("\n");
  const dynamicNote = allowDynamic
    ? "You MAY also generate NEW action types not listed above, as long as they are valid executor types."
    : "You MUST ONLY use the actions listed above. Do not invent new action types.";

  return [
    "You are a workflow repair specialist. Your job is to generate a YAML fragment to fix a failed workflow step.",
    "",
    "Available repair actions:",
    actionList,
    "",
    dynamicNote,
    "",
    "You MUST respond with a JSON object containing:",
    '- "nodes": array of 1-3 new node definitions to inject into the workflow',
    '- "dependsOnDeclarations": optional object mapping nodeId to array of upstream nodeIds',
    "",
    "Each node must have: id (unique, prefixed with 'repair_'), executor (with type and params),",
    "and optionally dependsOn (array of existing or previously-injected nodeIds).",
    "The nodes should address the specific failure reason and help the workflow achieve its goal.",
  ].join("\n");
}

function buildLocalRepairUserPrompt(params: {
  goal: string;
  failureReason: string;
  failedNodeId?: string;
  iterations: GoalLoopIterationRecord[];
}): string {
  const parts: string[] = [];

  parts.push("## Goal");
  parts.push(params.goal);
  parts.push("");

  parts.push("## Failure Details");
  parts.push(`Failed node: ${params.failedNodeId ?? "unknown"}`);
  parts.push(`Failure reason: ${params.failureReason}`);
  parts.push("");

  // Recent iteration history
  const recent = params.iterations.slice(-5);
  if (recent.length > 0) {
    parts.push("## Recent Iterations");
    for (const iter of recent) {
      const status = iter.failureReason ? `FAILED: ${iter.failureReason}` : "OK";
      parts.push(`- Iter ${iter.iteration} (${iter.phase}): ${iter.resultSummary} [${status}]`);
    }
    parts.push("");
  }

  parts.push("Generate a repair fragment that addresses the failure and helps achieve the goal.");
  return parts.join("\n");
}

function parseRepairYaml(raw: string): YamlFragment | null {
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // Try extracting from markdown code block
    const jsonMatch = raw.match(/```(?:json)?\s*\n?([\s\S]*?)\n?```/);
    if (jsonMatch) {
      try {
        parsed = JSON.parse(jsonMatch[1].trim());
      } catch {
        return null;
      }
    } else {
      return null;
    }
  }

  if (!Array.isArray(parsed.nodes)) return null;

  const nodes: WorkflowNode[] = [];
  for (const rawNode of parsed.nodes) {
    if (typeof rawNode !== "object" || rawNode === null) continue;
    const node = rawNode as Record<string, unknown>;
    if (typeof node.id !== "string" || typeof node.executor !== "object") continue;
    nodes.push({
      id: node.id,
      title: typeof node.title === "string" ? node.title : node.id,
      phase: typeof node.phase === "string" ? node.phase : "main",
      dependsOn: Array.isArray(node.dependsOn) ? node.dependsOn as string[] : [],
      executor: node.executor as WorkflowNode["executor"],
    });
  }

  if (nodes.length === 0) return null;

  const fragment: YamlFragment = { nodes };
  if (parsed.dependsOnDeclarations && typeof parsed.dependsOnDeclarations === "object") {
    fragment.dependsOnDeclarations = parsed.dependsOnDeclarations as Record<string, string[]>;
  }

  return fragment;
}