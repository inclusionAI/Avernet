/**
 * Workflow validation wrapper for ClawWeb.
 *
 * Provides structured validation results (collect mode) for workflow specs,
 * returning ALL errors at once instead of throwing on the first error.
 * Used by the save endpoint and the public /validate API.
 *
 * Based on the local workflow.ts normalization + collect-mode semantic checks.
 */

import { parse as parseYaml } from "yaml";
import {
  normalizeWorkflowSpec,
  type WorkflowSpec,
} from "../workflow.js";

// ── Types ──

export interface ValidationIssue {
  path: string;
  message: string;
  severity: "error" | "warning";
}

export interface ValidationResult {
  valid: boolean;
  issues: ValidationIssue[];
  normalizedSpec: WorkflowSpec | null;
}

// ── Collect-mode Entry Points ──

/**
 * Validate a raw workflow spec object (already parsed from JSON).
 *
 * Uses "collect" mode to gather ALL errors without throwing,
 * ideal for API responses.
 */
export function validateSpec(raw: unknown): ValidationResult {
  const issues: ValidationIssue[] = [];
  let normalizedSpec: WorkflowSpec | null = null;

  // L1: Structural validation (try normalize, which throws on first structural error)
  try {
    normalizedSpec = normalizeWorkflowSpec(raw);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    issues.push({ path: "", message, severity: "error" });
  }

  // L2: Semantic validation (only if L1 passed — collect all issues)
  if (normalizedSpec && issues.length === 0) {
    const semanticIssues = collectSemanticIssues(normalizedSpec);
    issues.push(...semanticIssues);
  }

  // Invalidate normalizedSpec if any issues found
  if (issues.length > 0) {
    normalizedSpec = null;
  }

  return {
    valid: issues.length === 0,
    issues,
    normalizedSpec,
  };
}

/**
 * Parse a YAML string and validate the resulting workflow spec.
 *
 * Returns a ValidationResult. YAML parse errors are reported
 * as validation issues (path: "", severity: "error").
 */
export function validateYaml(yamlString: string): ValidationResult {
  let parsed: unknown;
  try {
    parsed = parseYaml(yamlString);
  } catch (err) {
    const message = err instanceof Error
      ? `YAML parse error: ${err.message}`
      : `YAML parse error: ${String(err)}`;
    return {
      valid: false,
      issues: [{ path: "", message, severity: "error" }],
      normalizedSpec: null,
    };
  }
  return validateSpec(parsed);
}

// ── Collect-mode Semantic Validation ──

/**
 * Collect ALL semantic issues without throwing.
 * Checks: empty nodes, duplicate IDs, missing dependsOn, DAG cycles.
 */
function collectSemanticIssues(spec: WorkflowSpec): ValidationIssue[] {
  const issues: ValidationIssue[] = [];

  // Empty nodes
  if (spec.nodes.length === 0) {
    issues.push({ path: "nodes", message: "workflow must have at least one node", severity: "error" });
    return issues; // No point checking further
  }

  // Duplicate node IDs
  const seenIds = new Set<string>();
  for (const node of spec.nodes) {
    if (seenIds.has(node.id)) {
      issues.push({ path: `nodes[${node.id}]`, message: "duplicate node id", severity: "error" });
    }
    seenIds.add(node.id);
  }

  // Missing dependsOn references
  const nodeIds = new Set(spec.nodes.map((n) => n.id));
  for (const node of spec.nodes) {
    if (node.dependsOn) {
      for (const dep of node.dependsOn) {
        if (!nodeIds.has(dep)) {
          issues.push({
            path: `nodes[${node.id}].dependsOn`,
            message: `dependency "${dep}" is missing`,
            severity: "error",
          });
        }
      }
    }
  }

  // DAG cycle detection
  const uniqueNodes = spec.nodes.filter((n, i, arr) => arr.findIndex((x) => x.id === n.id) === i);
  const byId = new Map(uniqueNodes.map((n) => [n.id, n]));
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const stack: string[] = [];
  let cycle: string[] | undefined;

  function visit(nodeId: string): void {
    if (cycle || visited.has(nodeId)) return;
    if (visiting.has(nodeId)) {
      cycle = [...stack.slice(stack.indexOf(nodeId)), nodeId];
      return;
    }
    const node = byId.get(nodeId);
    if (!node) return;
    visiting.add(nodeId);
    stack.push(nodeId);
    for (const dep of node.dependsOn ?? []) {
      if (byId.has(dep)) visit(dep);
    }
    stack.pop();
    visiting.delete(nodeId);
    visited.add(nodeId);
  }

  for (const node of uniqueNodes) {
    visit(node.id);
    if (cycle) break;
  }

  if (cycle) {
    issues.push({
      path: "nodes",
      message: `cycle detected: ${cycle.join(" -> ")}`,
      severity: "error",
    });
  }

  return issues;
}