import type { DeployHistoryDetailRow } from "../db/repositories/types.js";
import type { PinnedWorkflowDependency, WorkflowSpec } from "../types.js";
import { normalizeWorkflowSpec } from "../validation/workflow.js";
import {
  canonicalWorkflowJson, collectSubworkflowExecutors, DependencyLockError, digestWorkflowSpec,
  resolvePinnedWorkflowDependency, snapshotWorkflowDependencies,
} from "../workflow-dependencies.js";

/** Explicit history selection only. The old repository cannot prove which release
 * is currently active, so getLatestDeploy/saved specs are not publication sources.
 * A tagged deploy record supplies its version even when specJson omits version.
 */
export function dependencyFromPublishedHistory(workflowId: string, row: DeployHistoryDetailRow): PinnedWorkflowDependency {
  if (!row || row.action !== "deploy" || !Number.isSafeInteger(row.version) || row.version < 1
    || !Number.isSafeInteger(row.deployNumber) || row.deployNumber < 1
    || row.tagName !== `deploy/${workflowId}/#${row.deployNumber}`) {
    throw new DependencyLockError(`"${workflowId}" requires an explicitly selected published deploy record, not saved/edit history`);
  }
  let spec: WorkflowSpec;
  try { spec = JSON.parse(row.specJson); }
  catch { throw new DependencyLockError(`invalid published JSON for "${workflowId}"`); }
  const dependency: PinnedWorkflowDependency = {
    workflowId, publishedVersion: row.version, releaseId: row.tagName, digest: digestWorkflowSpec(spec), spec,
  };
  return resolvePinnedWorkflowDependency({ [workflowId]: dependency }, workflowId);
}

/** CLI publication freezes explicit, self-contained locks before its first save.
 * Drafts may omit locks; publication and runtime may not. Never infer a lock from
 * mtime, saved version, parent version, or the history list's first row.
 */
export function freezeSubworkflowDependencies<T extends Record<string, unknown>>(raw: T): T {
  const workflow = raw as unknown as WorkflowSpec;
  const hasPinned = collectSubworkflowExecutors(workflow).some(({ executor }) =>
    executor.version === "pinned" || executor.fanout !== undefined);
  if (!hasPinned && workflow.subworkflowDependencies === undefined) return raw;
  normalizeWorkflowSpec(raw); // Validate the schema, but do not rewrite hashed child snapshots.
  const dependencies = snapshotWorkflowDependencies(workflow);
  return { ...raw, subworkflowDependencies: dependencies };
}

/** Save API normalization must not downgrade this release into an unlocked call. */
export function assertDependencySnapshotPreserved(expected: Record<string, unknown>, saved: Record<string, unknown>): void {
  if (expected.subworkflowDependencies === undefined) return;
  const actual = freezeSubworkflowDependencies(saved);
  const calls = (spec: Record<string, unknown>) => collectSubworkflowExecutors(spec as unknown as WorkflowSpec)
    .filter(({ executor }) => executor.version === "pinned" || executor.fanout !== undefined)
    .map(({ path, executor }) => ({ path, executor }));
  if (canonicalWorkflowJson(expected.subworkflowDependencies) !== canonicalWorkflowJson(actual.subworkflowDependencies)
    || canonicalWorkflowJson(calls(expected)) !== canonicalWorkflowJson(calls(actual))) {
    throw new DependencyLockError("save acknowledgement changed the frozen dependencies or pinned executor contract");
  }
}

/** The legacy insert API can report success for a 409. Verify the exact stored
 * record before claiming a pinned release was published; no latest/saved fallback.
 */
export async function assertPublishedDependencyHistory(
  repository: Pick<import("../db/repositories/types.js").IDeployHistoryRepository, "findByDeployNumber">,
  input: { packId: string; workflowId: string; deployNumber: number; version: number; tagName: string; specJson: string },
): Promise<void> {
  const row = await repository.findByDeployNumber(input.packId, input.workflowId, input.deployNumber);
  if (!row || row.action !== "deploy" || row.version !== input.version || row.deployNumber !== input.deployNumber
    || row.tagName !== input.tagName) {
    throw new DependencyLockError("published history acknowledgement is missing or has a conflicting release identity");
  }
  let stored: unknown;
  try { stored = JSON.parse(row.specJson); }
  catch { throw new DependencyLockError("published history acknowledgement is not JSON"); }
  if (canonicalWorkflowJson(stored) !== canonicalWorkflowJson(JSON.parse(input.specJson))) {
    throw new DependencyLockError("published history does not contain the exact frozen release snapshot");
  }
}
