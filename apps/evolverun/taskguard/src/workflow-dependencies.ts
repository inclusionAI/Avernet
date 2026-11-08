import { createHash } from "node:crypto";
import type { PinnedWorkflowDependency, SubworkflowExecutor, WorkflowNode, WorkflowSpec } from "./types.js";

export class DependencyLockError extends Error {
  readonly code = "DEPENDENCY_LOCK_INVALID";
  constructor(message: string) {
    super(`DEPENDENCY_LOCK_INVALID: ${message}`);
    this.name = "DependencyLockError";
  }
}
function invalid(message: string): never { throw new DependencyLockError(message); }
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}
function nonempty(value: unknown): value is string { return typeof value === "string" && !!value.trim(); }

/** Stable JSON: lexical object keys, ordered arrays, omitted optional object fields.
 * No transport defaults, UI stripping, mtime, or toJSON hooks participate in a release digest.
 */
export function canonicalWorkflowJson(value: unknown): string {
  const active = new Set<object>();
  function encode(value: unknown): string {
    if (value === null) return "null";
    if (typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
    if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
    if (typeof value !== "object" || (!Array.isArray(value) && !record(value))) {
      return invalid("snapshot must contain only JSON values");
    }
    if (active.has(value)) invalid("cyclic snapshot object");
    active.add(value);
    let result: string;
    if (Array.isArray(value)) {
      const items: string[] = [];
      for (let index = 0; index < value.length; index++) items.push(encode(value[index]));
      result = `[${items.join(",")}]`;
    } else {
      result = `{${Object.keys(value).sort().filter(key => value[key] !== undefined)
        .map(key => `${JSON.stringify(key)}:${encode(value[key])}`).join(",")}}`;
    }
    active.delete(value);
    return result;
  }
  return encode(value);
}

export function digestWorkflowSpec(spec: WorkflowSpec): string {
  return `sha256:${createHash("sha256").update(canonicalWorkflowJson(spec), "utf8").digest("hex")}`;
}

/** Includes loop bodies and dynamic-template definitions, not only top-level nodes. */
export function collectSubworkflowExecutors(workflow: WorkflowSpec): Array<{ executor: SubworkflowExecutor; path: string }> {
  const result: Array<{ executor: SubworkflowExecutor; path: string }> = [];
  function visit(nodes: WorkflowNode[], prefix: string): void {
    if (!Array.isArray(nodes)) invalid(`${prefix} must be an array`);
    for (const node of nodes) {
      if (!record(node) || !record(node.executor)) invalid(`${prefix} contains an invalid node`);
      const path = `${prefix}[${node.id}].executor`;
      if (node.executor.type === "subworkflow") result.push({ executor: node.executor as SubworkflowExecutor, path });
      if (node.executor.type === "loop-group") visit(node.executor.body as WorkflowNode[], `${path}.body`);
    }
  }
  visit(workflow.nodes, "nodes");
  if (workflow.nodeTemplates !== undefined) {
    if (!record(workflow.nodeTemplates)) invalid("nodeTemplates must be an object");
    for (const [name, template] of Object.entries(workflow.nodeTemplates)) {
      if (!record(template)) invalid(`nodeTemplates.${name} must be an object`);
      visit(template.body, `nodeTemplates.${name}.body`);
    }
  }
  return result;
}

function checkSpec(raw: unknown, workflowId: string): asserts raw is WorkflowSpec {
  if (!record(raw) || raw.id !== workflowId || !nonempty(raw.title) || !Array.isArray(raw.nodes)) {
    invalid(`snapshot identity/shape does not match workflow "${workflowId}"`);
  }
}

/** A release snapshot is a self-contained tree. Each locked child owns its direct
 * dependency locks, including old single-subworkflow calls inside that snapshot.
 * The workflow-id ancestry check also catches cycles across distinct JSON objects.
 */
export function validateWorkflowDependencies(workflow: WorkflowSpec): void {
  const identities = new Map<string, string>();
  function visit(spec: WorkflowSpec, ancestry: string[], lockAll: boolean): void {
    const refs = collectSubworkflowExecutors(spec).filter(({ executor }) =>
      lockAll || executor.version === "pinned" || executor.fanout !== undefined);
    const dependencies = spec.subworkflowDependencies;
    if (dependencies !== undefined && !record(dependencies)) invalid(`${spec.id}.subworkflowDependencies must be an object`);
    for (const { executor } of refs) {
      if (!dependencies || !Object.hasOwn(dependencies, executor.workflowId)) {
        invalid(`missing explicit published dependency "${executor.workflowId}" in "${spec.id}"`);
      }
    }
    for (const [key, raw] of Object.entries(dependencies ?? {})) {
      if (!nonempty(key) || !record(raw) || raw.workflowId !== key
        || !Number.isSafeInteger(raw.publishedVersion) || raw.publishedVersion < 1
        || !nonempty(raw.releaseId) || typeof raw.digest !== "string" || !/^sha256:[a-f0-9]{64}$/.test(raw.digest)) {
        invalid(`invalid release identity for "${key}"`);
      }
      checkSpec(raw.spec, key);
      if (raw.spec.version !== undefined && raw.spec.version !== raw.publishedVersion) invalid(`published version mismatch for "${key}"`);
      if (ancestry.includes(key)) invalid(`dependency cycle: ${[...ancestry, key].join(" -> ")}`);
      if (digestWorkflowSpec(raw.spec) !== raw.digest) invalid(`digest mismatch for "${key}"`);
      const identity = canonicalWorkflowJson([raw.publishedVersion, raw.releaseId, raw.digest]);
      if (identities.has(key) && identities.get(key) !== identity) invalid(`conflicting release identities for "${key}"`);
      identities.set(key, identity);
      for (const { executor } of refs.filter(ref => ref.executor.workflowId === key)) {
        if (executor.packId && raw.spec.packId !== executor.packId) invalid(`pack identity mismatch for "${key}"`);
      }
      visit(raw.spec, [...ancestry, key], true);
    }
  }
  checkSpec(workflow, workflow.id);
  // Validate serializability before recursion so cyclic objects fail with a domain error.
  if (workflow.subworkflowDependencies !== undefined) canonicalWorkflowJson(workflow.subworkflowDependencies);
  visit(workflow, [workflow.id], false);
}

/** Copy once at parent launch; persistence/retry consumes this value, never a resolver. */
export function snapshotWorkflowDependencies(workflow: WorkflowSpec): Record<string, PinnedWorkflowDependency> | undefined {
  validateWorkflowDependencies(workflow);
  return workflow.subworkflowDependencies === undefined ? undefined
    : JSON.parse(canonicalWorkflowJson(workflow.subworkflowDependencies));
}

/** Runtime lookup deliberately has no current-workflow/history/filesystem fallback. */
export function resolvePinnedWorkflowDependency(
  dependencies: Record<string, PinnedWorkflowDependency> | undefined, workflowId: string,
): PinnedWorkflowDependency {
  if (!record(dependencies) || !Object.hasOwn(dependencies, workflowId)) invalid(`missing explicit published dependency "${workflowId}"`);
  // Validate the complete retained closure, not only the requested digest.
  validateWorkflowDependencies({ id: "", title: "dependency snapshot", nodes: [], subworkflowDependencies: dependencies });
  return JSON.parse(canonicalWorkflowJson(dependencies[workflowId]));
}
