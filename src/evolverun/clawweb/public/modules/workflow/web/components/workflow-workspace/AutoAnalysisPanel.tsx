import { useUpdateWorkflowAutoAnalysis, useWorkflowAutoAnalysis } from '../../api/hooks'

export default function AutoAnalysisPanel({ workflowId }: { workflowId: string }) {
  const setting = useWorkflowAutoAnalysis(workflowId)
  const update = useUpdateWorkflowAutoAnalysis()

  if (setting.isLoading) {
    return (
      <div className="flex items-center gap-2 py-3 text-xs text-slate-500">
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-200 border-t-blue-600" />
        正在读取自动分析配置…
      </div>
    )
  }

  if (setting.isError) {
    return (
      <div className="flex items-center justify-between rounded-lg border border-red-100 bg-red-50 px-3 py-2.5 text-xs text-red-700">
        <span>配置加载失败：{setting.error instanceof Error ? setting.error.message : String(setting.error)}</span>
        <button type="button" onClick={() => void setting.refetch()} className="font-medium hover:underline">重试</button>
      </div>
    )
  }

  const enabled = setting.data?.enabled === true
  const inherited = setting.data?.source === 'environment'

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-4 py-3.5">
      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium text-slate-900">失败后自动分析</p>
            {inherited && (
              <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700">环境默认</span>
            )}
          </div>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            工作流运行失败后，自动使用本次运行的 Bot 创建分析任务。只创建分析任务，不会自动应用建议或部署。
          </p>
          <p className="mt-1 text-[11px] text-slate-400">
            若原运行 Bot 不可用，将在该工作流已明确授权的 OpenClaw Bot 中选择可用 Bot。
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-label="失败后自动分析"
          aria-checked={enabled}
          disabled={update.isPending}
          onClick={() => update.mutate(
            { workflowId, enabled: !enabled },
            { onError: (error) => window.alert(`保存失败：${error instanceof Error ? error.message : String(error)}`) },
          )}
          className={`relative mt-0.5 inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 disabled:cursor-wait disabled:opacity-60 ${enabled ? 'bg-blue-600' : 'bg-slate-300'}`}
        >
          <span className={`h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${enabled ? 'translate-x-5.5' : 'translate-x-0.5'}`} />
        </button>
      </div>
    </div>
  )
}
