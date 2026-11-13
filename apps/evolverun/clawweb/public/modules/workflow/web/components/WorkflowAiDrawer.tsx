import { useCallback, useEffect, useId, useLayoutEffect, useRef } from 'react'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import type { WorkflowAiDraft, WorkflowAiMode } from '../services/workflow-ai-draft'
import { useWorkflowAiDraft } from '../hooks/useWorkflowAiDraft'
import WorkflowAiDraftPreview from './WorkflowAiDraftPreview'

interface WorkflowAiDrawerProps {
  open: boolean
  mode: WorkflowAiMode
  spec: WorkflowSpec | null
  blockedReason?: string
  onClose: () => void
  onApply: (draft: WorkflowAiDraft) => void
}

const examples = {
  create: ['在现有流程中增加风险复核能力，并说明接入位置', '为失败路径增加重试和告警，保留其他节点'],
  modify: ['调整现有人工确认节点的超时配置，并说明影响', '检查现有节点依赖，按我的要求调整执行顺序'],
}
const secondaryButton = 'rounded-md border border-input bg-background px-3 py-1.5 text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

/** Nonmodal editor sibling: it never obscures or traps focus in the canvas. */
export default function WorkflowAiDrawer({ open, mode, spec, blockedReason, onClose, onApply }: WorkflowAiDrawerProps) {
  const ai = useWorkflowAiDraft(open, mode, spec)
  const titleId = useId()
  const promptId = useId()
  const helpId = useId()
  const disclosureId = useId()
  const blockedId = useId()
  const errorId = useId()
  const textarea = useRef<HTMLTextAreaElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)

  useLayoutEffect(() => {
    if (!open) return
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    textarea.current?.focus()
    return () => {
      if (returnFocus.current?.isConnected) returnFocus.current.focus()
    }
  }, [open])

  const close = useCallback(() => {
    ai.cancel()
    onClose()
    if (returnFocus.current?.isConnected) returnFocus.current.focus()
  }, [ai.cancel, onClose])

  useEffect(() => {
    if (!open) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing) return
      event.preventDefault()
      close()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, close])

  if (!open) return null

  return (
    <aside role="region" aria-labelledby={titleId}
      className="flex h-full min-h-0 w-[420px] max-w-[45vw] shrink-0 flex-col border-l border-input bg-background text-foreground">
      <header className="flex items-start justify-between gap-3 border-b border-input px-5 py-4">
        <div>
          <h2 id={titleId} className="text-base font-semibold"><span aria-hidden="true" className="mr-2 text-primary">✦</span>AI 工作流助手</h2>
          <p className="mt-1 text-xs text-muted-foreground">沿用智能接入生成服务</p>
        </div>
        <button type="button" aria-label="关闭 AI 助手" onClick={close} className={secondaryButton}>×</button>
      </header>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
        <section aria-label="当前上下文" className="rounded-lg border border-input bg-card p-3">
          <h3 className="text-xs font-semibold">当前工作流</h3>
          <p className="mt-2 break-all font-mono text-xs">{spec?.id ?? '未加载工作流'}</p>
          <p className="mt-1 text-xs text-muted-foreground">{spec ? `${spec.nodes.length} 个节点 · 基于当前编辑内容生成` : '请先加载工作流'}</p>
        </section>
        <div role="group" aria-label="AI 操作模式" className="flex gap-1 rounded-lg bg-muted p-1">
          {(['create', 'modify'] as const).map((item) => (
            <button key={item} type="button" aria-pressed={ai.mode === item} onClick={() => ai.changeMode(item)}
              className={`flex-1 rounded-md px-3 py-2 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${ai.mode === item ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-background'}`}>
              {item === 'create' ? '增加能力' : '修改现有流程'}
            </button>
          ))}
        </div>
        {blockedReason && (
          <p id={blockedId} role="status" className="rounded-md border border-input bg-muted p-3 text-xs leading-5">
            {blockedReason}
          </p>
        )}
        <form onSubmit={(event) => { event.preventDefault(); if (!blockedReason) void ai.generate() }} className="space-y-3">
          <div>
            <label htmlFor={promptId} className="block text-xs font-semibold">
              {ai.mode === 'create' ? '描述想增加的能力' : '描述对现有流程的修改'}
            </label>
            <p id={helpId} className="mt-1 text-xs leading-5 text-muted-foreground">
              {ai.mode === 'create' ? '在当前流程上增加能力，不替换整个工作流。' : '基于当前节点和配置修改，不重新创建整个工作流。'}
              示例仅填入需求，实际变更由生成服务返回。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {examples[ai.mode].map((example) => (
              <button key={example} type="button" onClick={() => { ai.changePrompt(example); textarea.current?.focus() }}
                className={`${secondaryButton} text-left text-muted-foreground`}>{example}</button>
            ))}
          </div>
          <textarea ref={textarea} id={promptId} value={ai.prompt} onChange={(event) => ai.changePrompt(event.target.value)} rows={4}
            aria-describedby={`${helpId} ${disclosureId}${ai.error ? ` ${errorId}` : ''}`} aria-invalid={Boolean(ai.error)}
            placeholder="说明目标、要保留的配置，以及允许调整的节点…"
            className="w-full resize-y rounded-lg border border-input bg-background px-3 py-2 text-xs leading-5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          <p id={disclosureId} className="rounded-md border border-input bg-muted p-3 text-xs leading-5 text-muted-foreground">
            生成时会将当前工作流的完整 YAML 和需求描述发送到已配置的智能接入生成服务。生成和应用草稿均不会自动保存或部署，需另行操作。
          </p>
          <div aria-live="polite" aria-atomic="true">
            {ai.error ? <p id={errorId} role="alert" className="text-xs text-destructive">{ai.error}</p>
              : <p className="text-xs text-muted-foreground">{ai.busy ? '正在生成草稿，可取消。' : ai.result ? '草稿已生成，请审阅变更后应用。' : '只生成待审阅草稿，不会自动保存或部署。'}</p>}
          </div>
          <button type="submit" aria-describedby={`${disclosureId}${blockedReason ? ` ${blockedId}` : ''}`} disabled={Boolean(blockedReason) || !spec || !ai.prompt.trim() || ai.busy}
            className="w-full rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground disabled:opacity-40">
            {ai.busy ? '生成中…' : ai.error ? '重试生成' : ai.stale ? '重新生成草稿' : '生成 YAML 草稿'}
          </button>
          {ai.busy && <button type="button" onClick={ai.cancel} className={`${secondaryButton} w-full`}>取消生成</button>}
        </form>
        {ai.result && (
          <WorkflowAiDraftPreview key={ai.result.requestId} draft={ai.result.draft} baseSpec={ai.result.baseSpec} stale={ai.stale} busy={ai.busy} blockedReason={blockedReason}
            onApply={(draft) => { if (!blockedReason && !ai.busy && !ai.stale) onApply(draft) }} />
        )}
      </div>
      <footer className="border-t border-input bg-card px-5 py-3 text-right">
        <button type="button" onClick={close} className={secondaryButton}>关闭</button>
      </footer>
    </aside>
  )
}
