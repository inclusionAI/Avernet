import { useQuery } from '@tanstack/react-query'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { dashboardApi, isDemoMode } from '@avernet/workflow/web/pages/Dashboard/api'
import { WorkflowFullTable } from '@avernet/workflow/web/pages/Dashboard/components/WorkflowFullTable'
import { FULL_SORT_LABELS, type FullSortKey } from '@avernet/workflow/web/pages/Dashboard/components/workflow-full-table-config'

const WORKFLOW_HEALTH_WINDOW_END_SEC = Math.floor(Date.now() / 1000)

export default function WorkflowHealthPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const DEMO = isDemoMode()
  const nowSec = WORKFLOW_HEALTH_WINDOW_END_SEC
  const from = nowSec - 30 * 86400
  const to = nowSec

  const q = useQuery({
    queryKey: ['workflow-health', from, to],
    queryFn: () => dashboardApi.workflowHealth(from, to),
  })
  const releaseStatsQ = useQuery({
    queryKey: ['dashboard', 'workflow-release-stats', from, to],
    queryFn: () => dashboardApi.workflowReleaseStats(from, to),
  })

  // 兼容链接参数:track=released/draft 映射状态筛选;sort 映射全景表排序
  const rawSort = params.get('sort') ?? 'completionAsc'
  const sort = (rawSort in FULL_SORT_LABELS ? rawSort : 'completionAsc') as FullSortKey
  const track = params.get('track') ?? ''
  const initialStatus = track === 'released' ? 'released' as const : track === 'draft' ? 'notReleased' as const : ''

  // 后端查询失败/库不可用时 available:false → 占位兜底,避免空表
  if (!q.isLoading && q.data?.available === false) {
    return (
      <div className="mx-auto max-w-screen-2xl px-4 py-6 sm:px-6 lg:px-8">
        <button onClick={() => navigate('/')} className="mb-1 text-xs text-gray-500 hover:text-gray-800">← 返回大盘</button>
        <h1 className="text-2xl font-bold text-gray-900">工作流列表</h1>
        <div className="mt-10 rounded-xl border border-dashed border-gray-200 bg-gray-50/60 px-6 py-10 text-center text-sm text-gray-500">
          工作流列表暂不可用(后端返回空,可能库未配置或查询失败)。
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-screen-2xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <button onClick={() => navigate('/')} className="mb-1 text-xs text-gray-500 hover:text-gray-800">← 返回大盘</button>
          <h1 className="text-2xl font-bold text-gray-900">工作流列表</h1>
          <p className="mt-1 text-sm text-gray-500">
            运行与发布口径均为近 30 天 · 研发周期/最近部署为全周期 · 当前排序:{FULL_SORT_LABELS[sort].replace(/ \(.*\)/, '')} · 点行进入单工作流详情
          </p>
        </div>
        {DEMO && <span className="rounded-md bg-violet-50 px-2 py-1 text-xs font-medium text-violet-700 ring-1 ring-violet-200">目标形态预览·示例数据</span>}
      </div>

      <div className="rounded-xl bg-white p-5 shadow-sm">
        <WorkflowFullTable
          healthRows={q.data?.workflows ?? []}
          releaseRows={releaseStatsQ.data?.available ? releaseStatsQ.data.workflows : null}
          isLoading={q.isLoading || releaseStatsQ.isLoading}
          initialSort={sort}
          initialStatus={initialStatus}
        />
      </div>
    </div>
  )
}
