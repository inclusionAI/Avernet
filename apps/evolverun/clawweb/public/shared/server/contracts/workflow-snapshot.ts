/** Immutable configuration identity shared by run registration, analysis and repair. */
export type WorkflowSnapshotRef =
  | { kind: 'deployment'; workflowId: string; workflowVersion: number; deployNumber: number; configDigest: string }
  | { kind: 'saved'; workflowId: string; commit: string; sourceFlowId: string; configDigest: string; repoId?: string; packDigest?: string; specPath?: string };
export type WorkflowSnapshotBinding =
  | { status: 'resolved'; snapshot: WorkflowSnapshotRef }
  | { status: 'unresolved'; reason: 'LEGACY_MISSING' | 'SNAPSHOT_MISSING' | 'IDENTITY_MISMATCH'; snapshot?: undefined };
export function workflowSnapshotKey(ref: WorkflowSnapshotRef): string {
  return JSON.stringify(ref.kind === 'deployment'
    ? ['deployment', ref.workflowId, ref.workflowVersion, ref.deployNumber]
    : ['saved', ref.workflowId, ref.repoId ?? '', ref.commit, ref.specPath ?? '']);
}
export type WorkflowSnapshotInput = { workflowSpec: Record<string, unknown>; workflowSpecDigest: string; workflowSnapshot: WorkflowSnapshotRef };
