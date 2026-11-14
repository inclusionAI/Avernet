import type { WorkflowAiMode } from '../services/workflow-ai-draft'

export default function EditorAiControls({ disabled, hasPendingEdits, onOpen }: {
  disabled: boolean; hasPendingEdits: boolean; onOpen: (mode: WorkflowAiMode) => void
}) {
  return <>
    <button type="button" onClick={() => onOpen('create')} disabled={disabled}
      title={hasPendingEdits ? '请先应用或放弃未完成草稿' : '使用服务器 AutoHeal Bot / BaaS 新增工作流能力'}
      className="rounded-lg border border-primary/20 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20 disabled:opacity-40">
      ✦ AI 生成
    </button>
    <button type="button" onClick={() => onOpen('modify')} disabled={disabled}
      title={hasPendingEdits ? '请先应用或放弃未完成草稿' : '根据自然语言修改当前工作流'}
      className="rounded-lg border border-input bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-40">
      AI 修改
    </button>
  </>
}
