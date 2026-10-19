import { workflowSnapshotKey, type WorkflowSnapshotBinding, type WorkflowSnapshotRef } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';
import type { RepairItem } from './repair-batch.js';
export type RepairVersionScope = 'current' | 'historical' | 'all';
export function itemSnapshot(item: RepairItem): WorkflowSnapshotBinding | null {
  const raw = item.context?.sourceSnapshot as WorkflowSnapshotBinding | undefined;
  return raw?.status === 'resolved' && raw.snapshot?.workflowId && raw.snapshot.configDigest ? raw : null;
}
export function itemSnapshotKey(item: RepairItem): string | null {
  const ref=itemSnapshot(item);return ref?.status==='resolved'?workflowSnapshotKey(ref.snapshot):null;
}
export function inVersionScope(item: RepairItem, active: WorkflowSnapshotBinding | null | undefined, scope: RepairVersionScope): boolean {
  if(scope==='all'||active===undefined)return true;
  const current=active?.status==='resolved'&&itemSnapshotKey(item)===workflowSnapshotKey(active.snapshot);
  return scope==='current'?!!current:!current;
}
export function snapshotLabel(ref: WorkflowSnapshotRef): string {
  return ref.kind==='deployment'?`工作流运行版本 ${ref.workflowVersion} · 部署 ${ref.deployNumber}`:`保存测试 ${ref.commit.slice(0,8)}`;
}

/** Historical goals are read-only provenance in this task, never reservations on the source item. */
export function historicalReference(item: RepairItem): boolean { return item.context?.referenceOnly === true; }
