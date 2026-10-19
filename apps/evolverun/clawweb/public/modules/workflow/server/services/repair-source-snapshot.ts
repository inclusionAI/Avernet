import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { WorkflowSnapshotRepository, parseWorkflowSnapshotBinding } from '@avernet/clawweb-shared/server/services/workflow-snapshot';
import { workflowSnapshotKey, type WorkflowSnapshotBinding } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';

/** Legacy analyses are trusted only when their original proposal digest proves the run configuration. */
export async function repairSourceSnapshot(db: IDatabase, workflowId: string, flowIds: string[], proposal: Record<string, unknown> | null, raw?: unknown, runs?: Map<string, WorkflowSnapshotBinding>): Promise<WorkflowSnapshotBinding> {
  const stored = parseWorkflowSnapshotBinding(raw === undefined ? null : JSON.stringify(raw));
  if (stored?.status === 'resolved') {
    if (stored.snapshot.workflowId !== workflowId || proposal?.baseSpecDigest && proposal.baseSpecDigest !== stored.snapshot.configDigest) return {status:'unresolved',reason:'IDENTITY_MISMATCH'};
    return stored;
  }
  if (stored?.status === 'unresolved' && stored.reason === 'IDENTITY_MISMATCH') return stored;
  if (!flowIds.length || typeof proposal?.baseSpecDigest !== 'string') return {status:'unresolved',reason:'LEGACY_MISSING'};
  const snapshots = new WorkflowSnapshotRepository(db);
  const refs = runs ? [...new Set(flowIds)].map(id => runs.get(id) ?? { status: 'unresolved' as const, reason: 'LEGACY_MISSING' as const }) : [...(await snapshots.forRuns(flowIds)).values()];
  const first=refs[0];
  if (first.status!=='resolved'||first.snapshot.workflowId!==workflowId||proposal.baseSpecDigest!==first.snapshot.configDigest
    ||refs.some(ref=>ref.status!=='resolved'||workflowSnapshotKey(ref.snapshot)!==workflowSnapshotKey(first.snapshot)||ref.status==='resolved'&&ref.snapshot.configDigest!==first.snapshot.configDigest)) return {status:'unresolved',reason:'IDENTITY_MISMATCH'};
  return first;
}
