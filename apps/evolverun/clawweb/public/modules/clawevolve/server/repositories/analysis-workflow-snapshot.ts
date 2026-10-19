import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { WorkflowSnapshotRepository, parseWorkflowSnapshotBinding } from '@avernet/clawweb-shared/server/services/workflow-snapshot';
import { workflowSnapshotKey, type WorkflowSnapshotBinding } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';

export async function bindAnalysisWorkflowSnapshot(db: IDatabase, workflowId: string | null, flowIds: string[]): Promise<WorkflowSnapshotBinding> {
  const snapshots = new WorkflowSnapshotRepository(db);
  const refs = [...(await snapshots.forRuns(flowIds)).values()];
  if (!refs.length || refs.some(ref => ref.status !== 'resolved')) return {status:'unresolved',reason:'LEGACY_MISSING'};
  const first = refs[0];
  if (first.status !== 'resolved' || first.snapshot.workflowId !== workflowId
    || refs.some(ref => ref.status !== 'resolved' || workflowSnapshotKey(ref.snapshot) !== workflowSnapshotKey(first.snapshot) || ref.status === 'resolved' && ref.snapshot.configDigest !== first.snapshot.configDigest)) return {status:'unresolved',reason:'IDENTITY_MISMATCH'};
  return first;
}
export function storedAnalysisWorkflowSnapshot(row: {scope_json: string; workflow_snapshot_json?: string | null}): WorkflowSnapshotBinding | null {
  const stored = parseWorkflowSnapshotBinding(row.workflow_snapshot_json);
  if (stored) return stored;
  try { return parseWorkflowSnapshotBinding(JSON.stringify(JSON.parse(row.scope_json).workflowSnapshot)); }
  catch { return null; }
}
export function analysisWorkflowSnapshot(row: {scope_json: string; workflow_snapshot_json?: string | null}): WorkflowSnapshotBinding {
  return storedAnalysisWorkflowSnapshot(row) ?? {status:'unresolved',reason:'LEGACY_MISSING'};
}
export async function hasAnalysisSnapshotColumn(db: IDatabase): Promise<boolean> {
  try { await db.query('SELECT workflow_snapshot_json FROM workflow_evolution_analysis_runs WHERE 1 = 0'); return true; }
  catch (error) {
    if (/no such column: workflow_snapshot_json|unknown column ['`]?workflow_snapshot_json/i.test(String(error))) return false;
    throw error;
  }
}
