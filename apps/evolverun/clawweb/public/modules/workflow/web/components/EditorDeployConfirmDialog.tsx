import type { EditorState } from '../editor/store'
import type { BaselineComparison } from '../editor/workflowBaselineComparison'
import {type DeployReview, default as DeploySnapshotReview} from './DeploySnapshotReview'
import type { ActiveHistoryChoice, ActiveHistoryEvidence } from './ActiveHistoryMismatchReview'
import ActiveHistoryMismatchReview from './ActiveHistoryMismatchReview'

export type EditorDeployCandidate = {
  workflowId: string; packId: string; commit: string; expectedActiveCommit: string; activeRef: string | null
  changes: DeployReview[]; draftToken: string; source: 'draft' | 'current'; sessionId: number; fileCount: number
  packRevision: number; writeEpoch: number; freshBaseline: boolean
  positions: EditorState['positions']; editorSpec: EditorState['spec']; editorPending: EditorState['pendingEdits']
  baselineComparison: BaselineComparison; activeHistoryMismatch: boolean
}

export default function EditorDeployConfirmDialog({ candidate, activeHistory, deployNumber, hasUnsavedEdits, reviewed, onReviewedChange, onCancel, onChooseActiveHistory, onConfirm }: {
  candidate: EditorDeployCandidate
  activeHistory: { evidence: ActiveHistoryEvidence | null; choice: ActiveHistoryChoice }
  deployNumber: number | null
  hasUnsavedEdits: boolean
  reviewed: boolean
  onReviewedChange: (reviewed: boolean) => void
  onCancel: () => void
  onChooseActiveHistory: (choice: ActiveHistoryChoice) => void
  onConfirm: () => void
}) {
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-3 sm:p-6">
    <div className="max-h-[calc(100dvh-1.5rem)] w-full max-w-2xl overflow-y-auto rounded-lg bg-card p-6 shadow-xl sm:max-h-[calc(100dvh-3rem)]">
      <h3 className="text-lg font-semibold text-gray-900">部署工作流</h3>
      <p className="mt-3 text-sm text-gray-600">即将部署以下已保存的草稿(wf)完整快照（含工作流 YAML、脚本与其他文件），不会使用未保存的编辑器修改。</p>
      <p className="mt-2 break-all text-xs text-gray-600">Pack：{candidate.packId}<br />保存版本 commit：{candidate.commit}<br />当前生效 tag：{candidate.activeRef ?? '不存在（首次部署）'}<br />已核对生产 commit：{candidate.expectedActiveCommit || '不存在（首次部署）'}<br />版本文件：{candidate.fileCount} 个</p>
      {candidate.baselineComparison === 'different' && <p role="status" className="mt-3 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning-foreground">已保存 Git 工作流与数据库草稿不同；当前运行时可能读取数据库草稿，服务端会拒绝此部署。请先保存当前内容，再重新部署。保存可能立即影响使用数据库草稿的运行端。</p>}
      {candidate.activeHistoryMismatch && <ActiveHistoryMismatchReview evidence={activeHistory.evidence} deployNumber={deployNumber} selected={activeHistory.choice} onChoose={onChooseActiveHistory} />}
      {hasUnsavedEdits && <p role="status" className="mt-3 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning-foreground">当前存在未保存的修改，本次部署不包含这些修改；如需部署它们，请先保存。</p>}
      <p className="mt-2 text-xs text-gray-500">部署不会用数据库改写 YAML；未保存的编辑不会部署。Git 与数据库写入不是跨存储原子操作，结果不确定时须人工核对。</p>
      <DeploySnapshotReview changes={candidate.changes} reviewed={reviewed} onReviewedChange={onReviewedChange} />
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onCancel} className="rounded-md border border-gray-300 px-4 py-1.5 text-sm text-gray-700 hover:bg-gray-50">取消</button>
        <button disabled={!reviewed} onClick={onConfirm} className="rounded-md bg-emerald-600 px-4 py-1.5 text-sm font-medium text-primary-foreground hover:bg-emerald-700">确认部署</button>
      </div>
    </div>
  </div>
}
