/* eslint-disable react-hooks/set-state-in-effect */
/* eslint-disable react-hooks/exhaustive-deps */
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, type EvolveTask } from '../../api/client'
import { useEvolveAdminScope } from '../../features/evolve/admin-scope'
import { taskDisplayType } from '../../features/evolve/task-presentation'
import { Icon, PageTitle, Status, TaskType } from './common'
import {
  formatStepTime,
  primaryButton,
  statusView,
  taskCategoryText,
  taskDetailPath,
  taskLifecycle,
  taskStepText,
  truncateText,
  type IconName,
  type TaskCategory,
} from './helpers'

type CreateMenuItemProps = {
  icon: IconName
  title: string
  description: string
  onClick?: () => void
  emphasized?: boolean
  disabled?: boolean
  badge?: string
}

function CreateMenuGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="min-w-0 border-gray-100 px-4 first:pl-0 last:border-r-0 last:pr-0 md:border-r md:even:border-r-0 md:even:pr-0 xl:border-r xl:even:border-r xl:even:pr-4 xl:last:border-r-0 xl:last:pr-0">
      <p className="mb-3 text-sm font-semibold text-gray-900">{title}</p>
      <div className="space-y-0.5">{children}</div>
    </section>
  )
}

function CreateMenuItem({
  icon,
  title,
  description,
  onClick,
  emphasized = false,
  disabled = false,
  badge,
}: CreateMenuItemProps) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      className={`group flex w-full items-start gap-2.5 rounded-lg px-2 py-2.5 text-left transition disabled:cursor-not-allowed disabled:opacity-50 ${
        emphasized ? 'bg-blue-50/70 hover:bg-blue-50' : 'hover:bg-gray-50'
      }`}
    >
      <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center ${emphasized ? 'text-blue-600' : 'text-gray-400 group-hover:text-blue-600'}`}>
        <Icon name={icon} className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <p className={`flex items-center gap-1.5 text-sm font-medium transition ${emphasized ? 'text-blue-800' : 'text-gray-700 group-hover:text-blue-700'}`}>
          <span>{title}</span>
          {badge && <span className="rounded-full bg-blue-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase leading-none text-blue-700">{badge}</span>}
        </p>
        <p className={`mt-1 text-[11px] leading-4 ${emphasized ? 'text-blue-600' : 'text-gray-400'}`}>{description}</p>
      </div>
    </button>
  )
}

export function TaskList() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { enabled: adminMode, ownerUserId } = useEvolveAdminScope()
  const requestedStatus = searchParams.get('status')
  const [filter, setFilter] = useState<'all' | 'running' | 'success' | 'failed'>(
    requestedStatus === 'running' || requestedStatus === 'success' || requestedStatus === 'failed' ? requestedStatus : 'all',
  )
  const [category, setCategory] = useState<TaskCategory>('all')
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(1)
  const [botNameCache, setBotNameCache] = useState<Record<string, string>>({})
  const [createMenuOpen, setCreateMenuOpen] = useState(false)
  const [tasks, setTasks] = useState<EvolveTask[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  const loadTasks = async () => {
    setLoading(true)
    setLoadError('')
    try {
      const evolve = await api.evolve.listTasks({
        page,
        pageSize,
        scope: adminMode ? 'all' : 'mine',
        ownerUserId: adminMode ? ownerUserId : undefined,
        category,
        status: filter,
        query: debouncedQuery,
      })
      setTasks(evolve.tasks)
      setTotal(evolve.total)
      setTotalPages(evolve.totalPages)
      setBotNameCache((current) => {
        const next = { ...current }
        for (const task of evolve.tasks) {
          if (task.bot_name) next[`${task.user_id}:${task.bot_id}`] = task.bot_name
        }
        return next
      })
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '任务加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 300)
    return () => clearTimeout(timer)
  }, [query])

  useEffect(() => {
    setPage(1)
  }, [category, filter, debouncedQuery, adminMode, ownerUserId, pageSize])

  useEffect(() => {
    queueMicrotask(() => {
      void loadTasks()
    })
  }, [page, pageSize, category, filter, debouncedQuery, adminMode, ownerUserId])

  useEffect(() => {
    if (!createMenuOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setCreateMenuOpen(false)
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [createMenuOpen])

  return (
    <div className="w-full px-3 py-6 sm:px-4 lg:px-5">
      <p className="mb-4 text-sm font-medium text-red-600">功能体验中，有任何问题和有诉求联系@山宗</p>

      <PageTitle
        action={
          <div className="flex items-center gap-2">
            <div className="relative">
              <button className={primaryButton} onClick={() => setCreateMenuOpen((open) => !open)}>
                <Icon name="plus" />
                发起进化
                <Icon name="arrow" className={`h-3.5 w-3.5 rotate-90 transition ${createMenuOpen ? '-rotate-90' : ''}`} />
              </button>
              {createMenuOpen && (
                <>
                  <button aria-label="关闭菜单" className="fixed inset-0 z-10 cursor-default" onClick={() => setCreateMenuOpen(false)} />
                  <div
                    aria-label="进化类型选择"
                    className="absolute right-0 z-20 mt-2 max-h-[min(80vh,760px)] w-[min(calc(100vw-2rem),1180px)] overflow-y-auto rounded-2xl border border-gray-200 bg-white px-6 py-5 shadow-xl shadow-gray-900/10"
                  >
                    <div className="mb-5 border-b border-gray-100 pb-4">
                      <p className="text-base font-semibold text-gray-950">选择进化方式</p>
                      <p className="mt-1 text-xs text-gray-400">按诊断、修复、优化、部署和全流程最佳实践查看全部可用能力</p>
                    </div>
                    <div className="grid gap-y-6 md:grid-cols-2 xl:grid-cols-5">
                      <CreateMenuGroup title="诊断">
                        <CreateMenuItem icon="target" title="Bot诊断" description="生成 Goal、Spec v0 和 Bench Case" onClick={() => navigate('/evolve/new?type=diagnose')} />
                        <CreateMenuItem icon="file" title="会话诊断" description="诊断单个 Session，或导出多个 Session" onClick={() => navigate('/evolve/new?type=session_analysis')} />
                        <CreateMenuItem icon="chart" title="Bench诊断" description="单独运行一次 Bench，查看指标和评测报告" onClick={() => navigate('/evolve/new?type=bench')} />
                      </CreateMenuGroup>
                      <CreateMenuGroup title="修复">
                        <CreateMenuItem icon="code" title="Bot修复" badge="Beta" description="深度诊断、方案生成、多轮交互和执行修复" onClick={() => navigate('/evolve/new?type=repair')} />
                      </CreateMenuGroup>
                      <CreateMenuGroup title="优化">
                        <CreateMenuItem icon="spark" title="诊断后优化" description="复用已完成的诊断和规划，继续执行优化 Loop" onClick={() => navigate('/evolve/new?type=optimize')} />
                        <CreateMenuItem icon="chart" title="Bench优化" description="使用训练和测试 Domain 驱动优化" onClick={() => navigate('/evolve/new?type=bench_optimize')} />
                        <CreateMenuItem icon="target" title="治理优化" description="选择治理项，生成 Spec 并执行优化" onClick={() => navigate('/evolve/new?type=full&source=improvement')} />
                      </CreateMenuGroup>
                      <CreateMenuGroup title="应用部署">
                        <CreateMenuItem icon="package" title="创建Pack" description="为当前 Bot 环境创建可恢复快照" onClick={() => navigate('/evolve/new?type=pack')} />
                        <CreateMenuItem icon="send" title="应用Pack" description="选择已登记 Pack，创建环境恢复任务" onClick={() => navigate('/evolve/new?type=pack_restore')} />
                        <CreateMenuItem icon="target" title="任务清理" description="清理目标 Bot 草稿环境中的历史进化 Agent 与 Session" onClick={() => navigate('/evolve/new?type=runtime_cleanup')} />
                      </CreateMenuGroup>
                      <CreateMenuGroup title="全流程最佳实践">
                        <CreateMenuItem icon="send" title="Bot自进化" description="可选择先诊断再进化，或按目标直接进化" onClick={() => navigate('/evolve/new?type=full')} emphasized />
                      </CreateMenuGroup>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        }
      />

      {adminMode && (
        <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
          管理员视图：{ownerUserId ? `工号 ${ownerUserId}` : '全部工号'}。仅改变列表读取范围。
        </div>
      )}

      <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        {loadError && (
          <div className="flex items-center justify-between border-b border-red-100 bg-red-50 px-5 py-3 text-sm text-red-700">
            <span>{loadError}</span>
            <button onClick={() => void loadTasks()} className="font-medium">重试</button>
          </div>
        )}

        <div className="space-y-3 border-b border-gray-100 px-5 py-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="mr-1 text-xs font-medium text-gray-400">任务类型</span>
            {(Object.entries(taskCategoryText) as [TaskCategory, string][]).map(([value, label]) => (
              <button
                key={value}
                onClick={() => setCategory(value)}
                className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${category === value ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300 hover:text-gray-700'}`}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex rounded-lg bg-gray-100 p-1 text-xs">
              {([['all', '全部'], ['running', '进行中'], ['success', '成功'], ['failed', '失败']] as const).map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setFilter(value)}
                  className={`rounded-md px-3 py-1.5 font-medium transition ${filter === value ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                >
                  {label}
                </button>
              ))}
            </div>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索名称、备注、Bot 或任务 ID"
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500 sm:w-72"
            />
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1420px] table-fixed text-left text-sm">
            <thead className="bg-gray-50/80 text-xs font-medium text-gray-500">
              <tr>
                <th className="w-[280px] px-5 py-3">任务名称</th>
                <th className="w-[230px] px-4 py-3">备注</th>
                <th className="w-[330px] px-5 py-3">进化对象</th>
                <th className="w-[120px] px-4 py-3">任务类型</th>
                <th className="w-[90px] px-4 py-3">状态</th>
                <th className="w-[190px] px-4 py-3">开始 / 结束时间</th>
                <th className="w-[140px] px-4 py-3">当前节点 / 时长</th>
                <th className="sticky right-0 z-10 w-[90px] border-l border-gray-100 bg-gray-50 px-4 py-3 text-center shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.35)]">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {tasks.map((task) => {
                const lifecycle = taskLifecycle(task)
                const display = taskDisplayType(task)
                const status = statusView(task.status)
                return (
                  <tr key={task.task_id} className="group transition hover:bg-gray-50/70">
                    <td className="px-5 py-4">
                      <button
                        type="button"
                        onClick={() => navigate(taskDetailPath(task))}
                        className="group/name block w-full cursor-pointer rounded-lg px-1 py-1 text-left transition hover:bg-blue-50 focus:outline-none focus:ring-2 focus:ring-blue-500/20"
                        title={task.task_name || undefined}
                        aria-label={`查看任务：${task.task_name || task.task_id}`}
                      >
                        <span className="block truncate font-medium text-gray-900 transition group-hover/name:text-blue-600 group-hover/name:underline">
                          {task.task_name || `${display.label}任务`}
                        </span>
                        <span className="mt-0.5 block truncate font-mono text-[11px] text-gray-400">{task.task_id}</span>
                      </button>
                    </td>
                    <td className="max-w-[240px] px-4 py-4 text-xs leading-5 text-gray-500" title={task.remark || undefined}>
                      {task.remark ? truncateText(task.remark, 32) : '—'}
                    </td>
                    <td className="px-5 py-4">
                      <div className="flex items-center gap-3">
                        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50 text-blue-600"><Icon name="bot" /></span>
                        <div className="min-w-0">
                          <p className="truncate font-medium text-gray-900">{botNameCache[`${task.user_id}:${task.bot_id}`] || task.bot_name || '未命名 Bot'}</p>
                          <p className="mt-0.5 truncate font-mono text-[11px] text-gray-400">{task.user_id} / {task.bot_id}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-4"><TaskType type={display.key}>{display.label}</TaskType></td>
                    <td className="px-4 py-4"><Status type={status.type}>{status.text}</Status></td>
                    <td className="whitespace-nowrap px-4 py-4 text-xs text-gray-500">
                      <p><span className="text-gray-400">开始：</span>{formatStepTime(lifecycle.startedAt)}</p>
                      <p className="mt-1"><span className="text-gray-400">结束：</span>{formatStepTime(lifecycle.completedAt)}</p>
                    </td>
                    <td className="px-4 py-4">
                      <p className="truncate whitespace-nowrap font-medium text-gray-700" title={lifecycle.currentStep ? (taskStepText[lifecycle.currentStep.stepType] ?? '任务执行') : '等待执行'}>{lifecycle.currentStep ? (taskStepText[lifecycle.currentStep.stepType] ?? '任务执行') : '等待执行'}</p>
                      <p className="mt-1 whitespace-nowrap text-xs text-gray-400">{lifecycle.duration}</p>
                    </td>
                    <td className="sticky right-0 border-l border-gray-100 bg-white px-3 py-4 text-center shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.35)] group-hover:bg-gray-50">
                      <button type="button" onClick={() => navigate(taskDetailPath(task))} className="inline-flex cursor-pointer items-center justify-center whitespace-nowrap rounded-md border border-blue-100 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 transition hover:border-blue-200 hover:bg-blue-100">查看</button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {!loading && tasks.length === 0 && <div className="py-16 text-center text-sm text-gray-400">{loadError ? '任务加载失败' : '暂无进化任务'}</div>}
        {loading && <div className="py-16 text-center text-sm text-gray-400">正在加载任务…</div>}

        {!loading && total > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 px-5 py-4 text-sm text-gray-500">
            <span>共 {total} 条，第 {page} / {totalPages} 页</span>
            <div className="flex items-center gap-2">
              <select
                value={pageSize}
                onChange={(event) => setPageSize(Number(event.target.value))}
                className="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm"
              >
                <option value={10}>10 条/页</option>
                <option value={20}>20 条/页</option>
                <option value={50}>50 条/页</option>
              </select>
              <button
                disabled={page <= 1}
                onClick={() => setPage((value) => Math.max(1, value - 1))}
                className="rounded-lg border border-gray-200 px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40"
              >
                上一页
              </button>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
                className="rounded-lg border border-gray-200 px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40"
              >
                下一页
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
