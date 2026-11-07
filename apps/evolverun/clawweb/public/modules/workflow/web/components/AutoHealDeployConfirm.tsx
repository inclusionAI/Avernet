import { useEffect, useState } from 'react'
import type { AutoHealSaveResult } from '@avernet/clawweb-shared/web/types'
import { autoHealUnifiedSaveIdentity } from '../api/autoHealWrites'
import { readAutoHealDeploySnapshot, type AutoHealDeploySnapshot } from '../api/autoHealDeploySnapshot'
import DeploySnapshotReview from './DeploySnapshotReview'

export default function AutoHealDeployConfirm({ receipt, onCancel, onConfirm }: {
  receipt: AutoHealSaveResult; onCancel: () => void; onConfirm: (snapshot?: AutoHealDeploySnapshot) => void
}) {
  const unified = !!autoHealUnifiedSaveIdentity(receipt)
  const [snapshot, setSnapshot] = useState<AutoHealDeploySnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reviewed, setReviewed] = useState(false)
  useEffect(() => {
    if (!unified) return
    let alive = true
    void readAutoHealDeploySnapshot(receipt).then(value => { if (alive) setSnapshot(value) }, reason => {
      if (alive) setError(reason instanceof Error ? reason.message : '读取部署基线失败；部署未发送。')
    })
    return () => { alive = false }
  }, [receipt, unified])
  return <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/30 p-6">
    <section role="dialog" aria-modal="true" aria-labelledby="autoheal-deploy-title" className="w-full max-w-lg rounded-lg bg-card p-6 shadow-xl">
      <h3 id="autoheal-deploy-title" className="text-lg font-semibold">{unified ? '确认部署已保存快照' : '确认兼容部署'}</h3>
      {unified ? <>
        {error ? <p role="alert" className="mt-3 text-sm">{error}</p> : !snapshot ? <p className="mt-3 text-sm">正在读取并核对部署基线…</p> : <>
          <p className="mt-3 break-all text-xs">保存版本 commit：{snapshot.commit}<br />当前生效 tag：{snapshot.activeRef ?? '不存在（首次部署）'}<br />已核对生产 commit：{snapshot.expectedActiveCommit || '不存在（首次部署）'}</p>
          <DeploySnapshotReview changes={snapshot.changes} reviewed={reviewed} onReviewedChange={setReviewed} />
        </>}
      </> : <>
        <p className="mt-3 text-sm">旧部署接口将部署服务端此刻读取的草稿，不保证等于本次修复；其他客户端可能已修改草稿。自动运行可能验证不同内容。</p>
        <p className="mt-2 text-sm text-muted-foreground">本页的本地状态检查不能证明其他客户端没有修改，请确认已了解此风险。</p>
      </>}
      <div className="mt-5 flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="rounded-lg border border-input px-4 py-2">取消</button>
        <button type="button" disabled={unified && (!snapshot || !reviewed)} onClick={() => onConfirm(snapshot ?? undefined)} className="rounded-lg bg-primary px-4 py-2 text-primary-foreground">{unified ? '确认部署' : '了解风险，确认部署'}</button>
      </div>
    </section>
  </div>
}
