export type IconName =
  | 'spark' | 'plus' | 'bot' | 'arrow' | 'check' | 'clock' | 'file'
  | 'chart' | 'code' | 'send' | 'target' | 'package'

import { evolveTaskRegistry } from '../../features/evolve/task-registry'
import type { EvolveStep, EvolveTask } from '../../api/client'

export const taskTypeText = Object.fromEntries(
  Object.values(evolveTaskRegistry).map((definition) => [definition.type, definition.label]),
) as Record<string, string>
taskTypeText.session_analysis = '会话诊断'
taskTypeText.session_export = 'Session 导出'

export type TaskCategory = 'all' | 'diagnosis' | 'optimization' | 'repair' | 'deployment' | 'full'

export const taskCategoryText: Record<TaskCategory, string> = {
  all: '全部类型',
  diagnosis: '诊断',
  optimization: '优化',
  repair: '修复',
  deployment: '部署',
  full: '全流程最佳实践',
}

export const taskCategoryOrder: TaskCategory[] = ['all', 'diagnosis', 'optimization', 'repair', 'deployment', 'full']

export const taskStatusText: Record<string, string> = {
  pending: '待运行',
  dispatched: '运行中',
  running: '运行中',
  waiting_approval: '等待批准方案',
  waiting_acceptance: '等待确认结果',
  waiting_context: '等待补充上下文',
  completed: '已完成',
  failed: '失败',
  canceled: '已取消',
}

export const taskStepText: Record<string, string> = {
  skill_init: 'Skill 初始化',
  diagnose: 'Bot诊断',
  plan: '目标规划',
  envprep: '环境准备',
  bench: 'Bench诊断',
  optimize: '策略优化',
  apply: '结果应用',
  pack: '创建 Pack',
  pack_restore: '应用 Pack',
  session_ais: '会话诊断',
  repair_plan: '生成修复方案',
  repair_apply: '执行与验证',
}

export function statusView(status: string): { type: 'running' | 'waiting' | 'done' | 'scheduled'; text: string } {
  if (status === 'completed' || status === 'succeeded') return { type: 'done', text: taskStatusText[status] ?? '已完成' }
  if (status === 'running' || status === 'accepted') return { type: 'running', text: taskStatusText[status] ?? '运行中' }
  if (status === 'dispatched') return { type: 'running', text: '运行中' }
  if (['waiting_approval', 'waiting_acceptance', 'waiting_context'].includes(status)) return { type: 'waiting', text: taskStatusText[status] }
  if (status === 'failed' || status === 'canceled') return { type: 'waiting', text: taskStatusText[status] ?? (status === 'failed' ? '失败' : '已取消') }
  return { type: 'scheduled', text: taskStatusText[status] ?? status }
}

export function truncateText(value: string, maxLength: number): string {
  return value.length > maxLength ? `${value.slice(0, maxLength)}…` : value
}

export function timestampMs(value: number | string | null | undefined): number | null {
  if (value == null) return null
  const timestamp = typeof value === 'number'
    ? (value < 10_000_000_000 ? value * 1000 : value)
    : new Date(value).getTime()
  return Number.isFinite(timestamp) ? timestamp : null
}

export function taskLifecycle(task: EvolveTask): {
  currentStep: EvolveStep | null
  startedAt: number | string | null
  completedAt: number | string | null
  duration: string
} {
  const steps = task.steps ?? []
  const currentStep = [...steps].reverse().find((step) =>
    ['created', 'pending', 'accepted', 'dispatched', 'running'].includes(step.status),
  ) ?? steps.at(-1) ?? null
  const starts = steps.flatMap((step) => {
    const value = timestampMs(step.startedAt)
    return step.startedAt != null && value != null ? [{ raw: step.startedAt, value }] : []
  })
  const completions = steps.flatMap((step) => {
    const value = timestampMs(step.completedAt)
    return step.completedAt != null && value != null ? [{ raw: step.completedAt, value }] : []
  })
  const start = starts.length ? starts.reduce((earliest, entry) => entry.value < earliest.value ? entry : earliest) : null
  const terminal = ['completed', 'succeeded', 'failed', 'canceled'].includes(task.status)
  const end = terminal && completions.length
    ? completions.reduce((latest, entry) => entry.value > latest.value ? entry : latest)
    : null
  if (!start) return { currentStep, startedAt: null, completedAt: end?.raw ?? null, duration: '等待启动' }
  const seconds = Math.max(0, Math.floor(((end?.value ?? Date.now()) - start.value) / 1000))
  const duration = seconds < 60
    ? `${seconds} 秒`
    : seconds < 3600
      ? `${Math.floor(seconds / 60)} 分钟`
      : `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分钟`
  return { currentStep, startedAt: start.raw, completedAt: end?.raw ?? null, duration }
}

export function taskDetailPath(task: EvolveTask): string {
  if (task.task_type === 'repair') return `/evolve/repair-runs/${task.task_id}`
  return task.task_type === 'session_analysis' || task.task_type === 'session_export'
    ? `/evolve/session-runs/${task.task_id}`
    : `/evolve/runs/${task.task_id}`
}

export function formatStepTime(value: number | string | null | undefined): string {
  if (value == null) return '—'
  const date = typeof value === 'number'
    ? new Date(value < 10_000_000_000 ? value * 1000 : value)
    : new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}

export function stepDuration(step: EvolveStep): string {
  const start = step.startedAt ?? (typeof step.gmtCreate === 'number' ? step.gmtCreate : Math.floor(new Date(step.gmtCreate).getTime() / 1000))
  if (!start || Number.isNaN(start)) return '等待启动'
  const end = step.completedAt ?? Math.floor(Date.now() / 1000)
  const seconds = Math.max(0, end - start)
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

export const taskStatusFilters: Array<{ key: 'all' | 'running' | 'success' | 'failed'; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'running', label: '进行中' },
  { key: 'success', label: '成功' },
  { key: 'failed', label: '失败' },
]

export const primaryButton =
  'inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-blue-700'

export const secondaryButton =
  'inline-flex items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition hover:bg-gray-50'

export const inputClass =
  'w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10'
