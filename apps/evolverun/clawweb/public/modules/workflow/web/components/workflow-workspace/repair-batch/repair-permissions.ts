import type { RepairTaskDetail } from '../../../../server/contracts/repair-workbench'

export function allowsRepairOptimization(access: { canEdit: boolean; canOptimize?: boolean } | null | undefined, canEdit: boolean): boolean {
  // Legacy responses require both the existing workflow edit grant and the repair edit grant.
  return access?.canOptimize === undefined ? canEdit && access?.canEdit === true : access.canOptimize === true
}

export function isAutomaticRepair(detail: RepairTaskDetail): boolean {
  return detail.source === 'automatic' || Boolean(detail.latestAttempt.input?.automation || detail.latestSuccessful?.input?.automation)
    || detail.revisions.some(revision => Boolean(revision.input?.automation))
}
