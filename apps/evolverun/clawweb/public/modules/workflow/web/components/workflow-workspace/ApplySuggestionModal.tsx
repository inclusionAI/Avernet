import { useMemo, useState } from 'react'
import type { EvolveSuggestion, SuggestionApplyTask } from '@avernet/clawweb-shared/web/api/client'
import { useApplySuggestionsBatch, useEligibleBotsForSuggestion } from '../../api/hooks'

type ApplySuggestionModalProps = {
  suggestions: EvolveSuggestion[]
  previousTask?: SuggestionApplyTask
  onClose: () => void
  onApplied: (suggestionIds: string[]) => void
}

export default function ApplySuggestionModal({ suggestions, previousTask, onClose, onApplied }: ApplySuggestionModalProps) {
  const suggestionIds = suggestions.map((suggestion) => suggestion.id)
  const firstId = suggestionIds[0]
  const { data, isLoading, error } = useEligibleBotsForSuggestion(firstId, suggestionIds.length > 0)
  const [selectedBotId, setSelectedBotId] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [isApplying, setIsApplying] = useState(false)
  const defaultSpec = useMemo(() => suggestions.map((suggestion) => suggestion.description).join('\n'), [suggestions])
  const [applicationSpec, setApplicationSpec] = useState(previousTask?.applicationSpec?.trim() || defaultSpec)
  const applyMutation = useApplySuggestionsBatch()
  const bots = useMemo(() => data?.bots ?? [], [data?.bots])
  const effectiveSelectedBotId = selectedBotId || (
    previousTask?.botId && bots.some((bot) => bot.botId === previousTask.botId) ? previousTask.botId : ''
  )
  const selectedBot = bots.find((bot) => bot.botId === effectiveSelectedBotId)
  const isBulk = suggestionIds.length > 1
  const isRetry = suggestions.some((suggestion) => suggestion.status === 'failed')

  const handleApply = async () => {
    const spec = applicationSpec.trim()
    if (!suggestionIds.length || !effectiveSelectedBotId || !spec) return
    setIsApplying(true)
    try {
      await applyMutation.mutateAsync({ suggestionIds, botId: effectiveSelectedBotId,
        botEnv: selectedBot?.env ?? undefined, applicationSpec: spec })
      setNotice(`已派发 1 个任务处理 ${suggestionIds.length} 条建议；应用完成后仍需自然流量或人工验证效果`)
      onApplied(suggestionIds)
    } catch (err) {
      setNotice(`应用任务派发失败：${err instanceof Error ? err.message : String(err)}`)
    }
    setIsApplying(false)
  }

  if (!suggestionIds.length) return null
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
    <div role="dialog" aria-modal="true" aria-labelledby="apply-suggestion-modal-title"
      className="flex max-h-[calc(100vh-2rem)] w-full max-w-md flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-lg"
      onClick={(event) => event.stopPropagation()}>
      <div data-testid="apply-suggestion-modal-body" className="min-h-0 flex-1 overflow-y-auto p-5 pb-4">
        <h3 id="apply-suggestion-modal-title" className="mb-3 text-sm font-semibold text-gray-900">
          {isBulk ? `批量应用 ${suggestionIds.length} 条建议` : '选择 Bot 自动应用建议'}
        </h3>
        <p className="mb-3 text-xs text-amber-700">选择一个具有编辑权限的 Bot。Bot 会读取完整配置，结合建议安全修改并部署；应用完成后仍需验证实际效果。</p>
        {notice && <div className="mb-3 rounded-md bg-blue-50 px-3 py-2 text-xs text-blue-700">{notice}</div>}
        {isLoading && <div className="py-4 text-xs text-gray-500">加载可应用 Bot 中...</div>}
        {!isLoading && error && <div className="py-3 text-xs text-red-600">加载失败：{error instanceof Error ? error.message : String(error)}</div>}
        {!isLoading && bots.length === 0 && <div className="py-3 text-xs text-gray-500">没有可用的 Bot 对该 workflow 拥有编辑权限。请先在权限管理中授予 Bot 的 can_edit 权限。</div>}
        {!isLoading && bots.length > 0 && <div className="mb-4 space-y-2">{bots.map((bot) => <label key={bot.botId}
          className="flex cursor-pointer items-center gap-2 rounded-md border border-gray-200 p-2 hover:bg-gray-50">
          <input type="radio" name="apply-bot" value={bot.botId} checked={effectiveSelectedBotId === bot.botId}
            onChange={() => setSelectedBotId(bot.botId)} className="text-blue-600" />
          <span className="text-xs"><span className="block font-medium text-gray-900">{bot.botName ?? bot.botId}</span>
            <span className="block text-gray-500">{bot.botId}{bot.env ? ` · ${bot.env}` : ''}</span></span>
        </label>)}</div>}
        <label className="mb-4 block text-xs font-medium text-slate-700">本次修复要求
          <textarea aria-label="本次修复要求" value={applicationSpec} maxLength={20_000}
            onChange={(event) => setApplicationSpec(event.target.value)} rows={6}
            className="mt-1.5 w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-xs leading-5 text-slate-700 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100" />
          <span className="mt-1 block text-[10px] font-normal text-slate-400">只影响本次应用任务，不会修改原始分析和建议。</span>
        </label>
      </div>
      <div data-testid="apply-suggestion-modal-footer" className="flex shrink-0 justify-end gap-2 border-t border-slate-100 bg-white px-5 py-4">
        <button onClick={onClose} className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50">取消</button>
        <button onClick={() => void handleApply()} disabled={!effectiveSelectedBotId || !applicationSpec.trim() || isApplying}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-60">
          {isApplying ? '派发中...' : (isBulk ? `确认应用 ${suggestionIds.length} 条` : isRetry ? '重新应用' : '确认应用')}
        </button>
      </div>
    </div>
  </div>
}
