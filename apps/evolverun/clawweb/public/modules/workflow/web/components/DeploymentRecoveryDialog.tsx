import { useState } from 'react'

export interface DeploymentRecoveryDialogProps {
  workflowId: string
  onCancel: () => void
  onConfirm: () => void
  errorMessage?: string | null
}

export default function DeploymentRecoveryDialog({ workflowId, onCancel, onConfirm, errorMessage }: DeploymentRecoveryDialogProps) {
  const [confirmed, setConfirmed] = useState(false)
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/30 p-6">
      <section role="dialog" aria-modal="true" aria-labelledby="deployment-recovery-title" className="w-full max-w-lg rounded-lg bg-card p-6 shadow-xl">
        <h3 id="deployment-recovery-title" className="text-lg font-semibold">清除浏览器核对标记</h3>
        <p className="mt-2 text-sm text-muted-foreground">
          仅会清除当前工作流 <span className="break-all font-mono">{workflowId}</span> 的浏览器标记，不会清除其他工作流，也不会改动 Git 或数据库。
        </p>
        <p className="mt-2 text-sm font-medium text-destructive">
          请先人工核对 Git 和数据库，确认本次部署的最终状态。失败或结果未知时不能自动重试。
        </p>
        {errorMessage && <p role="alert" className="mt-3 text-sm text-destructive">{errorMessage}</p>}
        <label className="mt-4 flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            className="mt-1 h-4 w-4"
          />
          <span>我已人工核对 Git 和数据库，并确认当前工作流的部署结果已处理完成。</span>
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="rounded-lg border border-input px-4 py-2 text-sm">
            取消
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={!confirmed}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-40"
          >
            清除当前工作流标记
          </button>
        </div>
      </section>
    </div>
  )
}
