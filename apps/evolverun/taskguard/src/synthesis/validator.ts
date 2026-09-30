/**
 * Three-stage validation pipeline for synthesized YAML.
 *
 * Stages:
 * 1. Schema — structural correctness via normalizeWorkflowSpec()
 * 2. Semantic — DAG topology and reference integrity
 * 3. Security — sandbox policy enforcement
 *
 * Earlier stage failures short-circuit later stages.
 * Warnings do not cause overall validation failure.
 */
import type { SynthesisConfig, ValidationError, WorkflowSpec } from "../types.js";
import { normalizeWorkflowSpec } from "../validation/workflow.js";
import { checkWorkflowSecurity } from "./sandbox-policy.js";

// ── Public types ──

export type ValidationResult = {
  valid: boolean;
  errors: ValidationError[];
  warnings: ValidationError[];
};

// ── Public API ──

/**
 * Validate a synthesized WorkflowSpec through the three-stage pipeline.
 *
 * Short-circuits: if an earlier stage produces errors, later stages are skipped.
 * Warnings do not cause validation to fail.
 */
export function validateSynthesizedYaml(
  raw: unknown,
  config: SynthesisConfig,
): ValidationResult {
  const allErrors: ValidationError[] = [];
  const allWarnings: ValidationError[] = [];

  // ── Stage 1: Schema validation ──
  const schemaResult = validateSchema(raw);
  for (const e of schemaResult) {
    if (e.severity === "warning") {
      allWarnings.push(e);
    } else {
      allErrors.push(e);
    }
  }

  // Short-circuit if schema errors found
  if (allErrors.length > 0) {
    return { valid: false, errors: allErrors, warnings: allWarnings };
  }

  // At this point raw should be a valid WorkflowSpec
  const spec = raw as WorkflowSpec;

  // ── Stage 2: Semantic validation ──
  const semanticResult = validateSemantic(spec);
  for (const e of semanticResult) {
    if (e.severity === "warning") {
      allWarnings.push(e);
    } else {
      allErrors.push(e);
    }
  }

  // Short-circuit if semantic errors found
  if (allErrors.length > 0) {
    return { valid: false, errors: allErrors, warnings: allWarnings };
  }

  // ── Stage 3: Security validation ──
  const securityResult = validateSecurity(spec, config);
  for (const e of securityResult) {
    if (e.severity === "warning") {
      allWarnings.push(e);
    } else {
      allErrors.push(e);
    }
  }

  return {
    valid: allErrors.length === 0,
    errors: allErrors,
    warnings: allWarnings,
  };
}

// ── Stage 1: Schema ──

function validateSchema(raw: unknown): ValidationError[] {
  try {
    // normalizeWorkflowSpec mutates the object and throws on fatal issues
    normalizeWorkflowSpec(raw as WorkflowSpec);
    return [];
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);

    // Try to extract a path from the error message
    const path = extractPathFromErrorMessage(message);

    return [
      {
        stage: "schema",
        path,
        message,
        suggestion: suggestSchemaFix(path, message),
      },
    ];
  }
}

// ── Stage 2: Semantic ──

function validateSemantic(spec: WorkflowSpec): ValidationError[] {
  const errors: ValidationError[] = [];
  const nodes = spec.nodes ?? [];

  // 2a. Node ID uniqueness
  const seenIds = new Map<string, number>();
  for (let i = 0; i < nodes.length; i++) {
    const id = nodes[i].id;
    const count = (seenIds.get(id) ?? 0) + 1;
    seenIds.set(id, count);
    if (count > 1) {
      errors.push({
        stage: "semantic",
        path: `nodes[${i}].id`,
        message: `Duplicate node ID '${id}'`,
        suggestion: "Node IDs must be unique within the workflow",
      });
    }
  }

  // 2b. dependsOn reference integrity
  const nodeIds = new Set(nodes.map((n) => n.id));
  for (let i = 0; i < nodes.length; i++) {
    const dependsOn = nodes[i].dependsOn ?? [];
    for (const depId of dependsOn) {
      if (!nodeIds.has(depId)) {
        errors.push({
          stage: "semantic",
          path: `nodes[${i}].dependsOn`,
          message: `References undefined node '${depId}'`,
          suggestion: `Did you mean one of: ${closestMatch(depId, nodeIds)}?`,
        });
      }
    }
  }

  // 2c. DAG acyclicity (topological sort)
  const cycleError = detectCycle(nodes);
  if (cycleError) {
    errors.push({
      stage: "semantic",
      path: "nodes",
      message: cycleError,
      suggestion: "Remove circular dependencies to form a valid DAG",
    });
  }

  // 2d. nodeTemplate reference integrity (dynamic-template nodes)
  const templateNames = new Set(Object.keys(spec.nodeTemplates ?? {}));
  for (let i = 0; i < nodes.length; i++) {
    const exec = nodes[i].executor as Record<string, unknown> | undefined;
    if (exec?.type === "dynamic-template" && typeof exec.template === "string") {
      if (!templateNames.has(exec.template)) {
        errors.push({
          stage: "semantic",
          path: `nodes[${i}].executor.template`,
          message: `References undefined nodeTemplate '${exec.template}'`,
          suggestion: `Available templates: ${[...templateNames].join(", ") || "(none defined)"}`,
        });
      }
    }
  }

  // 2e. Orphan detection (warning only)
  const dependedUpon = new Set<string>();
  for (const node of nodes) {
    for (const dep of node.dependsOn ?? []) {
      dependedUpon.add(dep);
    }
  }
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];
    const hasDeps = (node.dependsOn ?? []).length > 0;
    const isDependedUpon = dependedUpon.has(node.id);
    if (!hasDeps && !isDependedUpon && nodes.length > 1) {
      errors.push({
        stage: "semantic",
        path: `nodes[${i}]`,
        message: `Node '${node.id}' is unreachable (orphan)`,
        severity: "warning",
      });
    }
  }

  return errors;
}

// ── Stage 3: Security ──

function validateSecurity(spec: WorkflowSpec, config: SynthesisConfig): ValidationError[] {
  return checkWorkflowSecurity(spec, config);
}

// ── Helpers ──

/** Extract a dot-notation path from a typical normalizer error message. */
function extractPathFromErrorMessage(message: string): string {
  // Pattern: "nodes[2].dependsOn: ..." or ""version": ..."
  const match = message.match(/["']?(\w[\w.[\]]*)["']?\s*[:]/);
  return match ? match[1] : "";
}

/** Suggest a fix for common schema errors. */
function suggestSchemaFix(path: string, message: string): string {
  if (message.includes("required") || message.includes("Required")) {
    if (path === "version") return "Add 'version: 1' at the top level";
    if (path === "nodes") return "Add a 'nodes' array with at least one node";
    if (path === "id") return "Add an 'id' field in kebab-case";
  }
  if (message.includes("must be an array")) {
    return `Ensure '${path}' is an array`;
  }
  if (message.includes("must be a non-empty string")) {
    return `Provide a non-empty string for '${path}'`;
  }
  return "";
}

/** Find the closest matching node ID for a typo suggestion. */
function closestMatch(target: string, candidates: Set<string>): string {
  let best = "";
  let bestDist = Infinity;
  for (const c of candidates) {
    const dist = levenshtein(target, c);
    if (dist < bestDist) {
      bestDist = dist;
      best = c;
    }
  }
  return best;
}

/** Simple Levenshtein distance for typo suggestions. */
function levenshtein(a: string, b: string): number {
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0) as number[]);
  for (let i = 0; i <= m; i++) dp[i][0] = i;
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] =
        a[i - 1] === b[j - 1]
          ? dp[i - 1][j - 1]
          : 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
    }
  }
  return dp[m][n];
}

/** Detect cycles in the DAG using DFS. Returns a description of the cycle or null. */
function detectCycle(
  nodes: Array<{ id: string; dependsOn?: string[] }>,
): string | null {
  const adj = new Map<string, string[]>();
  for (const node of nodes) {
    adj.set(node.id, node.dependsOn ?? []);
  }

  const WHITE = 0;
  const GRAY = 1;
  const BLACK = 2;
  const color = new Map<string, number>();
  for (const node of nodes) {
    color.set(node.id, WHITE);
  }

  function dfs(nodeId: string, path: string[]): string | null {
    color.set(nodeId, GRAY);
    path.push(nodeId);

    const deps = adj.get(nodeId) ?? [];
    for (const dep of deps) {
      const depColor = color.get(dep) ?? WHITE;
      if (depColor === GRAY) {
        // Found cycle — reconstruct from path
        const cycleStart = path.indexOf(dep);
        const cyclePath = [...path.slice(cycleStart), dep];
        return `Dependency cycle detected: ${cyclePath.join(" → ")}`;
      }
      if (depColor === WHITE) {
        const result = dfs(dep, path);
        if (result) return result;
      }
    }

    path.pop();
    color.set(nodeId, BLACK);
    return null;
  }

  for (const node of nodes) {
    if (color.get(node.id) === WHITE) {
      const result = dfs(node.id, []);
      if (result) return result;
    }
  }

  return null;
}