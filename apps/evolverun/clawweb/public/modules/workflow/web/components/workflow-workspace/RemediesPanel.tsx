import { useEvolveLessons } from '../../api/hooks'

const REMEDY_STATUS: Record<string, { label: string; cls: string }> = {
  draft: { label: '草稿', cls: 'bg-gray-100 text-gray-600' },
  verified: { label: '已验证', cls: 'bg-blue-50 text-blue-700' },
  published: { label: '已上线', cls: 'bg-emerald-50 text-emerald-700' },
  retired: { label: '已失效', cls: 'bg-gray-100 text-gray-400' },
}

export const REMEDY_KIND: Record<string, string> = {
  kb_hint: '提示',
  prompt_patch: '提示词补丁',
  arg_template_fix: '参数模板修正',
  node_patch: '节点结构补丁',
  alert: '告警',
  'adjust-timeout': '超时调整',
  'retry-as-is': '直接重试',
  'skip-retry': '跳过重试',
}

export default function RemediesPanel({ workflowId }: { workflowId: string }) {
  const { data, isLoading, isError, refetch } = useEvolveLessons({ workflowId, limit: 100 })
  const lessons = data?.lessons ?? []

  if (isLoading) return <div className="p-4 text-xs text-gray-500">加载经验库...</div>
  if (isError) return <div role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-700">经验加载失败，暂时无法确认是否有可复用经验。
    <button type="button" className="ml-2 underline" onClick={() => void refetch()}>重试经验</button></div>

  return <div className="space-y-3">
    <div><p className="text-xs text-slate-500">经验是经过复用边界审核的知识，不由建议应用自动生成。</p></div>
    {lessons.length === 0 ? <div className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
      <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-slate-100 text-lg text-slate-500">◇</div>
      <h4 className="mt-3 text-sm font-medium text-slate-900">还没有可复用经验</h4>
      <p className="mx-auto mt-1 max-w-lg text-xs leading-5 text-slate-500">当同类问题多次出现、修复边界明确且效果经过人工审核后，才适合沉淀为经验。</p>
      <div className="mt-4 flex flex-wrap justify-center gap-2 text-[11px] text-slate-500">
        <span className="rounded-full bg-slate-100 px-2.5 py-1">多次命中</span><span className="rounded-full bg-slate-100 px-2.5 py-1">边界明确</span><span className="rounded-full bg-slate-100 px-2.5 py-1">人工审核</span>
      </div>
    </div> : <div className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
      {lessons.map((lesson) => <div key={lesson.lesson_id} className="flex items-start justify-between gap-5 px-4 py-3.5">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-slate-900">{REMEDY_KIND[lesson.fix_kind] ?? lesson.fix_kind}</span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${REMEDY_STATUS[lesson.status]?.cls ?? REMEDY_STATUS.draft.cls}`}>{REMEDY_STATUS[lesson.status]?.label ?? lesson.status}</span>
            <span className="text-[11px] text-slate-400">{lesson.workflow_id === workflowId ? '当前工作流' : '全局经验'}</span>
          </div>
          <p className="mt-1.5 truncate font-mono text-[11px] text-slate-500" title={lesson.failure_signature}>{lesson.failure_signature}</p>
          <p className="mt-1 text-[11px] text-slate-400">
            {lesson.source === 'retry_healing' ? '自愈重试' : lesson.source === 'manual' ? '手动录入' : lesson.source === 'evolve_optimize' ? '进化优化' : '日志分析'}
            {' · '}{String(lesson.gmt_create).slice(0, 10)}{' · '}{lesson.lesson_id}
          </p>
        </div>
        <div className="grid shrink-0 grid-cols-2 gap-5 text-right">
          <div><p className="text-sm font-semibold tabular-nums text-slate-900">{lesson.hit_count} / {lesson.rescued_count}</p><p className="text-[10px] text-slate-400">命中 / 救回</p></div>
          <div><p className="text-sm font-semibold tabular-nums text-slate-900">{lesson.hit_count > 0 ? `${Math.round((lesson.successRate ?? 0) * 100)}%` : '—'}</p><p className="text-[10px] text-slate-400">成功率</p></div>
        </div>
      </div>)}
    </div>}
  </div>
}
